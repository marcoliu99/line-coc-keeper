"""Discord adapter — the discord.py-based front-end.

Game/command logic lives in app/commands.py; this module only translates
Discord events into calls against that shared layer. Run it as its own process
(`python -m app.discord_bot`); discord.py owns a persistent gateway connection,
so there is no webhook URL or ngrok tunnel.
"""
from __future__ import annotations

import asyncio
import functools
import io
import logging
import re
import time
import unicodedata
from collections.abc import Awaitable
from pathlib import Path
from typing import TypeVar, cast

import discord

from app import (
    async_utils,
    config,
    db,
    help_service,
    locks,
    logging_config,
    observability,
    providers,
    scenario_library,
    scenario_rag,
)
from app.commands import router as command_router
from app.commands import sudo as sudo_policy
from app.config import (
    BACKUP_INTERVAL_MINUTES,
    DISCORD_BOT_TOKEN,
    LOG_SLOW_OPERATION_MS,
    LOG_SLOW_REQUEST_MS,
)
from app.help_registry import HelpAction, HelpPage
from app.legacy_commands import (
    Reply,
    SendImage,
    _is_kp_or_keeper,
    handle_check_command,
    handle_luck_decision,
    handle_map_upload,
    handle_pdf_upload,
    handle_role_sheet_upload,
    handle_scenario_compare_upload,
    handle_unsupported_message,
    resolve_pdf_upload_choice,
)
from app.models import GroupState
from app.providers import anthropic_provider, gemini_provider, openai_provider
from app.repositories.group_state import StateRevisionConflict
from app.repositories.group_state import load_state as load_group_state

_logger = logging.getLogger(__name__)
_backup_task: asyncio.Task | None = None
_T = TypeVar("_T")

MAX_DISCORD_MESSAGE_CHARS = 1900  # Discord's hard limit is 2000; leave a margin
MAX_REPLY_MESSAGES = 10
_REPLY_METRIC_KEYS = (
    "reply_message_count", "reply_edit_count", "reply_chunk_count", "reply_bytes",
)

intents = discord.Intents.default()
intents.message_content = True  # privileged intent — must also be switched on
# for this bot under Developer Portal > your app > Bot > Privileged Gateway Intents,
# or on_message will only ever see empty message content.
client = discord.Client(intents=intents)


def _is_ooc_message(text: str) -> bool:
    ooc_text = unicodedata.normalize("NFKC", text or "").lstrip()
    return ooc_text.startswith(("@", "<@"))


def _chunk_text(text: str) -> list[str]:
    text = text.strip() or "（沒有內容）"
    chunks = [text[i : i + MAX_DISCORD_MESSAGE_CHARS] for i in range(0, len(text), MAX_DISCORD_MESSAGE_CHARS)]
    return chunks[:MAX_REPLY_MESSAGES]


def _mark_reply_metrics(**touched: int) -> None:
    """Record one reply operation's metrics, zero-filling every
    _REPLY_METRIC_KEYS entry this call didn't touch.

    A caller reading metrics right after a single send/edit call — not just
    at the final per-request completion event, which backfills missing keys
    via _request_metrics()'s setdefault — needs the full key set present.
    Centralized here (instead of each call site separately listing "the
    other keys") so a future reply-metric source only has to say what it
    touched, not enumerate what it didn't.
    """
    for key in _REPLY_METRIC_KEYS:
        observability.increment_metric(key, touched.get(key, 0))


def _record_reply_output(text: str) -> None:
    """Account for text sent outside the shared Reply callback."""
    if not config.LOG_ENABLED:
        return
    _mark_reply_metrics(reply_message_count=1, reply_chunk_count=1, reply_bytes=len(text.encode("utf-8")))


def _record_reply_binary(size: int) -> None:
    """Account for a Discord message that contains an attachment."""
    if not config.LOG_ENABLED:
        return
    _mark_reply_metrics(reply_message_count=1, reply_bytes=size)


def _record_reply_edit(text: str) -> None:
    """Account for a text update to an existing Discord message."""
    if not config.LOG_ENABLED:
        return
    _mark_reply_metrics(reply_edit_count=1, reply_bytes=len(text.encode("utf-8")))


def _request_metrics() -> dict[str, int]:
    """Return stable reply metric keys for request lifecycle events."""
    metrics = observability.current_metrics()
    for key in _REPLY_METRIC_KEYS:
        metrics.setdefault(key, 0)
    return metrics


