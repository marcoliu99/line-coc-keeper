"""Discord adapter — the discord.py-based front-end.

Game/command logic lives in app/commands.py; this module only translates
Discord events into calls against that shared layer. Run it as its own process
(`python -m app.discord_bot`); discord.py owns a persistent gateway connection,
so there is no webhook URL or ngrok tunnel.
"""
from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path
import re
import unicodedata

import discord

from app import help_service, locks, scenario_library
from app.commands import router as command_router
from app.legacy_commands import (
    Reply, SendImage,
    handle_check_command, handle_luck_decision, resolve_pdf_upload_choice,
    handle_pdf_upload, handle_map_upload, handle_role_sheet_upload,
    handle_scenario_compare_upload, handle_unsupported_message
)
from app.config import DISCORD_BOT_TOKEN
from app.config import BACKUP_INTERVAL_MINUTES
from app import db
from app.models import GroupState
from app.help_registry import HelpAction, HelpPage
from app.repositories.group_state import StateRevisionConflict, load_state as load_group_state

_logger = logging.getLogger(__name__)
_backup_task: asyncio.Task | None = None

MAX_DISCORD_MESSAGE_CHARS = 1900  # Discord's hard limit is 2000; leave a margin
MAX_REPLY_MESSAGES = 10

intents = discord.Intents.default()
intents.message_content = True  # privileged intent — must also be switched on
# for this bot under Developer Portal > your app > Bot > Privileged Gateway Intents,
# or on_message will only ever see empty message content.
client = discord.Client(intents=intents)


def _is_ooc_message(text: str) -> bool:
    ooc_text = unicodedata.normalize("NFKC", text or "").lstrip()
    return ooc_text.startswith("@") or ooc_text.startswith("<@")


def _chunk_text(text: str) -> list[str]:
    text = text.strip() or "（沒有內容）"
    chunks = [text[i : i + MAX_DISCORD_MESSAGE_CHARS] for i in range(0, len(text), MAX_DISCORD_MESSAGE_CHARS)]
    return chunks[:MAX_REPLY_MESSAGES]


def _make_reply(channel: discord.abc.Messageable) -> Reply:
    async def reply(text: str) -> None:
        for chunk in _chunk_text(text):
            await channel.send(chunk)

    return reply


def _conversation_id(channel_id: int) -> str:
    return f"discord-channel-{channel_id}"


def _is_keeper_member(member: discord.abc.User) -> bool:
    """Allow the configured Discord Keeper role to manage host-only state."""
    roles = getattr(member, "roles", ())
    return any(getattr(role, "name", "").casefold() == "keeper" for role in roles)


async def _send_dm(owner_id: str, text: str) -> None:
    # owner_id is str(discord.Member.id), as stored on Character.owner_id. Raises
    # if the user has DMs from server members disabled; commands.py swallows
    # that (see its docstring on why it doesn't fall back to posting publicly).
    user = client.get_user(int(owner_id)) or await client.fetch_user(int(owner_id))
    for chunk in _chunk_text(text):
        await user.send(chunk)


def _make_send_image(channel: discord.abc.Messageable) -> SendImage:
    async def send_image(png_bytes: bytes, conversation_id: str, page_number: int) -> None:
        # conversation_id/page_number are retained in the shared callback
        # signature for state-aware image sends; Discord attaches bytes directly.
        await channel.send(file=discord.File(io.BytesIO(png_bytes), filename=f"page_{page_number}.png"))

    return send_image


async def _send_dm_image(owner_id: str, png_bytes: bytes, conversation_id: str, page_number: int) -> None:
    user = client.get_user(int(owner_id)) or await client.fetch_user(int(owner_id))
    await user.send(file=discord.File(io.BytesIO(png_bytes), filename=f"page_{page_number}.png"))


def _make_interaction_reply(interaction: discord.Interaction) -> Reply:
    # Used only after the initial interaction response has been consumed
    # (defer/edit_message), so the actual send has to go through followup.
    async def reply(text: str) -> None:
        for chunk in _chunk_text(text):
            await interaction.followup.send(chunk)

    return reply


