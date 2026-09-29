"""Private message delivery requested by the Keeper."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app import observability, spoiler_policy

if TYPE_CHECKING:
    from app.keeper_tools.registry import ToolCall


def send_private_info(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    char = keeper.find_character(call.state, call.input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{call.input.get('investigator')}」"}
    if not spoiler_policy.is_privacy_isolation_enabled():
        # Still deliver privately: there is no safe public fallback here.
        observability.event(
            "privacy.isolation.disabled", level=logging.WARNING, fn="send_private_info",
        )
    call.private_messages.append((char.owner_id, call.input["message"]))
    return {"ok": True, "delivered_to": char.name}
