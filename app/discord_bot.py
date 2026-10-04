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
import time
import unicodedata
from collections.abc import AsyncIterator, Awaitable, Sequence
from contextlib import asynccontextmanager
from typing import TypeVar, cast

import discord

from app import (
    async_utils,
    config,
    db,
    dice,
    help_actions,
    help_service,
    locks,
    logging_config,
    observability,
    providers,
    scenario_rag,
)
from app.check_identity import (
    compact_identity_token,
    effective_check_id,
    effective_decision_id,
)
from app.commands import permissions
from app.commands import router as command_router
from app.commands import sudo as sudo_policy
from app.commands.handlers.buttons import ButtonIO
from app.commands.handlers.uploads import Upload
from app.commands.types import PdfChoice, Reply, SendImage
from app.config import (
    BACKUP_INTERVAL_MINUTES,
    DISCORD_BOT_TOKEN,
    LOG_SLOW_OPERATION_MS,
    LOG_SLOW_REQUEST_MS,
)
from app.help_registry import HelpAction, HelpPage
from app.models import GroupState
from app.providers import anthropic_provider, gemini_provider, openai_provider
from app.repositories.group_state import StateRevisionConflict
from app.repositories.group_state import load_state as load_group_state
from app.scenario_source_authoring import SourceReadyMessage
from app.services import mutation_admission, pending_buttons, turn_delivery
from app.services.pending_buttons import PendingButtonIntent

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
    # A stale-button check normally runs after the callback has already
    # acknowledged the component with _edit_interaction_view.  Discord only
    # permits one initial response, so use a follow-up in that case instead of
    # trying to send a second response and masking the useful stale-button
    # message with InteractionResponded.
    is_done = getattr(interaction.response, "is_done", None)
    response_is_done = bool(is_done()) if callable(is_done) else False
    if not config.LOG_ENABLED:
        if response_is_done:
            await _discord_operation(interaction.followup.send(text, ephemeral=ephemeral))
        else:
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
        if response_is_done:
            await _discord_operation(interaction.followup.send(text, ephemeral=ephemeral))
        else:
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


def _log_reply_text(text: str) -> None:
    """Plain text log, not a structured event field — same rationale as
    app/keeper.py's search_scenario query log: the structured discord.reply
    span (in both _make_reply and _make_interaction_reply below) only ever
    captures counts/bytes, never what was actually said, so "what story text
    did the Keeper just post to this channel" was previously unanswerable
    from the logs.

    The LOG_TEXT_ENABLED check happens here, at the call site, rather than
    being left to app/logging_config.py's downstream _ChannelFilter:
    - Correctness: configure_logging() skips installing that filter entirely
      when both LOG_ENABLED and LOG_TEXT_ENABLED are false. A host that
      configures its own root handler before we run would still capture
      this record in full despite LOG_TEXT_ENABLED=false, defeating the
      documented opt-out for potentially sensitive story text.
    - Efficiency: even in the normal case, checking here means a disabled
      toggle costs nothing — no LogRecord built, no formatting, nothing
      queued to the background listener thread — on what is now a
      per-reply (not per-rare-search) hot path.
    """
    if config.LOG_TEXT_ENABLED:
        _logger.info("discord_reply text=%r", text)


def _make_reply(channel: discord.abc.Messageable) -> Reply:
    async def send_recorded(chunk: str) -> None:
        state = None
        channel_id = getattr(channel, "id", None)
        if isinstance(channel_id, int):
            try:
                state = await asyncio.to_thread(load_group_state, _conversation_id(channel_id))
            except Exception:
                _logger.exception("Unable to capture correction target timeline")
        sent = await _discord_operation(channel.send(chunk))
        if state is not None and isinstance(getattr(sent, "id", None), int):
            from app.services.narrative_corrections import record_message
            try:
                await asyncio.to_thread(record_message, state, str(sent.id), chunk)
            except Exception:
                _logger.exception("Unable to persist correction target receipt; message already sent")

    async def reply(text: str) -> None:
        channel_id = getattr(channel, "id", None)
        if isinstance(text, SourceReadyMessage) and isinstance(channel_id, int):
            await _discord_operation(channel.send(str(text), view=SourceReadyView(_conversation_id(channel_id), text)))
            return
        _log_reply_text(text)
        chunks = _chunk_text(text)
        if not config.LOG_ENABLED:
            for chunk in chunks:
                await send_recorded(chunk)
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
                await send_recorded(chunk)
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
            observability.event("turn.entry", entry="button")
            observed = config.LOG_ENABLED
            started = time.perf_counter() if observed else 0.0
            if observed:
                observability.event("request.started", platform="discord", message_kind="button")
            try:
                await callback(self, interaction)
            except mutation_admission.MutationHeld:
                await _send_interaction_message(interaction, mutation_admission.NOTICE, ephemeral=True)
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


def _can_manage_server(member: discord.abc.User) -> bool:
    """Discord's own Manage Server permission (the owner and Administrator imply it).

    Only /coc kp takeover uses it; it grants no other authority.
    """
    return bool(getattr(getattr(member, "guild_permissions", None), "manage_guild", False))


# What a role named `keeper` used to unlock, by /coc subcommand; None means
# every use of it (docs/specs/bug/keeper_role_superuser_design_spec.md).
_FORMERLY_ROLE_GATED: dict[str, frozenset[str] | None] = {
    "sudo": None, "checkpoint": None, "checkpoints": None, "rollback": None,
    "digest": None, "digests": None, "index": None, "pdf": None,
    "scenario": frozenset({"source", "template", "cards", "use", "reparse", "cancel", "clean"}),
    "correct": frozenset({"approve", "reject", "hold", "supersede"}),
}


def _server_facts(author: discord.abc.User, mentions: Sequence[discord.abc.User]) -> permissions.ServerFacts:
    """What Discord says about a message: the author's Manage Server permission
    and which mentioned users are members of this server (a Member has a guild)."""
    members = [u for u in mentions if getattr(u, "guild", None) is not None]
    return permissions.ServerFacts(
        can_manage_server=_can_manage_server(author),
        member_ids=frozenset(str(u.id) for u in members),
        bot_user_ids=frozenset(str(u.id) for u in members if u.bot),
    )