def _check_button_specs(check: dict) -> list[tuple[str, bool, str]]:
    """Returns (label, danger, option) triples — however many buttons this
    pending check needs. A plain skill/sanity check needs exactly one
    (option="", meaning "just /coc check", no option name to pass); a
    "choice" check (see keeper.py's offer_check_choice — e.g. 閃避 vs 反擊)
    needs one button per option, each resolving to "/coc check <該選項>"."""
    if check.get("type") == "sanity":
        return [("🎲 理智檢定", True, "")]
    if check.get("type") == "choice":
        return [
            (f"🎲 {o['label']}（{o['skill']} {o['skill_value']}%）", False, o["label"])
            for o in check.get("options", [])
        ]
    return [(f"🎲 {check.get('skill', '')}（{check.get('skill_value', 0)}%）", False, "")]


# The trailing option segment can be empty (plain check) or a Chinese option
# label (e.g. "閃避") from offer_check_choice — [^:]* rather than \w+ so it
# isn't restricted to ASCII word characters.
_CHECK_BUTTON_ID_TEMPLATE = r"coc_check:(?P<conversation_id>discord-channel-\d+):(?P<owner_id>\d+):(?P<option>[^:]*)"


class CheckButton(discord.ui.DynamicItem[discord.ui.Button], template=_CHECK_BUTTON_ID_TEMPLATE):  # type: ignore[call-arg]
    """A "🎲 roll" button under the Keeper's message whenever it asks for a
    check — see app/keeper.py's skill_check/sanity_check/offer_check_choice
    tools, which now only *register* a pending check (GroupState.
    pending_checks) instead of secretly rolling for the player. Clicking this
    runs exactly what typing "/coc check" (or "/coc check <option>" for a
    choice) would — see app/commands.py's handle_check_command.

    Registered as a *dynamic* item (client.add_dynamic_items below, matched by
    the custom_id pattern above) rather than a plain per-message View, so it
    keeps working across bot restarts — this project restarts the Discord
    process after nearly every deploy, and a plain View() only lives in this
    process's memory, so a button clicked after a restart would otherwise
    silently fail ("This interaction failed") even though nothing about the
    game state was actually lost.
    """

    def __init__(self, conversation_id: str, owner_id: str, label: str, danger: bool = False, option: str = "") -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.danger if danger else discord.ButtonStyle.primary,
                custom_id=f"coc_check:{conversation_id}:{owner_id}:{option}",
            )
        )
        self.conversation_id = conversation_id
        self.owner_id = owner_id
        self.option = option

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        danger = item.style == discord.ButtonStyle.danger
        return cls(match["conversation_id"], match["owner_id"], item.label or "🎲 擲骰", danger, match["option"])

    async def callback(self, interaction: discord.Interaction) -> None:
        if str(interaction.user.id) != self.owner_id:
            await interaction.response.send_message("這不是你的檢定，換你自己的角色來按。", ephemeral=True)
            return
        if not locks.try_acquire_check(self.conversation_id, self.owner_id):
            # A slow Keeper call from a first click (or an earlier /coc check)
            # is still in flight — reject outright rather than letting a
            # second click queue behind get_conversation_lock and run as a
            # genuinely separate, duplicate roll once its turn comes.
            await interaction.response.send_message("上一次的檢定還在處理中，請稍等結果出來，不要重複點擊。", ephemeral=True)
            return
        try:
            await interaction.response.edit_message(view=None)
            reply = _make_interaction_reply(interaction)
            send_image = _make_send_image(interaction.channel)
            command_text = f"/coc check {self.option}" if self.option else "/coc check"
            state_before = await asyncio.to_thread(load_group_state, self.conversation_id)
            before_pending = dict(state_before.pending_checks)
            before_luck_pending = dict(state_before.pending_luck_decisions)
            try:
                await handle_check_command(
                    self.conversation_id, self.owner_id, reply, _send_dm, send_image, _send_dm_image,
                    command_text, split_roll_feedback=True, acquire_legacy_for_keeper=True
                )
            finally:
                # Always attempt this, even if handle_check_command raised
                # partway through — see app/discord_bot.py's on_message for
                # why (a check can already be registered/saved before a later
                # failure in the same turn).
                await _post_pending_buttons(interaction.channel, self.conversation_id, before_pending, before_luck_pending)
        finally:
            locks.release_check(self.conversation_id, self.owner_id)


