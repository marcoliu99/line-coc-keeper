"""Who may do what: KP authority comes only from being the group's KP Assistant.

No Discord role grants it. A role named `keeper` used to, which made it a
hidden superuser (docs/specs/bug/keeper_role_superuser_design_spec.md); on our
servers that role is the bot's own.
"""
from __future__ import annotations

import re
from typing import Any

from app import config

_MENTION = re.compile(r"^<@!?(\d+)>$")

KP_ONLY_MESSAGE = "只有目前的 KP 助手可以{action}。"


def is_kp(state: Any, user_id: str) -> bool:
    """Whether `user_id` is this group's registered KP Assistant."""
    return bool(state.kp_assistant_user_id) and state.kp_assistant_user_id == user_id


def may_manage_scenario_lifecycle(state: Any, user_id: str) -> bool:
    """Scenario lifecycle actions are open to everyone unless SCENARIO_LIFECYCLE_KP_ONLY."""
    return not config.SCENARIO_LIFECYCLE_KP_ONLY or is_kp(state, user_id)


def kp_only(action: str) -> str:
    """The refusal for an action only the KP Assistant may take."""
    return KP_ONLY_MESSAGE.format(action=action)


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
        return "KP 助手與調查員角色互斥；這位成員已經有調查員角色。"
    if user_id in state.creation_sessions:
        return "KP 助手與建角流程互斥；這位成員正在進行互動式建角，請先取消。"
    return None