def _formerly_role_gated(parts: list[str]) -> str | None:
    """The action named for the transition log if `parts` is a command the role used to unlock."""
    if len(parts) < 2 or parts[0].casefold() != "/coc":
        return None
    sub = parts[1].casefold()
    if sub not in _FORMERLY_ROLE_GATED:
        return None
    actions = _FORMERLY_ROLE_GATED[sub]
    if actions is None:
        return sub
    second = parts[2].casefold() if len(parts) > 2 else ""
    return f"{sub} {second}" if second in actions else None


def _note_ignored_keeper_role(member: discord.abc.User, state: GroupState, action: str | None) -> None:
    """Transition log: a role named `keeper` no longer grants KP authority.

    That role is the bot's own; a human holding it used to be a hidden
    superuser (docs/specs/bug/keeper_role_superuser_design_spec.md). Log the
    attempts the role would have let through for one release, to see who is
    affected, then delete this.
    """
    if action is None or getattr(member, "bot", False) or permissions.is_kp(state, str(getattr(member, "id", ""))):
        return
    if any(getattr(role, "name", "").casefold() == "keeper" for role in getattr(member, "roles", ())):
        observability.event(
            "authz.keeper_role_ignored", action=action,
            user_hash=observability.safe_identifier(str(getattr(member, "id", ""))),
        )


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
        if isinstance(text, SourceReadyMessage) and interaction.channel is not None:
            await _discord_operation(interaction.followup.send(
                str(text), ephemeral=True,
                view=SourceReadyView(_conversation_id(interaction.channel.id), text)))
            return
        _log_reply_text(text)
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


# Code review: this used to be its own independently-maintained copy of
# dice.TIER_ZH, and had silently drifted from app/checks/narration.py's copy
# on "regular" ("一般成功" vs "成功"). Now a plain alias to the single source.
_TIER_ZH_FULL = dice.TIER_ZH
_TIER_ORDER = sorted(dice.TIER_RANK, key=lambda t: dice.TIER_RANK[t])


def _tier_percentage_hint(tier: str, skill_value: int) -> str:
    """The %-under-skill-value a player needs to roll to land a given tier
    — see docs/specs/bug/bug-dodge-counter-tie-and-ranged-mechanics.md
    Delegates the actual threshold to dice.tier_upper_bound() (the same
    formula skill_check() resolves a roll against) rather than
    re-hardcoding skill_value//5 etc. here — code review flagged that a
    second, independent copy of this formula could silently drift from
    what the server actually resolves if the rule ever changes. "critical"
    and fail/fumble aren't skill_value-derived bounds, so those still get
    their own plain-language handling."""
    if tier == "critical":
        return "骰出 01"
    bound = dice.tier_upper_bound(skill_value, tier)
    if bound is not None:
        return f"≤{bound}"
    return "幾乎任何擲骰"


def _defense_choice_hint(check: dict) -> str:
    """Builds the "you need at least tier X (<=Y%)" hint for a pending melee
    Dodge/Fight Back choice, so the button doesn't just show a bare skill %
    that looks like an ordinary (non-opposed) check — see
    docs/specs/bug/bug-dodge-counter-tie-and-ranged-mechanics.md

    Only applies once attacker_tier is already known, which is true for
    melee (rolled up front) but never true for a ranged choice at this
    point — a ranged offer_npc_attack_defense_choice defers the attacker's
    shot until the player's own dive-for-cover roll is in (see keeper.py's
    is_ranged branch), so this naturally returns "" there; a "threshold to
    beat" wouldn't even make sense for ranged since dodging it isn't a tier
    comparison in the first place (§2).

    Dodge needs to only match attacker_tier (a tie favors the defender on a
    Dodge — dice.resolve_opposed's is_counter=False branch), while Fight
    Back needs to strictly beat it (a tie favors the attacker on a Fight
    Back) — these are genuinely different thresholds, not the same number
    with different wording."""
    attacker_tier = check.get("attacker_tier")
    if attacker_tier is None:
        return ""
    attacker_rank = dice.TIER_RANK[attacker_tier]
    lines = []
    for o in check.get("options", []):
        is_counter = dice.is_counter_option(o)
        needed_rank = attacker_rank + 1 if is_counter else attacker_rank
        # Code review: dice.resolve_opposed treats BOTH sides being
        # fail-or-worse as "both_miss", not a defender win — so if the
        # attacker fumbled, attacker_rank+1 lands on "fail" (rank 1), and
        # a Fight Back that only reaches "fail" still resolves to
        # both_miss (no hit landed), not the counterattack actually
        # connecting. Clamp to at least "regular" so this hint doesn't
        # promise the player that "almost any roll" lands a Fight Back —
        # Dodge doesn't need this clamp: both_miss and a defender win both
        # mean "not hit", so a low needed_rank there is still accurate.
        if is_counter and needed_rank <= dice.TIER_RANK["fail"]:
            needed_rank = dice.TIER_RANK["regular"]
        if needed_rank >= len(_TIER_ORDER):
            # A Fight Back option against a Critical attacker is filtered out
            # server-side before this ever renders (see keeper.py's
            # offer_npc_attack_defense_choice) — this is just a defensive
            # skip in case that invariant is ever violated, not an expected path.
            continue
        needed_tier = _TIER_ORDER[needed_rank]
        threshold = _tier_percentage_hint(needed_tier, o["skill_value"])
        comparator = "高於" if is_counter else "達到或高於"
        verb = "才能命中" if is_counter else "才能躲開"
        lines.append(f"選擇「{o['label']}」需要{comparator}「{_TIER_ZH_FULL[needed_tier]}」（{threshold}）{verb}")
    if not lines:
        return ""
    attacker_zh = _TIER_ZH_FULL[attacker_tier]
    return f"對方擲出「{attacker_zh}」。\n   " + "；\n   ".join(lines) + "。"


def _check_button_specs(check: dict) -> list[tuple[str, bool, str]]:
    """Return buttons for legacy checks and pending player choices.

    Ordinary skill/SAN/attack/major-wound checks reach this function when
    autoroll is off (the default), and the button is the player's explicit
    roll trigger. Choice buttons first select the option and then trigger the
    player's selected roll.
    """
    if check.get("type") == "sanity":
        return [("🎲 理智檢定", True, "")]
    if check.get("type") == "choice":
        return [
            (f"選擇並擲 {o['label']}（{o['skill']} {o['skill_value']}%）", False, f"#{index}")
            for index, o in enumerate(check.get("options", []))
        ]
    return [(f"🎲 {check.get('skill', '')}（{check.get('skill_value', 0)}%）", False, "")]