async def _post_check_buttons(
    channel: discord.abc.Messageable, conversation_id: str, state: GroupState, before_pending: dict
) -> None:
    """Posts a roll button (or, for a "choice" check, one button per option —
    e.g. 閃避／反擊ーー in the same message) for every pending check that's
    new or changed since `before_pending` was snapshotted. Content-diffed,
    not just key-diffed, so a replaced check for the same player still gets
    fresh buttons; a stale/duplicate pending entry never gets re-posted.
    Takes an already-loaded `state` (see _post_pending_buttons) rather than
    loading its own — every caller needs this same post-turn state for both
    this and _post_luck_buttons, so there's no reason to read it twice."""
    for owner_id, check in state.pending_checks.items():
        if before_pending.get(owner_id) == check:
            continue
        try:
            char = state.get_active_character(owner_id)
            name = char.name if char else "你"
            view = discord.ui.View(timeout=None)
            for label, danger, option in _check_button_specs(check):
                view.add_item(CheckButton(conversation_id, owner_id, label, danger, option))
            await channel.send(f"👉 {name}，輪到你檢定了，點下面按鈕擲骰（或直接輸入 /coc check）：", view=view)
        except Exception:
            # Never let one broken/unpostable entry (a malformed check dict,
            # a transient Discord API error, ...) silently swallow every
            # other pending check's button in the same batch, or propagate
            # up and mask whatever the caller's own try/finally is protecting.
            _logger.exception(
                "failed to post check button for owner_id=%s in conversation_id=%s", owner_id, conversation_id
            )


_TIER_ZH = {"regular": "一般成功", "hard": "困難成功", "extreme": "極難成功"}

# choice is restricted to these four literal tokens (app/luck.py's tier names,
# plus "skip") rather than [^:]* — nothing about it is freeform player text.
_LUCK_BUTTON_ID_TEMPLATE = (
    r"coc_luck:(?P<conversation_id>discord-channel-\d+):(?P<owner_id>\d+):(?P<choice>skip|regular|hard|extreme)"
)


class LuckSpendButton(discord.ui.DynamicItem[discord.ui.Button], template=_LUCK_BUTTON_ID_TEMPLATE):  # type: ignore[call-arg]
    """A "花 N 點 Luck → 一般成功" (or "維持目前結果") button posted after a
    near-miss roll — see app/commands.py's handle_check_command (which decides
    whether to prompt at all) and handle_luck_decision (what clicking one of
    these actually resolves to). Same discord.ui.DynamicItem + timeout=None
    pattern as CheckButton above, for the same reason: survives bot restarts.
    """

    def __init__(self, conversation_id: str, owner_id: str, label: str, choice: str, danger: bool = False) -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary if danger else discord.ButtonStyle.success,
                custom_id=f"coc_luck:{conversation_id}:{owner_id}:{choice}",
            )
        )
        self.conversation_id = conversation_id
        self.owner_id = owner_id
        self.choice = choice

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        danger = item.style == discord.ButtonStyle.secondary
        return cls(match["conversation_id"], match["owner_id"], item.label or "維持目前結果", match["choice"], danger)

    async def callback(self, interaction: discord.Interaction) -> None:
        if str(interaction.user.id) != self.owner_id:
            await interaction.response.send_message("這不是你的 Luck 花費決定，換你自己的角色來按。", ephemeral=True)
            return
        if not locks.try_acquire_check(self.conversation_id, self.owner_id):
            await interaction.response.send_message("上一次的檢定還在處理中，請稍等結果出來，不要重複點擊。", ephemeral=True)
            return
        try:
            await interaction.response.edit_message(view=None)
            reply = _make_interaction_reply(interaction)
            send_image = _make_send_image(interaction.channel)
            state_before = await asyncio.to_thread(load_group_state, self.conversation_id)
            before_pending = dict(state_before.pending_checks)
            before_luck_pending = dict(state_before.pending_luck_decisions)
            try:
                await handle_luck_decision(
                    self.conversation_id, self.owner_id, self.choice, reply, _send_dm, send_image,
                    _send_dm_image, split_roll_feedback=True, acquire_legacy_for_keeper=True
                )
            finally:
                # See on_message's own comment: always attempt this, even if
                # handle_luck_decision raised partway through.
                await _post_pending_buttons(interaction.channel, self.conversation_id, before_pending, before_luck_pending)
        finally:
            locks.release_check(self.conversation_id, self.owner_id)