def _record_sent_chunk(metrics: dict[str, int], chunk: str) -> None:
    """Add one chunk only after its Discord send has succeeded."""
    byte_count = len(chunk.encode("utf-8"))
    metrics["reply_message_count"] += 1
    metrics["reply_chunk_count"] += 1
    metrics["reply_bytes"] += byte_count
    observability.increment_metric("reply_message_count")
    observability.increment_metric("reply_chunk_count")
    observability.increment_metric("reply_bytes", byte_count)


async def _discord_operation(awaitable: Awaitable[_T]) -> _T:
    """Bound one Discord API operation without retrying a possible send."""
    try:
        async with asyncio.timeout(config.DISCORD_REQUEST_TIMEOUT_SECONDS):
            return await awaitable
    except asyncio.TimeoutError:
        observability.event(
            "discord.request.timeout",
            level=logging.ERROR,
            timeout_ms=config.DISCORD_REQUEST_TIMEOUT_SECONDS * 1000,
            status="timeout",
        )
        raise


async def _send_direct_message(
    channel: discord.abc.Messageable, text: str, *, view: discord.ui.View | None = None
) -> None:
    """Send a non-chunked public message with the same reply span as Reply."""
    if not config.LOG_ENABLED:
        if view is None:
            await _discord_operation(channel.send(text))
        else:
            await _discord_operation(channel.send(text, view=view))
        return
    byte_count = len(text.encode("utf-8"))
    with observability.span(
        "discord.reply",
        slow_threshold_ms=LOG_SLOW_OPERATION_MS,
        reply_message_count=1,
        reply_bytes=byte_count,
        reply_chunk_count=1,
        reply_edit_count=0,
    ):
        if view is None:
            await _discord_operation(channel.send(text))
        else:
            await _discord_operation(channel.send(text, view=view))
    _record_reply_output(text)


async def _send_interaction_message(
    interaction: discord.Interaction, text: str, *, ephemeral: bool = False
) -> None:
    """Send an interaction response and include it in request metrics."""
    if not config.LOG_ENABLED:
        await _discord_operation(interaction.response.send_message(text, ephemeral=ephemeral))
        return
    byte_count = len(text.encode("utf-8"))
    with observability.span(
        "discord.reply",
        slow_threshold_ms=LOG_SLOW_OPERATION_MS,
        reply_message_count=1,
        reply_bytes=byte_count,
        reply_chunk_count=1,
        reply_edit_count=0,
        ephemeral=ephemeral,
    ):
        await _discord_operation(interaction.response.send_message(text, ephemeral=ephemeral))
    _record_reply_output(text)


async def _edit_interaction_message(
    interaction: discord.Interaction, text: str, *, view: discord.ui.View | None = None
) -> None:
    """Edit an existing interaction message without inflating message count."""
    if not config.LOG_ENABLED:
        await _discord_operation(interaction.response.edit_message(content=text, view=view))
        return
    byte_count = len(text.encode("utf-8"))
    with observability.span(
        "discord.reply",
        slow_threshold_ms=LOG_SLOW_OPERATION_MS,
        reply_message_count=0,
        reply_bytes=byte_count,
        reply_chunk_count=0,
        reply_edit_count=1,
    ):
        await _discord_operation(interaction.response.edit_message(content=text, view=view))
    _record_reply_edit(text)


async def _edit_interaction_view(
    interaction: discord.Interaction, *, view: discord.ui.View | None = None
) -> None:
    """Measure an interaction edit that changes only the component view."""
    if not config.LOG_ENABLED:
        await _discord_operation(interaction.response.edit_message(view=view))
        return
    with observability.span(
        "discord.reply",
        slow_threshold_ms=LOG_SLOW_OPERATION_MS,
        reply_message_count=0,
        reply_bytes=0,
        reply_chunk_count=0,
        reply_edit_count=1,
    ):
        await _discord_operation(interaction.response.edit_message(view=view))
    _record_reply_edit("")