# The trailing option segment can be empty (plain check), a compact choice
# index (new buttons), or a Chinese option label (pre-existing buttons).
# Full persisted IDs are accepted for backwards compatibility; new buttons
# use compact_identity_token because Discord limits custom_id to 100 chars.
_CHECK_BUTTON_ID_TEMPLATE = (
    r"coc_check:(?P<conversation_id>discord-channel-\d+):(?P<owner_id>\d+):"
    r"(?:(?P<check_id>(?:check|legacy-check)-[^:]+|c[0-9a-f]{12}):)?(?P<option>[^:]*)"
)


class CheckButton(discord.ui.DynamicItem[discord.ui.Button], template=_CHECK_BUTTON_ID_TEMPLATE):  # type: ignore[call-arg]
    """A choice button for a pending defensive/action choice.

    Ordinary checks create a button while autoroll is off. Clicking it runs
    the same player-triggered path as typing "/coc check"; autoroll is the
    explicit group-level opt-in exception. Persisted pending checks remain
    supported.

    Registered as a *dynamic* item (client.add_dynamic_items below, matched by
    the custom_id pattern above) rather than a plain per-message View, so it
    keeps working across bot restarts — this project restarts the Discord
    process after nearly every deploy, and a plain View() only lives in this
    process's memory, so a button clicked after a restart would otherwise
    silently fail ("This interaction failed") even though nothing about the
    game state was actually lost.
    """

    def __init__(
        self,
        conversation_id: str,
        owner_id: str,
        label: str,
        danger: bool = False,
        option: str = "",
        check_id: str = "",
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.danger if danger else discord.ButtonStyle.primary,
                custom_id=(
                    f"coc_check:{conversation_id}:{owner_id}:{check_id}:{option}"
                    if check_id
                    else f"coc_check:{conversation_id}:{owner_id}:{option}"
                ),
            )
        )
        self.conversation_id = conversation_id
        self.owner_id = owner_id
        self.option = option
        self.check_id = check_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        danger = item.style == discord.ButtonStyle.danger
        groups = match.groupdict()
        return cls(
            match["conversation_id"], match["owner_id"], item.label or "選擇", danger,
            match["option"], groups.get("check_id") or "",
        )

    @_observed_interaction
    async def callback(self, interaction: discord.Interaction) -> None:
        await command_router.handle_check_button(
            self.conversation_id, str(interaction.user.id), self.owner_id, self.option, self.check_id,
            _button_io(interaction, self.conversation_id, "check"),
        )


def _button_io(interaction: discord.Interaction, conversation_id: str, kind: str) -> ButtonIO:
    """The Discord side of a check/Luck button click (see app/commands/handlers/buttons.py)."""

    def channel() -> discord.abc.Messageable:
        if interaction.channel is None:
            raise RuntimeError(f"{kind} interaction has no messageable channel")
        return cast(discord.abc.Messageable, interaction.channel)

    async def notify(text: str) -> None:
        await _send_interaction_message(interaction, text, ephemeral=True)

    async def acknowledge() -> None:
        channel()
        await _edit_interaction_view(interaction, view=None)

    async def send_image(*args, **kwargs):
        return await _make_send_image(channel())(*args, **kwargs)

    async def restore_buttons(before_pending: dict, before_luck: dict, claimed: list[PendingButtonIntent] | None) -> None:
        if claimed is None:
            await _post_pending_buttons(channel(), conversation_id, before_pending, before_luck)
        else:
            await _send_claimed_button_intents(channel(), conversation_id, claimed)

    return ButtonIO(
        notify=notify, acknowledge=acknowledge, reply=_make_interaction_reply(interaction),
        send_dm=_send_dm, send_image=send_image, send_dm_image=_send_dm_image, restore_buttons=restore_buttons,
    )


async def _send_check_button(
    channel: discord.abc.Messageable,
    conversation_id: str,
    owner_id: str,
    check: dict,
    name: str,
    timeline_id: str,
    public_marker: str | None,
) -> None:
    if turn_delivery.is_private(check):
        recipient = client.get_user(int(owner_id)) or await _discord_operation(client.fetch_user(int(owner_id)))
        if recipient is None:
            raise ValueError("private decision recipient is unavailable")
        channel = recipient
        public_marker = None
    view = discord.ui.View(timeout=None)
    full_check_id = effective_check_id(owner_id, check, timeline_id)
    check_id = compact_identity_token("check", owner_id, full_check_id, timeline_id)
    for label, danger, option in _check_button_specs(check):
        view.add_item(CheckButton(conversation_id, owner_id, label, danger, option, check_id))
    marker = f"{public_marker}\n" if public_marker else ""
    if check.get("type") == "choice":
        hint = _defense_choice_hint(check)
        hint_line = f"{hint}\n" if hint else ""
        prompt = f"{hint_line}請選擇要採取的防守／行動方式，並由你觸發擲骰："
    else:
        prompt = "請按鈕完成你的檢定（或輸入 /coc check）："
    await _send_direct_message(channel, f"{marker}👉 {name}，{prompt}", view=view)


async def _post_check_buttons(
    channel: discord.abc.Messageable,
    conversation_id: str,
    state: GroupState | None,
    before_pending: dict,
    public_marker: str | None = None,
    *,
    sudo_command: sudo_policy.ParsedSudoCommand | None = None,
) -> None:
    """Recovery publisher for checks; service owns claim and send recovery."""
    try:
        async with locks.get_conversation_lock(conversation_id):
            intents = await pending_buttons.claim_pending_buttons_locked(
                conversation_id, before_pending, {}, kinds=frozenset({"check"}),
                public_marker=public_marker, sudo_command=sudo_command,
            )
        await _send_claimed_button_intents(channel, conversation_id, intents)
    except Exception:
        _logger.exception("failed to recover check buttons for conversation_id=%s", conversation_id)


_TIER_ZH = {"regular": "一般成功", "hard": "困難成功", "extreme": "極難成功"}

# choice is restricted to these four literal tokens (app/luck.py's tier names,
# plus "skip") rather than [^:]* — nothing about it is freeform player text.
_LUCK_BUTTON_ID_TEMPLATE = (
    r"coc_luck:(?P<conversation_id>discord-channel-\d+):(?P<owner_id>\d+):"
    r"(?:(?P<decision_id>(?:decision|legacy-decision)-[^:]+|d[0-9a-f]{12}):)?"
    r"(?P<choice>skip|regular|hard|extreme)"
)