async def _post_luck_buttons(
    channel: discord.abc.Messageable, conversation_id: str, state: GroupState, before_pending: dict
) -> None:
    """Same content-diff pattern as _post_check_buttons, for pending Luck-spend
    decisions (see app/commands.py's handle_check_command). Takes an already-
    loaded `state` for the same reason _post_check_buttons does."""
    for owner_id, decision in state.pending_luck_decisions.items():
        if before_pending.get(owner_id) == decision:
            continue
        try:
            char = state.get_active_character(owner_id)
            name = char.name if char else "你"
            view = discord.ui.View(timeout=None)
            for option in decision["options"]:
                label = f"花 {option['cost']} 點 Luck → {_TIER_ZH[option['tier']]}"
                view.add_item(LuckSpendButton(conversation_id, owner_id, label, option["tier"]))
            view.add_item(LuckSpendButton(conversation_id, owner_id, "維持目前結果", "skip", danger=True))
            await channel.send(f"🍀 {name}，要花 Luck 買到更好的結果嗎？", view=view)
        except Exception:
            _logger.exception(
                "failed to post luck button for owner_id=%s in conversation_id=%s", owner_id, conversation_id
            )


async def _post_pending_buttons(
    channel: discord.abc.Messageable,
    conversation_id: str,
    before_pending: dict,
    before_luck_pending: dict,
) -> None:
    """Shared tail for every place that posts fresh check/Luck-spend buttons
    after a command or turn finishes (CheckButton/LuckSpendButton callbacks,
    on_message).

    Loads state once for _post_check_buttons, then loads it AGAIN,
    separately, right before _post_luck_buttons — this is NOT the same as
    the two independently reloading right after each other with nothing in
    between (which really would be a redundant read worth merging): every
    channel.send() inside _post_check_buttons' loop is a real await, a point
    where the event loop can run another handler (a concurrent /coc newgame,
    another player's action, ...) that mutates pending_luck_decisions before
    _post_luck_buttons ever runs. An earlier version of this function shared
    one snapshot across both calls — cheaper, but meant _post_luck_buttons
    could publish a stale Luck-spend view for a decision that had already
    been resolved or cleared by the time it actually posted. Reverted after
    review: the point-in-time freshness on the Luck pass matters more than
    saving one SQLite read here.

    Both loads run via asyncio.to_thread: load_group_state is a synchronous
    SQLite read + JSON deserialize of the *whole* GroupState blob (scenario
    text, full log, character sheets, ...). Calling it directly on
    discord.py's single event-loop thread blocks Discord's gateway heartbeat
    processing for however long that takes — on a long-running campaign
    (a large scenario_text, hundreds of log entries) this is measurable, and
    under load can trigger discord.py's own "Heartbeat blocked" warnings or
    even a gateway reconnect. Every direct load_group_state call in this
    module goes through to_thread for the same reason."""
    state = await asyncio.to_thread(load_group_state, conversation_id)
    await _post_check_buttons(channel, conversation_id, state, before_pending)
    state = await asyncio.to_thread(load_group_state, conversation_id)
    await _post_luck_buttons(channel, conversation_id, state, before_luck_pending)


# choice is restricted to these two literal tokens (see app/commands.py's
# resolve_pdf_upload_choice) rather than [^:]* — nothing about it is freeform
# player text.
_PDF_CHOICE_BUTTON_ID_TEMPLATE = r"coc_pdfchoice:(?P<conversation_id>discord-channel-\d+):(?P<choice>new|fix)"