async def _send_direct_image(
    channel: discord.abc.Messageable, png_bytes: bytes, page_number: int
) -> None:
    """Send a public attachment with Discord reply timing and byte metrics."""
    filename = f"page_{page_number}.png"
    if not config.LOG_ENABLED:
        await _discord_operation(channel.send(file=discord.File(io.BytesIO(png_bytes), filename=filename)))
        return
    with observability.span(
        "discord.reply",
        slow_threshold_ms=LOG_SLOW_OPERATION_MS,
        reply_message_count=1,
        reply_bytes=len(png_bytes),
        reply_chunk_count=0,
        reply_edit_count=0,
        reply_kind="image",
    ):
        await _discord_operation(channel.send(file=discord.File(io.BytesIO(png_bytes), filename=filename)))
    _record_reply_binary(len(png_bytes))


def _make_reply(channel: discord.abc.Messageable) -> Reply:
    async def reply(text: str) -> None:
        chunks = _chunk_text(text)
        if not config.LOG_ENABLED:
            for chunk in chunks:
                await _discord_operation(channel.send(chunk))
            return
        reply_metrics = {
            "reply_message_count": 0,
            "reply_chunk_count": 0,
            "reply_bytes": 0,
        }
        with observability.span(
            "discord.reply",
            slow_threshold_ms=LOG_SLOW_OPERATION_MS,
            metrics=reply_metrics,
        ):
            # Written to the shared _METRICS context, not passed as a literal
            # span() field — span() merges **_METRICS.get() into the log
            # event too, and a key present in both would collide (see
            # app/observability.py:span's **fields unpacking).
            observability.increment_metric("reply_edit_count", 0)
            for chunk in chunks:
                await _discord_operation(channel.send(chunk))
                _record_sent_chunk(reply_metrics, chunk)

    return reply


def _conversation_id(channel_id: int) -> str:
    return f"discord-channel-{channel_id}"


def _observed_interaction(callback):
    """Give persistent Discord buttons the same request lifecycle as messages."""
    @functools.wraps(callback)
    async def wrapped(self, interaction: discord.Interaction):
        channel_id = getattr(interaction.channel, "id", None)
        conversation_id = _conversation_id(channel_id) if channel_id is not None else None
        with observability.request_context(
            conversation_id=conversation_id,
        ):
            observed = config.LOG_ENABLED
            started = time.perf_counter() if observed else 0.0
            if observed:
                observability.event("request.started", platform="discord", message_kind="button")
            try:
                await callback(self, interaction)
            except Exception as exc:
                if observed:
                    observability.event(
                        "request.failed", level=logging.ERROR,
                        duration_ms=(time.perf_counter() - started) * 1000,
                        error_type=type(exc).__name__, status="error",
                        **_request_metrics(),
                    )
                raise
            else:
                if observed:
                    duration_ms = (time.perf_counter() - started) * 1000
                    observability.event(
                        "request.completed",
                        level=logging.WARNING if duration_ms >= LOG_SLOW_REQUEST_MS else logging.INFO,
                        duration_ms=duration_ms,
                        slow_threshold_ms=LOG_SLOW_REQUEST_MS,
                        status="success",
                        **_request_metrics(),
                    )
    return wrapped


def _is_keeper_member(member: discord.abc.User) -> bool:
    """Allow the configured Discord Keeper role to manage host-only state."""
    roles = getattr(member, "roles", ())
    return any(getattr(role, "name", "").casefold() == "keeper" for role in roles)


async def _send_dm(owner_id: str, text: str) -> None:
    # owner_id is str(discord.Member.id), as stored on Character.owner_id. Raises
    # if the user has DMs from server members disabled; commands.py swallows
    # that (see its docstring on why it doesn't fall back to posting publicly).
    user = client.get_user(int(owner_id)) or await _discord_operation(client.fetch_user(int(owner_id)))
    if user is None:
        raise RuntimeError(f"Discord user {owner_id} could not be resolved")
    for chunk in _chunk_text(text):
        if not config.LOG_ENABLED:
            await _discord_operation(user.send(chunk))
            continue
        with observability.span(
            "discord.reply",
            slow_threshold_ms=LOG_SLOW_OPERATION_MS,
            reply_kind="dm",
            reply_message_count=1,
            reply_chunk_count=1,
            reply_bytes=len(chunk.encode("utf-8")),
            reply_edit_count=0,
        ):
            await _discord_operation(user.send(chunk))
        _record_reply_output(chunk)