class LuckSpendButton(discord.ui.DynamicItem[discord.ui.Button], template=_LUCK_BUTTON_ID_TEMPLATE):  # type: ignore[call-arg]
    """A "花 N 點 Luck → 一般成功" (or "維持目前結果") button posted whenever
    there's at least one tier-improving option the player can afford — not
    just a near-miss, see docs/specs/enhancement/enhancement-luck-buyup-always-offered.md
    — via app/commands/handlers/checks.py's handle_check_command (which decides
    whether to prompt at all) and handle_luck_decision (what clicking one of
    these actually resolves to). Same discord.ui.DynamicItem + timeout=None
    pattern as CheckButton above, for the same reason: survives bot restarts.
    """

    def __init__(
        self, conversation_id: str, owner_id: str, label: str, choice: str,
        danger: bool = False, decision_id: str = "",
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary if danger else discord.ButtonStyle.success,
                custom_id=(
                    f"coc_luck:{conversation_id}:{owner_id}:{decision_id}:{choice}"
                    if decision_id
                    else f"coc_luck:{conversation_id}:{owner_id}:{choice}"
                ),
            )
        )
        self.conversation_id = conversation_id
        self.owner_id = owner_id
        self.choice = choice
        self.decision_id = decision_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        danger = item.style == discord.ButtonStyle.secondary
        groups = match.groupdict()
        return cls(
            match["conversation_id"], match["owner_id"], item.label or "維持目前結果",
            match["choice"], danger, groups.get("decision_id") or "",
        )

    @_observed_interaction
    async def callback(self, interaction: discord.Interaction) -> None:
        await command_router.handle_luck_button(
            self.conversation_id, str(interaction.user.id), self.owner_id, self.choice, self.decision_id,
            _button_io(interaction, self.conversation_id, "luck"),
        )


async def _send_luck_button(
    channel: discord.abc.Messageable,
    conversation_id: str,
    owner_id: str,
    decision: dict,
    name: str,
    timeline_id: str,
    public_marker: str | None,
) -> None:
    if turn_delivery.is_private(decision):
        recipient = client.get_user(int(owner_id)) or await _discord_operation(client.fetch_user(int(owner_id)))
        if recipient is None:
            raise ValueError("private decision recipient is unavailable")
        channel = recipient
        public_marker = None
    view = discord.ui.View(timeout=None)
    full_decision_id = effective_decision_id(owner_id, decision, timeline_id)
    decision_id = compact_identity_token("decision", owner_id, full_decision_id, timeline_id)
    for option in decision["options"]:
        label = f"花 {option['cost']} 點 Luck → {_TIER_ZH[option['tier']]}"
        view.add_item(LuckSpendButton(conversation_id, owner_id, label, option["tier"], decision_id=decision_id))
    view.add_item(LuckSpendButton(conversation_id, owner_id, "維持目前結果", "skip", danger=True, decision_id=decision_id))
    marker = f"{public_marker}\n" if public_marker else ""
    await _send_direct_message(channel, f"{marker}🍀 {name}，要花 Luck 買到更好的結果嗎？", view=view)


async def _post_luck_buttons(
    channel: discord.abc.Messageable,
    conversation_id: str,
    state: GroupState | None,
    before_pending: dict,
    public_marker: str | None = None,
    *,
    sudo_command: sudo_policy.ParsedSudoCommand | None = None,
) -> None:
    """Recovery publisher for Luck; reloads after check sends have completed."""
    try:
        async with locks.get_conversation_lock(conversation_id):
            intents = await pending_buttons.claim_pending_buttons_locked(
                conversation_id, {}, before_pending, kinds=frozenset({"luck"}),
                public_marker=public_marker, sudo_command=sudo_command,
            )
        await _send_claimed_button_intents(channel, conversation_id, intents)
    except Exception:
        _logger.exception("failed to recover Luck buttons for conversation_id=%s", conversation_id)


async def _send_claimed_button_intents(
    channel: discord.abc.Messageable,
    conversation_id: str,
    intents: list[PendingButtonIntent],
) -> None:
    """Render via Discord while the service owns freshness and recovery."""
    await pending_buttons.publish_claimed_buttons(
        conversation_id, intents,
        lambda intent: _send_button_intent(channel, conversation_id, intent),
    )


async def _send_button_intent(
    channel: discord.abc.Messageable, conversation_id: str, intent: PendingButtonIntent,
) -> None:
    renderer = _send_check_button if intent.kind == "check" else _send_luck_button
    await renderer(
        channel, conversation_id, intent.owner_id, intent.entry,
        intent.name, intent.timeline_id, intent.public_marker,
    )