class PdfUploadChoiceButton(discord.ui.DynamicItem[discord.ui.Button], template=_PDF_CHOICE_BUTTON_ID_TEMPLATE):  # type: ignore[call-arg]
    """Posted after a PDF re-upload while a scenario is already running (see
    app/commands.py's handle_pdf_upload, which stashes the extraction into
    state.pending_pdf_upload rather than guessing) — lets the GM pick whether
    the new upload is a fresh scenario or a corrected re-upload of the
    current one. Not restricted to a specific user (unlike CheckButton/
    LuckSpendButton, which resolve one particular player's own roll/decision)
    — this is a group-level call about which scenario is running, and this
    project has no separate "who's the GM" role to check against. Dynamic
    (not a plain View) for the same reason CheckButton/LuckSpendButton are:
    this project restarts on almost every deploy, and pending_pdf_upload is
    persisted specifically so this button still works across one."""

    def __init__(self, conversation_id: str, choice: str, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.danger if choice == "new" else discord.ButtonStyle.primary,
                custom_id=f"coc_pdfchoice:{conversation_id}:{choice}",
            )
        )
        self.conversation_id = conversation_id
        self.choice = choice

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["conversation_id"], match["choice"], item.label or "")

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(view=None)
        push = _make_reply(interaction.channel)
        await resolve_pdf_upload_choice(self.conversation_id, self.choice, push)


async def _post_pdf_upload_buttons(channel: discord.abc.Messageable, conversation_id: str) -> None:
    """Checks whether the PDF upload that just ran (see on_message's .pdf
    branch) left a pending new-scenario-vs-correction choice for the GM (see
    app/commands.py's handle_pdf_upload) and, if so, posts the two buttons
    that resolve it. No "before" snapshot is needed the way
    _post_check_buttons/_post_luck_buttons need one: a fresh PDF upload is
    the only thing that ever sets pending_pdf_upload (see
    handle_pdf_upload/resolve_pdf_upload_choice, the latter always clearing
    it), so simply checking whether it's non-None right after the call is
    unambiguous — there's no pre-existing pending choice this could be
    confused with."""
    state = await asyncio.to_thread(load_group_state, conversation_id)
    if state.pending_pdf_upload is None:
        return
    view = discord.ui.View(timeout=None)
    view.add_item(PdfUploadChoiceButton(conversation_id, "new", "🆕 全新劇本"))
    view.add_item(PdfUploadChoiceButton(conversation_id, "fix", "🩹 修正目前劇本"))
    await channel.send("👉 請選擇：", view=view)


_HELP_BUTTON_ID_TEMPLATE = r"coc_help:(?P<conversation_id>discord-channel-\d+):(?P<path>root|[a-z0-9_-]+(?:/[a-z0-9_-]+)?)"


def _help_path_token(path: tuple[str, ...]) -> str:
    return "/".join(path) if path else "root"


def _help_path_from_token(token: str) -> tuple[str, ...]:
    return () if token == "root" else tuple(token.split("/"))


def _help_view(conversation_id: str, page: HelpPage) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in page.actions:
        view.add_item(HelpButton(conversation_id, action))
    return view


class HelpButton(discord.ui.DynamicItem[discord.ui.Button], template=_HELP_BUTTON_ID_TEMPLATE):
    """Persistent navigation button for the three-level player help."""

    def __init__(self, conversation_id: str, action: HelpAction):
        label = action.label[:80]
        style = discord.ButtonStyle.primary if action.kind in ("category", "entry") else discord.ButtonStyle.secondary
        super().__init__(
            discord.ui.Button(
                label=label,
                style=style,
                custom_id=f"coc_help:{conversation_id}:{_help_path_token(action.path)}",
            )
        )
        self.conversation_id = conversation_id
        self.path = action.path

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        path = _help_path_from_token(match["path"])
        kind = "home" if not path else "entry" if len(path) == 2 else "category"
        return cls(match["conversation_id"], HelpAction(item.label or "Help", path, kind))

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if channel is None or _conversation_id(channel.id) != self.conversation_id:
            await interaction.response.send_message("這個 Help 按鈕不屬於目前頻道。", ephemeral=True)
            return
        state = await asyncio.to_thread(load_group_state, self.conversation_id)
        page = help_service.get_page(state, str(interaction.user.id), self.path)
        await interaction.response.edit_message(
            content=help_service.bounded_page_text(page, MAX_DISCORD_MESSAGE_CHARS),
            view=_help_view(self.conversation_id, page),
        )


