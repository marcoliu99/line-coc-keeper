"""Who is speaking and how an interaction is observed: the conversation id of a channel, the server facts the router
needs for permissions, and the timing wrapper around a button/interaction callback.

Split out of app/discord_bot.py unchanged.
"""
from __future__ import annotations

import functools
import logging
import time
from collections.abc import Sequence

import discord

from app import (
    config,
    observability,
)
from app.commands import permissions
from app.config import (
    LOG_SLOW_REQUEST_MS,
)
from app.discord_transport import delivery
from app.models import GroupState
from app.services import mutation_admission

_logger = logging.getLogger(__name__)


def channel_conversation_id(channel_id: int) -> str:
    return f"discord-channel-{channel_id}"


def observed_interaction(callback):
    """Give persistent Discord buttons the same request lifecycle as messages."""
    @functools.wraps(callback)
    async def wrapped(self, interaction: discord.Interaction):
        channel_id = getattr(interaction.channel, "id", None)
        conversation_id = channel_conversation_id(channel_id) if channel_id is not None else None
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
                await delivery.send_interaction_message(interaction, mutation_admission.NOTICE, ephemeral=True)
            except Exception as exc:
                if observed:
                    observability.event(
                        "request.failed", level=logging.ERROR,
                        duration_ms=(time.perf_counter() - started) * 1000,
                        error_type=type(exc).__name__, status="error",
                        **delivery.request_metrics(),
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
                        **delivery.request_metrics(),
                    )
    return wrapped


def can_manage_server(member: discord.abc.User) -> bool:
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


def server_facts(author: discord.abc.User, mentions: Sequence[discord.abc.User]) -> permissions.ServerFacts:
    """What Discord says about a message: the author's Manage Server permission
    and which mentioned users are members of this server (a Member has a guild)."""
    members = [u for u in mentions if getattr(u, "guild", None) is not None]
    return permissions.ServerFacts(
        can_manage_server=can_manage_server(author),
        member_ids=frozenset(str(u.id) for u in members),
        bot_user_ids=frozenset(str(u.id) for u in members if u.bot),
    )


def formerly_role_gated(parts: list[str]) -> str | None:
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


def note_ignored_keeper_role(member: discord.abc.User, state: GroupState, action: str | None) -> None:
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
