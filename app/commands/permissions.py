"""Who may do what: KP authority comes only from being the group's KP Assistant.

No Discord role grants it. A role named `keeper` used to, which made it a
hidden superuser (docs/specs/bug/keeper_role_superuser_design_spec.md); on our
servers that role is the bot's own.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app import config

_MENTION = re.compile(r"^<@!?(\d+)>$")

_KP_ONLY_MESSAGE = "只有目前的 KP 助手可以{action}。"


@dataclass(frozen=True)
class ServerFacts:
    """What the chat server says about a message, for seating a KP Assistant.

    `can_manage_server` is Discord's Manage Server permission, the only thing
    that lets a member take over the seat. `member_ids` are the server members
    the message @-mentions: only they can be handed the seat, so a typed
    `<@id>` for someone outside the server never can. `bot_user_ids` are the
    mentioned bots, who can never hold it.
    """

    can_manage_server: bool = False
    member_ids: frozenset[str] = frozenset()
    bot_user_ids: frozenset[str] = frozenset()


NO_SERVER_FACTS = ServerFacts()  # a DM, or a caller that isn't a chat server


def is_kp(state: Any, user_id: str) -> bool:
    """Whether `user_id` is this group's registered KP Assistant."""
    return bool(state.kp_assistant_user_id) and state.kp_assistant_user_id == user_id


def may_manage_scenario_lifecycle(state: Any, user_id: str) -> bool:
    """Scenario lifecycle actions are open to everyone unless SCENARIO_LIFECYCLE_KP_ONLY."""
    return not config.SCENARIO_LIFECYCLE_KP_ONLY or is_kp(state, user_id)


def kp_only(action: str) -> str:
    """The refusal for an action only the KP Assistant may take."""
    return _KP_ONLY_MESSAGE.format(action=action)


def mentioned_user_id(token: str) -> str | None:
    """The user id in a Discord mention such as `<@123>` or `<@!123>`."""
    match = _MENTION.match(token.strip())
    return match.group(1) if match else None


def kp_seat_blocker(state: Any, user_id: str, *, is_bot: bool = False) -> str | None:
    """Why `user_id` can't hold the KP Assistant seat, or None.

    The KP sees private scenario data and can roll back and act for others,
    so the seat excludes anyone playing an investigator or creating one.
    """
    if is_bot:
        return "機器人不能擔任 KP 助手。"
    if state.get_active_character(user_id) is not None:
        return "KP 助手與調查員角色互斥；已經有調查員角色的成員不能擔任 KP 助手。"
    if user_id in state.creation_sessions:
        return "KP 助手與建角流程互斥；正在互動式建角的成員要先輸入「/coc create cancel」取消。"
    return None