async def _post_help_page(channel: discord.abc.Messageable, conversation_id: str, user_id: str, path: tuple[str, ...]) -> None:
    state = await asyncio.to_thread(load_group_state, conversation_id)
    page = help_service.get_page(state, user_id, path)
    await channel.send(
        help_service.bounded_page_text(page, MAX_DISCORD_MESSAGE_CHARS),
        view=_help_view(conversation_id, page),
    )


client.add_dynamic_items(CheckButton, LuckSpendButton, PdfUploadChoiceButton, HelpButton)


@client.event
async def on_ready() -> None:
    global _backup_task
    if _backup_task is None or _backup_task.done():
        _backup_task = asyncio.create_task(_backup_loop())
    print(f"Discord bot 已上線：{client.user}")


async def _backup_loop() -> None:
    while True:
        try:
            await asyncio.sleep(BACKUP_INTERVAL_MINUTES * 60)
            await asyncio.to_thread(db.backup_now, "scheduled")
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.exception("scheduled backup failed; will retry next interval")


@client.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return  # ignore other bots (and echoes of our own messages)

    if _is_ooc_message(message.content):
        return

    conversation_id = _conversation_id(message.channel.id)
    user_id = str(message.author.id)
    reply = _make_reply(message.channel)
    send_image = _make_send_image(message.channel)

    async def get_display_name() -> str:
        return message.author.display_name

    def format_mention(owner_id: str) -> str:
        # owner_id is str(message.author.id) — a Discord snowflake — so this
        # needs no API call, unlike get_display_name; Discord resolves
        # <@id> to a clickable name client-side.
        return f"<@{owner_id}>"

    try:
        pdf_attachments = [a for a in message.attachments if a.filename.lower().endswith(".pdf")]
        if pdf_attachments:
            ordered = sorted(pdf_attachments, key=lambda item: item.filename.lower())
            part_name = re.compile(r"(?:^|[_ .-])part(?:[_ .-]?\d+)(?:$|[_ .-])", re.IGNORECASE)
            should_stage = len(ordered) > 1 or any(
                part_name.search(Path(item.filename).stem) for item in ordered
            )
            if should_stage:
                staged = []
                for attachment in ordered:
                    payload = await attachment.read()
                    key = await asyncio.to_thread(scenario_library.stage_upload, payload)
                    staged.append({"key": key, "file_name": attachment.filename})
                async with locks.get_conversation_lock(conversation_id):
                    state = await asyncio.to_thread(load_group_state, conversation_id)
                    state.staged_pdf_parts.extend(staged)
                    from app.repositories.group_state import save_state as save_group_state
                    save_group_state(state)
                await reply(
                    "已暫存 PDF part，尚未合併或解析：\n"
                    + "\n".join(f"・{item['key'][:12]} {item['file_name']}" for item in staged)
                    + "\n請由 KP 輸入 `/coc scenario merge 暫存ID1 暫存ID2 ...`。"
                )
                return
            attachment = ordered[0]
            content = await attachment.read()
            filename = attachment.filename
            # No reply-token/time-window constraint here, so the same callback
            # serves as both the immediate ack and the final result.
            await handle_pdf_upload(conversation_id, reply, reply, content, filename)
            await _post_pdf_upload_buttons(message.channel, conversation_id)
            return

        # Requires the map_ prefix (see docs/character_and_dictionary_system_
        # spec.md's Module 1) — a bare .yaml/.yml attachment is no longer
        # assumed to be a map on extension alone.
        map_attachments = [
            a for a in message.attachments
            if a.filename.lower().startswith("map_") and a.filename.lower().endswith((".yaml", ".yml"))
        ]
        if map_attachments:
            attachment = map_attachments[0]
            content = await attachment.read()
            await handle_map_upload(conversation_id, reply, reply, content, attachment.filename)
            return

        # A .yaml/.yml file that's missing the map_ prefix isn't silently
        # dropped (it wouldn't match anything else below either) — tell the
        # GM exactly what to rename it to, rather than leaving them wondering
        # why nothing happened.
        unprefixed_map_attachments = [
            a for a in message.attachments
            if a.filename.lower().endswith((".yaml", ".yml")) and not a.filename.lower().startswith("map_")
        ]
        if unprefixed_map_attachments:
            await reply(
                f"「{unprefixed_map_attachments[0].filename}」看起來是地圖資料，"
                "但檔名需要以 map_ 開頭（例如 map_lighthouse.yaml）才會被辨識，請改檔名後重新上傳。"
            )
            return

        role_attachments = [
            a for a in message.attachments
            if a.filename.lower().startswith("role_") and a.filename.lower().endswith((".txt", ".md"))
        ]
        if role_attachments:
            # A GM handing out the whole party's cards often drags every
            # role_*.txt into one message (Discord natively supports multiple
            # attachments per message) — this used to only ever look at
            # attachments[0], silently dropping every other card in the same
            # message with no feedback at all.
            for attachment in role_attachments:
                content = await attachment.read()
                await handle_role_sheet_upload(
                    conversation_id, reply, content.decode("utf-8", errors="replace"), attachment.filename
                )
            return

        compare_attachments = [a for a in message.attachments if a.filename.lower().endswith((".txt", ".md"))]
        if compare_attachments:
            attachment = compare_attachments[0]
            content = await attachment.read()
            await handle_scenario_compare_upload(
                conversation_id, reply, reply, content.decode("utf-8", errors="replace"), attachment.filename
            )
            return

        text = (message.content or "").strip()
        if not text:
            if message.attachments:
                await handle_unsupported_message(conversation_id, reply, "附件")
            return

        command_parts = text.split()
        if command_parts[0].casefold() == "/coc" and (
            len(command_parts) == 1
            or command_parts[1].casefold() == "help"
            or not command_router.is_known_coc_command(command_parts[1])
        ):
            state = await asyncio.to_thread(load_group_state, conversation_id)
            path = help_service.resolve_text_path(
                state,
                user_id,
                command_parts[2:] if len(command_parts) > 1 and command_parts[1].casefold() == "help" else [],
            )
            await _post_help_page(message.channel, conversation_id, user_id, path)
            return

        state_before = await asyncio.to_thread(load_group_state, conversation_id)
        before_pending = dict(state_before.pending_checks)
        before_luck_pending = dict(state_before.pending_luck_decisions)
        try:
            is_keeper = _is_keeper_member(message.author)
            await command_router.handle_text_message(
                conversation_id, user_id, get_display_name, reply, _send_dm, send_image, _send_dm_image, text,
                format_mention, is_keeper,
            )
        finally:
            # Always attempt this, even if handle_text_message raised partway
            # through a turn — a check can already be registered and saved
            # (e.g. skill_check's tool call) before a *later* tool call in the
            # same turn blows up, and that would otherwise silently strand a
            # pending check with no button ever posted for it.
            await _post_pending_buttons(message.channel, conversation_id, before_pending, before_luck_pending)
    except StateRevisionConflict:
        _logger.warning(
            "state revision conflict for conversation_id=%s; asking the user to retry",
            conversation_id,
        )
        try:
            await reply("遊戲狀態剛被另一個操作更新，這次指令沒有套用，請再試一次。")
        except Exception:
            _logger.exception("failed to report state revision conflict for conversation_id=%s", conversation_id)
    except Exception:  # noqa: BLE001 - keep the bot alive, surface the error to the channel
        _logger.exception("on_message failed for conversation_id=%s", conversation_id)
        try:
            await reply("發生內部錯誤了，請稍後再試；詳細資訊已記錄到 Bot log。")
        except Exception:
            _logger.exception("also failed to report the above error back to conversation_id=%s", conversation_id)


def main() -> None:
    if not DISCORD_BOT_TOKEN:
        raise SystemExit("尚未設定 DISCORD_BOT_TOKEN，請檢查 .env")
    client.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