def _make_send_image(channel: discord.abc.Messageable) -> SendImage:
    async def send_image(png_bytes: bytes, conversation_id: str, page_number: int) -> None:
        # conversation_id/page_number are retained in the shared callback
        # signature for state-aware image sends; Discord attaches bytes directly.
        await _send_direct_image(channel, png_bytes, page_number)

    return send_image


async def _send_dm_image(owner_id: str, png_bytes: bytes, conversation_id: str, page_number: int) -> None:
    user = client.get_user(int(owner_id)) or await _discord_operation(client.fetch_user(int(owner_id)))
    if user is None:
        raise RuntimeError(f"Discord user {owner_id} could not be resolved")
    filename = f"page_{page_number}.png"
    if not config.LOG_ENABLED:
        await _discord_operation(user.send(file=discord.File(io.BytesIO(png_bytes), filename=filename)))
        return
    with observability.span(
        "discord.reply",
        slow_threshold_ms=LOG_SLOW_OPERATION_MS,
        reply_kind="dm_image",
        reply_message_count=1,
        reply_chunk_count=0,
        reply_bytes=len(png_bytes),
        reply_edit_count=0,
    ):
        await _discord_operation(user.send(file=discord.File(io.BytesIO(png_bytes), filename=filename)))
    _record_reply_binary(len(png_bytes))


def _make_interaction_reply(interaction: discord.Interaction) -> Reply:
    # Used only after the initial interaction response has been consumed
    # (defer/edit_message), so the actual send has to go through followup.
    async def reply(text: str) -> None:
        chunks = _chunk_text(text)
        if not config.LOG_ENABLED:
            for chunk in chunks:
                await _discord_operation(interaction.followup.send(chunk))
            return
        reply_metrics = {
            "reply_message_count": 0,
            "reply_chunk_count": 0,
            "reply_bytes": 0,
        }
        with observability.span(
            "discord.reply",
            slow_threshold_ms=LOG_SLOW_OPERATION_MS,
            metrics=reply_metrics,
        ):
            # See _make_reply's comment on why this isn't also a literal
            # span() field.
            observability.increment_metric("reply_edit_count", 0)
            for chunk in chunks:
                await _discord_operation(interaction.followup.send(chunk))
                _record_sent_chunk(reply_metrics, chunk)

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

    @_observed_interaction
    async def callback(self, interaction: discord.Interaction) -> None:
        if str(interaction.user.id) != self.owner_id:
            text = "這不是你的檢定，換你自己的角色來按。"
            await _send_interaction_message(interaction, text, ephemeral=True)
            return
        if not locks.try_acquire_check(self.conversation_id, self.owner_id):
            # A slow Keeper call from a first click (or an earlier /coc check)
            # is still in flight — reject outright rather than letting a
            # second click queue behind get_conversation_lock and run as a
            # genuinely separate, duplicate roll once its turn comes.
            text = "上一次的檢定還在處理中，請稍等結果出來，不要重複點擊。"
            await _send_interaction_message(interaction, text, ephemeral=True)
            return
        try:
            channel = interaction.channel
            if channel is None:
                raise RuntimeError("check interaction has no messageable channel")
            messageable = cast(discord.abc.Messageable, channel)
            await _edit_interaction_view(interaction, view=None)
            reply = _make_interaction_reply(interaction)
            send_image = _make_send_image(messageable)
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
                await _post_pending_buttons(messageable, self.conversation_id, before_pending, before_luck_pending)
        finally:
            locks.release_check(self.conversation_id, self.owner_id)


