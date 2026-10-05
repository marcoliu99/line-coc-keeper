"""Who is speaking: the conversation id of a channel and the server facts the router needs for permissions.

Split out of app/discord_bot.py unchanged. Nothing here sends to Discord, so every other transport module may import it.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

import discord

from app import (
    observability,
)
from app.commands import permissions
from app.models import GroupState

_logger = logging.getLogger(__name__)


def channel_conversation_id(channel_id: int) -> str:
    return f"discord-channel-{channel_id}"


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