async def _publish_control_completion(
    completion: pending_buttons.ControlCompletion,
    channel: discord.abc.Messageable,
) -> None:
    async def recover(before_pending: dict, before_luck: dict,
                      sudo_command: sudo_policy.ParsedSudoCommand | None) -> None:
        await _post_pending_buttons(
            channel, completion.conversation_id, before_pending, before_luck,
            sudo_command=sudo_command,
        )

    await completion.publish(
        lambda intent: _send_button_intent(channel, completion.conversation_id, intent), recover,
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
    """Recovery path for turns that could not claim controls inside their lock."""
    await _post_check_buttons(
        channel, conversation_id, None, before_pending,
        public_marker, sudo_command=sudo_command,
    )
    # Claim Luck only after check delivery, so a decision changed during that
    # send cannot produce an obsolete Luck button.
    await _post_luck_buttons(
        channel, conversation_id, None, before_luck_pending,
        public_marker, sudo_command=sudo_command,
    )


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

    def __init__(self, conversation_id: str, choice: PdfChoice, label: str) -> None:
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
        # The template's regex accepts only "new"/"fix" (see it above), so
        # this narrows a plain str to PdfChoice rather than re-validating it.
        return cls(match["conversation_id"], cast(PdfChoice, match["choice"]), item.label or "")

    @_observed_interaction
    async def callback(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if channel is None or _conversation_id(channel.id) != self.conversation_id:
            text = "這個 PDF 按鈕不屬於目前頻道。"
            await _send_interaction_message(interaction, text, ephemeral=True)
            return
        state = await asyncio.to_thread(load_group_state, self.conversation_id)
        if not permissions.may_manage_scenario_lifecycle(state, str(interaction.user.id)):
            _note_ignored_keeper_role(interaction.user, state, "pdf_choice")
            text = permissions.kp_only("處理劇本 PDF")
            await _send_interaction_message(interaction, text, ephemeral=True)
            return
        await _edit_interaction_view(interaction, view=None)
        push = _make_reply(cast(discord.abc.Messageable, channel))
        await command_router.handle_pdf_choice_button(
            self.conversation_id, self.choice, str(interaction.user.id), push,
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
    if len(page.path) == 2:
        for execution in help_actions.actions_for(page.path):
            view.add_item(HelpExecuteButton(conversation_id, execution))
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


class SourceReadyView(discord.ui.View):
    """Short-lived, owner-bound links into existing confirmed Help actions."""

    def __init__(self, conversation_id: str, result: SourceReadyMessage):
        super().__init__(timeout=300)
        for key, label in (("template_export", "匯出中文模板"), ("source_use", "選用新版英文")):
            self.add_item(SourceReadyButton(conversation_id, result, key, label))


class SourceReadyButton(discord.ui.Button):
    def __init__(self, conversation_id: str, result: SourceReadyMessage, key: str, label: str):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.conversation_id, self.result, self.key = conversation_id, result, key

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _help_interaction_is_valid(interaction, self.conversation_id, self.result.owner_id):
            return
        state = await asyncio.to_thread(load_group_state, self.conversation_id)
        if not permissions.is_kp(state, str(interaction.user.id)):
            _note_ignored_keeper_role(interaction.user, state, "source_ready")
            await _send_interaction_message(interaction, permissions.kp_only("使用英文來源操作"), ephemeral=True)
            return
        selected = self.result.scenario_id + (" original" if self.key == "source_use" else "")
        await _finish_help_action(interaction, help_actions.BY_KEY[self.key], selected=selected)


_HELP_EXECUTE_ID_TEMPLATE = r"coc_help_run:(?P<conversation_id>discord-channel-\d+):(?P<key>[a-z0-9_]+)"


async def _help_interaction_is_valid(
    interaction: discord.Interaction, conversation_id: str, owner_id: str | None = None
) -> bool:
    channel = interaction.channel
    if channel is None or _conversation_id(channel.id) != conversation_id:
        await _send_interaction_message(interaction, "這個 Help 操作不屬於目前頻道。", ephemeral=True)
        return False
    if owner_id is not None and str(interaction.user.id) != owner_id:
        await _send_interaction_message(interaction, "這個操作屬於另一位使用者，請從 Help 重新開啟。", ephemeral=True)
        return False
    return True


async def _dispatch_help_command(
    interaction: discord.Interaction, action: help_actions.HelpExecution, command: str,
    expected_revision: int | None = None,
    selected: str = "",
) -> None:
    channel = interaction.channel
    if channel is None or not hasattr(channel, "send"):
        return
    message_channel = cast(discord.abc.Messageable, channel)
    conversation_id = _conversation_id(channel.id)
    user_id = str(interaction.user.id)
    state = await asyncio.to_thread(load_group_state, conversation_id)
    if help_service.get_page(state, user_id, action.path).title == "找不到 Help 頁面":
        await _send_interaction_message(interaction, "這個 Help 操作在目前情境已不可用。", ephemeral=True)
        return
    if expected_revision is not None and state.state_revision != expected_revision:
        await _send_interaction_message(interaction, "遊戲狀態已更新，請重新開啟這個 Help 操作。", ephemeral=True)
        return
    if action.source:
        fresh = await asyncio.to_thread(help_actions.options_for, action.source, state, user_id)
        if selected not in {value for _, value in fresh} and not (action.key == "check" and not fresh):
            await _send_interaction_message(interaction, "選項已失效，請重新開啟操作。", ephemeral=True)
            return
    if action.mode == "merge" and any(
        key not in {str(part["key"]) for part in state.staged_pdf_parts}
        for key in selected.split()
    ):
        await _send_interaction_message(interaction, "暫存 PDF 已失效，請重新開啟操作。", ephemeral=True)
        return
    await interaction.response.defer()
    reply = _make_interaction_reply(interaction)
    parts = command.split()
    sudo_command: sudo_policy.ParsedSudoCommand | None = None
    if len(parts) > 1 and parts[0] == "/coc" and parts[1] == "sudo":
        sudo_command, _ = sudo_policy.parse_sudo_command(parts, allow_opaque_target=False)
    completion = pending_buttons.ControlCompletion(
        conversation_id, dict(state.pending_checks), dict(state.pending_luck_decisions), sudo_command,
    )

    async def get_display_name() -> str:
        return getattr(interaction.user, "display_name", str(interaction.user.id))

    _note_ignored_keeper_role(interaction.user, state, _formerly_role_gated(parts))
    try:
        await command_router.handle_text_message(
            conversation_id, user_id, get_display_name, reply, _send_dm,
            _make_send_image(message_channel), _send_dm_image, command,
            lambda owner_id: f"<@{owner_id}>",
            server=permissions.ServerFacts(can_manage_server=_can_manage_server(interaction.user)),
            post_turn_hook=completion.claim_locked,
            expected_revision=expected_revision,
        )
    except StateRevisionConflict:
        await reply("遊戲狀態剛被另一個操作更新，這次指令沒有套用，請再試一次。")
    except Exception:
        _logger.exception("Help command failed: action=%s", action.key)
        await reply("Help 操作發生內部錯誤，請稍後再試。")
    finally:
        await _publish_control_completion(completion, message_channel)


async def _finish_help_action(
    interaction: discord.Interaction, action: help_actions.HelpExecution,
    selected: str = "", fields: tuple[str, ...] = (),
    expected_revision: int | None = None,
) -> None:
    channel = interaction.channel
    if channel is None:
        await _send_interaction_message(interaction, "找不到目前頻道，請重新開啟操作。", ephemeral=True)
        return
    try:
        command = help_actions.build_command(action, selected, fields)
    except ValueError as exc:
        await _send_interaction_message(interaction, str(exc), ephemeral=True)
        return
    if action.confirm:
        state = await asyncio.to_thread(load_group_state, _conversation_id(channel.id))
        if expected_revision is not None and state.state_revision != expected_revision:
            await _send_interaction_message(interaction, "遊戲狀態已更新，請重新開啟這個操作。", ephemeral=True)
            return
        await interaction.response.send_message(
            f"確認執行「{action.label}」{f'（{selected[:160]}）' if selected else ''}？", ephemeral=True,
            view=HelpConfirmView(action, command, str(interaction.user.id),
                                 _conversation_id(channel.id), state.state_revision, selected),
        )
        return
    await _dispatch_help_command(interaction, action, command, expected_revision, selected)


class HelpConfirmView(discord.ui.View):
    def __init__(
        self, action: help_actions.HelpExecution, command: str, owner_id: str,
        conversation_id: str, revision: int, selected: str = "",
    ):
        super().__init__(timeout=300)
        self.action = action
        self.command = command
        self.owner_id = owner_id
        self.conversation_id = conversation_id
        self.revision = revision
        self.selected = selected
        self.used = False
        self._use_lock = asyncio.Lock()

    @discord.ui.button(label="確認執行", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not await _help_interaction_is_valid(interaction, self.conversation_id, self.owner_id):
            return
        async with self._use_lock:
            if self.used:
                await _send_interaction_message(interaction, "這個確認已使用，請重新開啟操作。", ephemeral=True)
                return
            self.used = True
        await _dispatch_help_command(interaction, self.action, self.command,
                                     self.revision, self.selected)

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if await _help_interaction_is_valid(interaction, self.conversation_id, self.owner_id):
            self.used = True
            await interaction.response.edit_message(content="已取消。", view=None)


class HelpCommandModal(discord.ui.Modal):
    def __init__(
        self, action: help_actions.HelpExecution, owner_id: str,
        conversation_id: str, revision: int, selected: str = "",
    ):
        super().__init__(title=action.label[:45], timeout=300)
        self.action = action
        self.owner_id = owner_id
        self.conversation_id = conversation_id
        self.revision = revision
        self.selected = selected
        self.inputs: list[discord.ui.TextInput] = []
        for field in action.fields:
            item: discord.ui.TextInput = discord.ui.TextInput(
                label=field.label[:45], required=field.required,
                style=discord.TextStyle.paragraph if field.paragraph else discord.TextStyle.short,
                max_length=200,
            )
            self.add_item(item)
            self.inputs.append(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _help_interaction_is_valid(interaction, self.conversation_id, self.owner_id):
            return
        await _finish_help_action(
            interaction, self.action, self.selected,
            tuple(item.value for item in self.inputs), self.revision,
        )


class HelpOptionSelect(discord.ui.Select):
    def __init__(self, parent: HelpSelectView, options: list[tuple[str, str]]):
        super().__init__(
            placeholder="選擇一項",
            min_values=1, max_values=1,
            options=[discord.SelectOption(label=label[:100], value=str(parent.page * 25 + index))
                     for index, (label, _) in enumerate(options)],
        )
        self.parent_help = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.parent_help
        if not await _help_interaction_is_valid(interaction, view.conversation_id, view.owner_id):
            return
        selected = view.options[int(self.values[0])][1]
        fresh = await asyncio.to_thread(help_actions.options_for, view.action.source,
                                        load_group_state(view.conversation_id), view.owner_id)
        if selected not in {value for _, value in fresh}:
            await _send_interaction_message(interaction, "選項已失效，請重新開啟操作。", ephemeral=True)
            return
        if view.action.fields:
            await interaction.response.send_modal(HelpCommandModal(
                view.action, view.owner_id, view.conversation_id, view.revision, selected
            ))
        else:
            await _finish_help_action(interaction, view.action, selected, expected_revision=view.revision)


class HelpSelectView(discord.ui.View):
    def __init__(
        self, action: help_actions.HelpExecution, owner_id: str,
        conversation_id: str, revision: int, options: list[tuple[str, str]], page: int = 0,
    ):
        super().__init__(timeout=300)
        self.action = action
        self.owner_id = owner_id
        self.conversation_id = conversation_id
        self.revision = revision
        self.options = options
        self.page = page
        self.add_item(HelpOptionSelect(self, options[page * 25:(page + 1) * 25]))
        self.previous.disabled = page == 0
        self.next.disabled = (page + 1) * 25 >= len(options)

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._page(interaction, self.page - 1)

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._page(interaction, self.page + 1)

    async def _page(self, interaction: discord.Interaction, page: int) -> None:
        if not await _help_interaction_is_valid(interaction, self.conversation_id, self.owner_id):
            return
        if page < 0 or page * 25 >= len(self.options):
            await _send_interaction_message(interaction, "沒有更多選項。", ephemeral=True)
            return
        await interaction.response.edit_message(
            view=HelpSelectView(self.action, self.owner_id, self.conversation_id,
                                self.revision, self.options, page)
        )


class HelpMergeSelect(discord.ui.Select):
    def __init__(self, parent: HelpMergeView):
        available = [(key, name) for key, name in parent.parts if key not in parent.selected]
        page_items = available[parent.page * 25:(parent.page + 1) * 25]
        super().__init__(
            placeholder="依合併順序逐一選擇 PDF",
            options=[discord.SelectOption(label=name[:80], description=key[:12], value=str(i))
                     for i, (key, name) in enumerate(page_items, parent.page * 25)],
        )
        self.parent_help = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.parent_help
        if not await _help_interaction_is_valid(interaction, view.conversation_id, view.owner_id):
            return
        available = [(key, name) for key, name in view.parts if key not in view.selected]
        index = int(self.values[0])
        if index >= len(available):
            await _send_interaction_message(interaction, "選項已失效，請重新開啟操作。", ephemeral=True)
            return
        key = available[index][0]
        state = await asyncio.to_thread(load_group_state, view.conversation_id)
        if state.state_revision != view.revision or key not in {p["key"] for p in state.staged_pdf_parts}:
            await _send_interaction_message(interaction, "暫存 PDF 已更新，請重新開啟操作。", ephemeral=True)
            return
        selected = (*view.selected, key)
        await interaction.response.edit_message(
            content=f"合併順序：{', '.join(k[:12] for k in selected)}",
            view=HelpMergeView(view.action, view.owner_id, view.conversation_id,
                               view.revision, view.parts, selected),
        )


class HelpMergeView(discord.ui.View):
    def __init__(
        self, action: help_actions.HelpExecution, owner_id: str, conversation_id: str,
        revision: int, parts: list[tuple[str, str]], selected: tuple[str, ...] = (), page: int = 0,
    ):
        super().__init__(timeout=300)
        self.action, self.owner_id, self.conversation_id = action, owner_id, conversation_id
        self.revision, self.parts, self.selected, self.page = revision, parts, selected, page
        remaining = len(parts) - len(selected)
        if remaining:
            self.add_item(HelpMergeSelect(self))
        self.previous.disabled = page == 0
        self.next.disabled = (page + 1) * 25 >= remaining
        self.finish.disabled = len(selected) < 2

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._page(interaction, self.page - 1)

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._page(interaction, self.page + 1)

    @discord.ui.button(label="確認順序", style=discord.ButtonStyle.primary)
    async def finish(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not await _help_interaction_is_valid(interaction, self.conversation_id, self.owner_id):
            return
        if len(self.selected) < 2:
            await _send_interaction_message(interaction, "至少選兩個 PDF。", ephemeral=True)
            return
        await _finish_help_action(interaction, self.action, " ".join(self.selected),
                                  expected_revision=self.revision)

    async def _page(self, interaction: discord.Interaction, page: int) -> None:
        if not await _help_interaction_is_valid(interaction, self.conversation_id, self.owner_id):
            return
        if page < 0 or page * 25 >= len(self.parts) - len(self.selected):
            await _send_interaction_message(interaction, "沒有更多選項。", ephemeral=True)
            return
        await interaction.response.edit_message(view=HelpMergeView(
            self.action, self.owner_id, self.conversation_id, self.revision,
            self.parts, self.selected, page,
        ))


_HELP_SUDO_COMMANDS = (
    "act", "away", "back", "characters", "check", "enter", "leavemap",
    "luck skip", "luck regular", "luck hard", "luck extreme", "pregen",
    "pregens", "retire", "setconnection", "setskill", "sheet", "showpage",
    "switch", "where",
)
_HELP_SUDO_NEEDS_ARGS = {"act", "enter", "setconnection", "setskill", "showpage", "switch"}
_HELP_SUDO_OPTIONAL_ARGS = {"check", "pregen", "retire"}


class HelpSudoArgsModal(discord.ui.Modal):
    def __init__(self, view: HelpSudoView, command: str):
        super().__init__(title=f"sudo {command}"[:45], timeout=300)
        self.parent_help = view
        self.command = command
        self.args: discord.ui.TextInput = discord.ui.TextInput(
            label="指令參數", required=command not in _HELP_SUDO_OPTIONAL_ARGS,
            style=discord.TextStyle.paragraph if command == "act" else discord.TextStyle.short,
            max_length=200,
        )
        self.add_item(self.args)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        view = self.parent_help
        if not await _help_interaction_is_valid(interaction, view.conversation_id, view.owner_id):
            return
        suffix = self.args.value.strip()
        command = f"/coc sudo <@{view.target_id}> {self.command}"
        if suffix:
            command += f" {suffix}"
        await _confirm_sudo_help(interaction, view, command)


async def _confirm_sudo_help(interaction: discord.Interaction, view: HelpSudoView, command: str) -> None:
    state = await asyncio.to_thread(load_group_state, view.conversation_id)
    if state.state_revision != view.revision:
        await _send_interaction_message(interaction, "遊戲狀態已更新，請重新開啟操作。", ephemeral=True)
        return
    parsed, error = sudo_policy.parse_sudo_command(command.split())
    if error or parsed is None:
        await _send_interaction_message(interaction, sudo_policy.denial_message(error or "forbidden_command"), ephemeral=True)
        return
    await interaction.response.send_message(
        f"確認代 <@{view.target_id}> 執行 `{parsed.command}`？", ephemeral=True,
        view=HelpConfirmView(view.action, command, view.owner_id, view.conversation_id, view.revision),
    )


class HelpSudoCommandSelect(discord.ui.Select):
    def __init__(self, parent: HelpSudoView):
        super().__init__(
            placeholder="選擇代操作指令",
            options=[discord.SelectOption(label=name, value=name) for name in _HELP_SUDO_COMMANDS],
        )
        self.parent_help = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.parent_help
        if not await _help_interaction_is_valid(interaction, view.conversation_id, view.owner_id):
            return
        command = self.values[0]
        if command in _HELP_SUDO_NEEDS_ARGS | _HELP_SUDO_OPTIONAL_ARGS:
            await interaction.response.send_modal(HelpSudoArgsModal(view, command))
            return
        await _confirm_sudo_help(interaction, view, f"/coc sudo <@{view.target_id}> {command}")


class HelpSudoView(discord.ui.View):
    def __init__(
        self, action: help_actions.HelpExecution, owner_id: str,
        conversation_id: str, revision: int, target_id: str = "",
    ):
        super().__init__(timeout=300)
        self.action, self.owner_id, self.conversation_id = action, owner_id, conversation_id
        self.revision, self.target_id = revision, target_id
        if target_id:
            self.add_item(HelpSudoCommandSelect(self))
        else:
            self.add_item(HelpSudoTargetSelect(self))


class HelpSudoTargetSelect(discord.ui.UserSelect):
    def __init__(self, parent: HelpSudoView):
        super().__init__(placeholder="選擇玩家", min_values=1, max_values=1)
        self.parent_help = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.parent_help
        if not await _help_interaction_is_valid(interaction, view.conversation_id, view.owner_id):
            return
        target_id = str(self.values[0].id)
        await interaction.response.edit_message(
            content=f"代操作目標：<@{target_id}>。請選擇指令。",
            view=HelpSudoView(view.action, view.owner_id, view.conversation_id,
                              view.revision, target_id),
        )


class HelpExecuteButton(discord.ui.DynamicItem[discord.ui.Button], template=_HELP_EXECUTE_ID_TEMPLATE):  # type: ignore[call-arg]
    def __init__(self, conversation_id: str, action: help_actions.HelpExecution):
        super().__init__(discord.ui.Button(
            label=action.label[:80], style=discord.ButtonStyle.success,
            custom_id=f"coc_help_run:{conversation_id}:{action.key}",
        ))
        self.conversation_id = conversation_id
        self.action = action

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        action = help_actions.BY_KEY.get(match["key"])
        if action is None:
            raise ValueError("unknown Help action")
        return cls(match["conversation_id"], action)

    @_observed_interaction
    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _help_interaction_is_valid(interaction, self.conversation_id):
            return
        state = await asyncio.to_thread(load_group_state, self.conversation_id)
        user_id = str(interaction.user.id)
        if help_service.get_page(state, user_id, self.action.path).title == "找不到 Help 頁面":
            await _send_interaction_message(interaction, "這個操作目前不可用。", ephemeral=True)
            return
        if self.action.mode == "direct":
            await _finish_help_action(interaction, self.action, expected_revision=state.state_revision)
        elif self.action.mode == "form":
            await interaction.response.send_modal(HelpCommandModal(
                self.action, user_id, self.conversation_id, state.state_revision,
            ))
        elif self.action.mode == "select":
            options = await asyncio.to_thread(
                help_actions.options_for, self.action.source, state, user_id,
            )
            if not options:
                if self.action.key == "check":
                    await _finish_help_action(interaction, self.action,
                                              expected_revision=state.state_revision)
                    return
                await _send_interaction_message(interaction, "目前沒有可選項目。", ephemeral=True)
                return
            await interaction.response.send_message(
                f"請選擇：{self.action.label}", ephemeral=True,
                view=HelpSelectView(self.action, user_id, self.conversation_id,
                                    state.state_revision, options),
            )
        elif self.action.mode == "merge":
            parts = [(str(p["key"]), str(p["file_name"])) for p in state.staged_pdf_parts]
            if len(parts) < 2:
                await _send_interaction_message(interaction, "至少需要兩個暫存 PDF，請先上傳。", ephemeral=True)
                return
            await interaction.response.send_message(
                "請依合併順序逐一選擇 PDF。", ephemeral=True,
                view=HelpMergeView(self.action, user_id, self.conversation_id,
                                   state.state_revision, parts),
            )
        elif self.action.mode == "sudo":
            await interaction.response.send_message(
                "請選擇代操作的玩家。", ephemeral=True,
                view=HelpSudoView(self.action, user_id, self.conversation_id, state.state_revision),
            )
        else:
            raise AssertionError(f"unknown Help mode: {self.action.mode}")


async def _post_help_page(channel: discord.abc.Messageable, conversation_id: str, user_id: str, path: tuple[str, ...]) -> None:
    state = await asyncio.to_thread(load_group_state, conversation_id)
    page = help_service.get_page(state, user_id, path)
    content = help_service.bounded_page_text(page, MAX_DISCORD_MESSAGE_CHARS)
    await _send_direct_message(channel, content, view=_help_view(conversation_id, page))


client.add_dynamic_items(CheckButton, LuckSpendButton, PdfUploadChoiceButton, HelpButton, HelpExecuteButton)


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


@asynccontextmanager
async def _best_effort_typing(channel: discord.abc.Messageable) -> AsyncIterator[None]:
    """Wraps `channel.typing()` so a Discord-side failure entering or
    exiting it (rate limit, transient network hiccup — `Typing.__aenter__`
    itself makes a real API call, see discord.py's `Typing` class) can
    never block or fail actual message processing. The indicator is a
    nice-to-have UX signal, not a prerequisite — review finding: the naive
    `async with message.channel.typing():` wrapper made every message's
    processing depend on that one API call succeeding first, a brand new
    single point of failure that didn't exist before this indicator was
    added."""
    try:
        typing_cm = channel.typing()
        await typing_cm.__aenter__()
    except Exception:  # noqa: BLE001 - the indicator failing must never block the message it's decorating
        observability.event("discord.typing.failed", level=logging.WARNING)
        typing_cm = None
    try:
        yield
    finally:
        if typing_cm is not None:
            try:
                await typing_cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001 - same reasoning as entering it
                observability.event("discord.typing.exit_failed", level=logging.WARNING)


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
            # Entered before anything else (including the conversation
            # lock) so Discord's typing indicator appears immediately on
            # receipt, not only once processing actually starts — a queued
            # message behind a long-running Keeper turn would otherwise
            # look identical to the bot being dead for tens of seconds.
            # `typing()` is an async context manager that keeps re-sending
            # Discord's ~10s typing signal for as long as the block is open.
            # _best_effort_typing wraps it so a Discord-side failure on the
            # typing indicator itself can never block _handle_message.
            async with _best_effort_typing(message.channel):
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
        uploads = [Upload(a.filename, a.read) for a in message.attachments]
        if await command_router.handle_uploads(
            conversation_id, uploads, reply,
            post_pdf_buttons=lambda: _post_pdf_upload_buttons(message.channel, conversation_id),
        ):
            return

        text = (message.content or "").strip()
        if not text:
            if message.attachments:
                await command_router.handle_unsupported_attachment(conversation_id, reply)
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
        sudo_command: sudo_policy.ParsedSudoCommand | None = None
        if command_parts[0].casefold() == "/coc" and len(command_parts) > 1 and command_parts[1].casefold() == "sudo":
            sudo_command, _ = sudo_policy.parse_sudo_command(command_parts, allow_opaque_target=False)
        completion = pending_buttons.ControlCompletion(
            conversation_id, dict(state_before.pending_checks),
            dict(state_before.pending_luck_decisions), sudo_command,
        )

        try:
            _note_ignored_keeper_role(message.author, state_before, _formerly_role_gated(command_parts))
            await command_router.handle_text_message(
                conversation_id, user_id, get_display_name, reply, _send_dm, send_image, _send_dm_image, text,
                format_mention, post_turn_hook=completion.claim_locked,
                server=_server_facts(message.author, getattr(message, "mentions", ())),
                referenced_message_id=(
                    str(message.reference.message_id)
                    if command_parts[0].casefold() == "/coc"
                    and len(command_parts) > 1
                    and command_parts[1].casefold() == "correct"
                    and message.reference is not None
                    and message.reference.message_id is not None
                    else None
                ),
            )
        finally:
            # Always attempt this, even if handle_text_message raised partway
            # through a turn — a check can already be registered and saved
            # (e.g. skill_check's tool call) before a *later* tool call in the
            # same turn blows up, and that would otherwise silently strand a
            # pending check with no button ever posted for it.
            await _publish_control_completion(completion, message.channel)
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


def warn_if_privacy_isolation_disabled() -> None:
    """spec §12 item #6: doesn't block startup (a KP may have a legitimate
    local/dev reason to flip this), but a silent per-call WARNING deep in
    spoiler_policy is easy to miss — surface it once, loudly, at boot."""
    if config.PRIVACY_ISOLATION_ENABLED:
        return
    _logger.warning(
        "PRIVACY_ISOLATION_ENABLED=false — 玩家私訊、秘密目標、戰鬥隱藏資訊等隱私保護"
        "已全部停用，不建議用於正式營運環境。"
    )
    observability.event(
        "privacy.isolation.disabled", level=logging.WARNING, reason="startup_config"
    )


def main() -> None:
    if not DISCORD_BOT_TOKEN:
        raise SystemExit("尚未設定 DISCORD_BOT_TOKEN，請檢查 .env")
    logging_config.configure_logging()
    warn_if_privacy_isolation_disabled()
    asyncio.run(_run_bot())


if __name__ == "__main__":
    main()