async def _post_check_buttons(
    channel: discord.abc.Messageable,
    conversation_id: str,
    state: GroupState,
    before_pending: dict,
    public_marker: str | None = None,
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
            marker = f"{public_marker}\n" if public_marker else ""
            text = f"{marker}👉 {name}，輪到你檢定了，點下面按鈕擲骰（或直接輸入 /coc check）："
            await _send_direct_message(channel, text, view=view)
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

    @_observed_interaction
    async def callback(self, interaction: discord.Interaction) -> None:
        if str(interaction.user.id) != self.owner_id:
            text = "這不是你的 Luck 花費決定，換你自己的角色來按。"
            await _send_interaction_message(interaction, text, ephemeral=True)
            return
        if not locks.try_acquire_check(self.conversation_id, self.owner_id):
            text = "上一次的檢定還在處理中，請稍等結果出來，不要重複點擊。"
            await _send_interaction_message(interaction, text, ephemeral=True)
            return
        try:
            channel = interaction.channel
            if channel is None:
                raise RuntimeError("luck interaction has no messageable channel")
            messageable = cast(discord.abc.Messageable, channel)
            await _edit_interaction_view(interaction, view=None)
            reply = _make_interaction_reply(interaction)
            send_image = _make_send_image(messageable)
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
                await _post_pending_buttons(messageable, self.conversation_id, before_pending, before_luck_pending)
        finally:
            locks.release_check(self.conversation_id, self.owner_id)


async def _post_luck_buttons(
    channel: discord.abc.Messageable,
    conversation_id: str,
    state: GroupState,
    before_pending: dict,
    public_marker: str | None = None,
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
            marker = f"{public_marker}\n" if public_marker else ""
            text = f"{marker}🍀 {name}，要花 Luck 買到更好的結果嗎？"
            await _send_direct_message(channel, text, view=view)
        except Exception:
            _logger.exception(
                "failed to post luck button for owner_id=%s in conversation_id=%s", owner_id, conversation_id
            )


async def _post_pending_buttons(
    channel: discord.abc.Messageable,
    conversation_id: str,
    before_pending: dict,
    before_luck_pending: dict,
    public_marker: str | None = None,
    *,
    sudo_command: sudo_policy.ParsedSudoCommand | None = None,
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
    check_marker = public_marker
    if sudo_command is not None:
        # Resolve the marker from the same post-dispatch snapshot used to find
        # new pending checks. Computing it before the router lock could name a
        # character that a concurrent switch/retire operation had already
        # replaced by the time this sudo command actually ran.
        check_marker = command_router.sudo_public_marker(state, sudo_command)
    await _post_check_buttons(channel, conversation_id, state, before_pending, check_marker)
    state = await asyncio.to_thread(load_group_state, conversation_id)
    luck_marker = public_marker
    if sudo_command is not None:
        luck_marker = command_router.sudo_public_marker(state, sudo_command)
    await _post_luck_buttons(channel, conversation_id, state, before_luck_pending, luck_marker)


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

    @_observed_interaction
    async def callback(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if channel is None or _conversation_id(channel.id) != self.conversation_id:
            text = "這個 PDF 按鈕不屬於目前頻道。"
            await _send_interaction_message(interaction, text, ephemeral=True)
            return
        state = await asyncio.to_thread(load_group_state, self.conversation_id)
        if not _is_kp_or_keeper(state, str(interaction.user.id), _is_keeper_member(interaction.user)):
            text = "只有目前的 KP Assistant 或 Discord Keeper 可以處理劇本 PDF。"
            await _send_interaction_message(interaction, text, ephemeral=True)
            return
        await _edit_interaction_view(interaction, view=None)
        push = _make_reply(cast(discord.abc.Messageable, channel))
        await resolve_pdf_upload_choice(
            self.conversation_id,
            self.choice,
            push,
            user_id=str(interaction.user.id),
            is_keeper=_is_keeper_member(interaction.user),
        )


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
    text = "👉 請選擇："
    await _send_direct_message(channel, text, view=view)


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


class HelpButton(discord.ui.DynamicItem[discord.ui.Button], template=_HELP_BUTTON_ID_TEMPLATE):  # type: ignore[call-arg]
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

    @_observed_interaction
    async def callback(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if channel is None or _conversation_id(channel.id) != self.conversation_id:
            text = "這個 Help 按鈕不屬於目前頻道。"
            await _send_interaction_message(interaction, text, ephemeral=True)
            return
        state = await asyncio.to_thread(load_group_state, self.conversation_id)
        page = help_service.get_page(state, str(interaction.user.id), self.path)
        content = help_service.bounded_page_text(page, MAX_DISCORD_MESSAGE_CHARS)
        await _edit_interaction_message(
            interaction, content, view=_help_view(self.conversation_id, page)
        )


async def _post_help_page(channel: discord.abc.Messageable, conversation_id: str, user_id: str, path: tuple[str, ...]) -> None:
    state = await asyncio.to_thread(load_group_state, conversation_id)
    page = help_service.get_page(state, user_id, path)
    content = help_service.bounded_page_text(page, MAX_DISCORD_MESSAGE_CHARS)
    await _send_direct_message(channel, content, view=_help_view(conversation_id, page))


client.add_dynamic_items(CheckButton, LuckSpendButton, PdfUploadChoiceButton, HelpButton)


@client.event
async def on_ready() -> None:
    global _backup_task
    provider_module = {
        "openai": openai_provider,
        "anthropic": anthropic_provider,
        "gemini": gemini_provider,
    }.get(config.LLM_PROVIDER)
    provider_key = {
        "openai": config.OPENAI_API_KEY,
        "anthropic": config.ANTHROPIC_API_KEY,
        "gemini": config.GEMINI_API_KEY,
    }.get(config.LLM_PROVIDER, "")
    if provider_module is not None and provider_key:
        try:
            await provider_module.get_async_client()
        except Exception:
            _logger.exception("failed to prewarm %s async provider client", config.LLM_PROVIDER)
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
    if message.author.bot or _is_ooc_message(message.content):
        return

    conversation_id = _conversation_id(message.channel.id)
    with observability.request_context(conversation_id=conversation_id):
        observed = config.LOG_ENABLED
        if observed:
            parts = (message.content or "").strip().split()
            command_name = parts[1].casefold() if len(parts) > 1 and parts[0].casefold() == "/coc" else None
            message_kind = "attachment" if message.attachments else "plain_text"
            observability.event(
                "request.started",
                platform="discord",
                message_kind=message_kind,
                command_name=command_name,
                attachment_count=len(message.attachments),
            )
        started = time.perf_counter() if observed else 0.0
        try:
            await _handle_message(message)
        except Exception as exc:
            if observed:
                observability.event(
                    "request.failed",
                    level=logging.ERROR,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    error_type=type(exc).__name__,
                    status="error",
                    **_request_metrics(),
                )
            raise
        else:
            if observed:
                duration_ms = (time.perf_counter() - started) * 1000
                level = logging.WARNING if duration_ms >= LOG_SLOW_REQUEST_MS else logging.INFO
                observability.event(
                    "request.completed",
                    level=level,
                    duration_ms=duration_ms,
                    slow_threshold_ms=LOG_SLOW_REQUEST_MS,
                    status=observability.current_context().get("request_status", "success"),
                    **_request_metrics(),
                )


async def _handle_message(message: discord.Message) -> None:
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
                    from app.repositories.group_state import (
                        save_state as save_group_state,
                    )
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
        sudo_command: sudo_policy.ParsedSudoCommand | None = None
        if command_parts[0].casefold() == "/coc" and len(command_parts) > 1 and command_parts[1].casefold() == "sudo":
            sudo_command, _ = sudo_policy.parse_sudo_command(command_parts, allow_opaque_target=False)
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
            await _post_pending_buttons(
                message.channel,
                conversation_id,
                before_pending,
                before_luck_pending,
                sudo_command=sudo_command,
            )
    except StateRevisionConflict:
        observability.mark_request_error()
        _logger.warning(
            "state revision conflict for conversation_id=%s; asking the user to retry",
            conversation_id,
        )
        try:
            await reply("遊戲狀態剛被另一個操作更新，這次指令沒有套用，請再試一次。")
        except Exception:
            _logger.exception("failed to report state revision conflict for conversation_id=%s", conversation_id)
    except Exception:
        observability.mark_request_error()
        _logger.exception("on_message failed for conversation_id=%s", conversation_id)
        try:
            await reply("發生內部錯誤了，請稍後再試；詳細資訊已記錄到 Bot log。")
        except Exception:
            _logger.exception("also failed to report the above error back to conversation_id=%s", conversation_id)


async def _run_bot() -> None:
    try:
        await client.start(DISCORD_BOT_TOKEN)
    finally:
        try:
            if not client.is_closed():
                await client.close()
        finally:
            try:
                await scenario_rag.shutdown_prewarm()
            finally:
                try:
                    await async_utils.wait_for_background_tasks(
                        config.PROVIDER_SHUTDOWN_GRACE_SECONDS
                    )
                finally:
                    await providers.shutdown_async_clients()


def main() -> None:
    if not DISCORD_BOT_TOKEN:
        raise SystemExit("尚未設定 DISCORD_BOT_TOKEN，請檢查 .env")
    logging_config.configure_logging()
    asyncio.run(_run_bot())


if __name__ == "__main__":
    main()
