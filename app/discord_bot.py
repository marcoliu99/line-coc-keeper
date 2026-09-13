"""Discord adapter — the discord.py-based front-end.

Game/command logic lives in app/commands.py; this module only translates
Discord events into calls against that shared layer. Run it as its own process
(`python -m app.discord_bot`), separate from the LINE FastAPI server — discord.py
owns a persistent gateway connection rather than serving HTTP requests, so
there's no webhook URL or ngrok tunnel needed for this adapter at all.
"""
from __future__ import annotations

import io

import discord

from app import commands, locks
from app.config import DISCORD_BOT_TOKEN
from app.state import load_state as load_group_state

MAX_DISCORD_MESSAGE_CHARS = 1900  # Discord's hard limit is 2000; leave a margin
MAX_REPLY_MESSAGES = 10

intents = discord.Intents.default()
intents.message_content = True  # privileged intent — must also be switched on
# for this bot under Developer Portal > your app > Bot > Privileged Gateway Intents,
# or on_message will only ever see empty message content.
client = discord.Client(intents=intents)


def _chunk_text(text: str) -> list[str]:
    text = text.strip() or "（沒有內容）"
    chunks = [text[i : i + MAX_DISCORD_MESSAGE_CHARS] for i in range(0, len(text), MAX_DISCORD_MESSAGE_CHARS)]
    return chunks[:MAX_REPLY_MESSAGES]


def _make_reply(channel: discord.abc.Messageable) -> commands.Reply:
    async def reply(text: str) -> None:
        for chunk in _chunk_text(text):
            await channel.send(chunk)

    return reply


def _conversation_id(channel_id: int) -> str:
    return f"discord-channel-{channel_id}"


async def _send_dm(owner_id: str, text: str) -> None:
    # owner_id is str(discord.Member.id), as stored on Character.owner_id. Raises
    # if the user has DMs from server members disabled; commands.py swallows
    # that (see its docstring on why it doesn't fall back to posting publicly).
    user = client.get_user(int(owner_id)) or await client.fetch_user(int(owner_id))
    for chunk in _chunk_text(text):
        await user.send(chunk)


def _make_send_image(channel: discord.abc.Messageable) -> commands.SendImage:
    async def send_image(png_bytes: bytes, conversation_id: str, page_number: int) -> None:
        # conversation_id/page_number are part of the shared SendImage signature
        # (LINE's adapter needs them to build a URL) but unused here — Discord
        # just attaches the bytes directly.
        await channel.send(file=discord.File(io.BytesIO(png_bytes), filename=f"page_{page_number}.png"))

    return send_image


async def _send_dm_image(owner_id: str, png_bytes: bytes, conversation_id: str, page_number: int) -> None:
    user = client.get_user(int(owner_id)) or await client.fetch_user(int(owner_id))
    await user.send(file=discord.File(io.BytesIO(png_bytes), filename=f"page_{page_number}.png"))


def _make_interaction_reply(interaction: discord.Interaction) -> commands.Reply:
    # Used only after interaction.response.defer() (see CheckButton.callback),
    # so the actual send has to go through followup, not response.send_message.
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


class CheckButton(discord.ui.DynamicItem[discord.ui.Button], template=_CHECK_BUTTON_ID_TEMPLATE):
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
        await interaction.response.defer()  # resolving involves a Keeper (LLM) call — can't ack within 3s otherwise
        reply = _make_interaction_reply(interaction)
        send_image = _make_send_image(interaction.channel)
        command_text = f"/coc check {self.option}" if self.option else "/coc check"
        async with locks.get_conversation_lock(self.conversation_id):
            state_before = load_group_state(self.conversation_id)
            before_pending = dict(state_before.pending_checks)
            before_luck_pending = dict(state_before.pending_luck_decisions)
            await commands.handle_check_command(
                self.conversation_id, self.owner_id, reply, _send_dm, send_image, _send_dm_image, command_text
            )
        await _post_check_buttons(interaction.channel, self.conversation_id, before_pending)
        await _post_luck_buttons(interaction.channel, self.conversation_id, before_luck_pending)
        try:
            await interaction.message.edit(view=None)  # spent — don't let it be clicked twice
        except Exception:
            pass


async def _post_check_buttons(channel: discord.abc.Messageable, conversation_id: str, before_pending: dict) -> None:
    """Posts a roll button (or, for a "choice" check, one button per option —
    e.g. 閃避／反擊ーー in the same message) for every pending check that's
    new or changed since `before_pending` was snapshotted. Content-diffed,
    not just key-diffed, so a replaced check for the same player still gets
    fresh buttons; a stale/duplicate pending entry never gets re-posted."""
    state = load_group_state(conversation_id)
    for owner_id, check in state.pending_checks.items():
        if before_pending.get(owner_id) == check:
            continue
        char = state.characters.get(owner_id)
        name = char.name if char else "你"
        view = discord.ui.View(timeout=None)
        for label, danger, option in _check_button_specs(check):
            view.add_item(CheckButton(conversation_id, owner_id, label, danger, option))
        await channel.send(f"👉 {name}，輪到你檢定了，點下面按鈕擲骰（或直接輸入 /coc check）：", view=view)


_TIER_ZH = {"regular": "一般成功", "hard": "困難成功", "extreme": "極難成功"}

# choice is restricted to these four literal tokens (app/luck.py's tier names,
# plus "skip") rather than [^:]* — nothing about it is freeform player text.
_LUCK_BUTTON_ID_TEMPLATE = (
    r"coc_luck:(?P<conversation_id>discord-channel-\d+):(?P<owner_id>\d+):(?P<choice>skip|regular|hard|extreme)"
)


class LuckSpendButton(discord.ui.DynamicItem[discord.ui.Button], template=_LUCK_BUTTON_ID_TEMPLATE):
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
        await interaction.response.defer()  # resolving involves a Keeper (LLM) call — can't ack within 3s otherwise
        reply = _make_interaction_reply(interaction)
        send_image = _make_send_image(interaction.channel)
        async with locks.get_conversation_lock(self.conversation_id):
            before_pending = dict(load_group_state(self.conversation_id).pending_checks)
            await commands.handle_luck_decision(
                self.conversation_id, self.owner_id, self.choice, reply, _send_dm, send_image, _send_dm_image
            )
        await _post_check_buttons(interaction.channel, self.conversation_id, before_pending)
        try:
            await interaction.message.edit(view=None)  # spent — don't let it be clicked twice
        except Exception:
            pass


async def _post_luck_buttons(channel: discord.abc.Messageable, conversation_id: str, before_pending: dict) -> None:
    """Same content-diff pattern as _post_check_buttons, for pending Luck-spend
    decisions (see app/commands.py's handle_check_command)."""
    state = load_group_state(conversation_id)
    for owner_id, decision in state.pending_luck_decisions.items():
        if before_pending.get(owner_id) == decision:
            continue
        char = state.characters.get(owner_id)
        name = char.name if char else "你"
        view = discord.ui.View(timeout=None)
        for option in decision["options"]:
            label = f"花 {option['cost']} 點 Luck → {_TIER_ZH[option['tier']]}"
            view.add_item(LuckSpendButton(conversation_id, owner_id, label, option["tier"]))
        view.add_item(LuckSpendButton(conversation_id, owner_id, "維持目前結果", "skip", danger=True))
        await channel.send(f"🍀 {name}，要花 Luck 買到更好的結果嗎？", view=view)


client.add_dynamic_items(CheckButton, LuckSpendButton)


@client.event
async def on_ready() -> None:
    print(f"Discord bot 已上線：{client.user}")


@client.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return  # ignore other bots (and echoes of our own messages)

    conversation_id = _conversation_id(message.channel.id)
    user_id = str(message.author.id)
    reply = _make_reply(message.channel)
    send_image = _make_send_image(message.channel)

    async def get_display_name() -> str:
        return message.author.display_name

    try:
        pdf_attachments = [a for a in message.attachments if a.filename.lower().endswith(".pdf")]
        if pdf_attachments:
            attachment = pdf_attachments[0]
            content = await attachment.read()
            # No reply-token/time-window constraint here, so the same callback
            # serves as both the immediate ack and the final result.
            await commands.handle_pdf_upload(conversation_id, reply, reply, content, attachment.filename)
            return

        text = (message.content or "").strip()
        if not text:
            if message.attachments:
                await commands.handle_unsupported_message(conversation_id, reply, "附件")
            return

        state_before = load_group_state(conversation_id)
        before_pending = dict(state_before.pending_checks)
        before_luck_pending = dict(state_before.pending_luck_decisions)
        await commands.handle_text_message(
            conversation_id, user_id, get_display_name, reply, _send_dm, send_image, _send_dm_image, text
        )
        await _post_check_buttons(message.channel, conversation_id, before_pending)
        await _post_luck_buttons(message.channel, conversation_id, before_luck_pending)
    except Exception as exc:  # noqa: BLE001 - keep the bot alive, surface the error to the channel
        try:
            await reply(f"發生錯誤了：{exc}")
        except Exception:
            pass


def main() -> None:
    if not DISCORD_BOT_TOKEN:
        raise SystemExit("尚未設定 DISCORD_BOT_TOKEN，請檢查 .env")
    client.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
