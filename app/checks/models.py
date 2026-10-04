"""What a settled step of a player check hands back to its adapter."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services import turn_delivery


@dataclass
class CheckOutcome:
    """The result of one player roll or Luck decision.

    ``reply_text`` set means "say this and stop" (a refusal, or a Luck prompt);
    ``should_finalize`` means the check settled and the Keeper should narrate it.
    ``changed`` tells the transaction whether anything was written: a refusal
    leaves the pending entry exactly as it was.
    """

    roll_line: str = ""
    keeper_message: str = ""
    roll_feedback_text: str = ""
    keeper_header: str = ""
    reply_text: str = ""
    should_finalize: bool = False
    check_id: str = ""
    decision_id: str = ""
    timeline_id: str = ""
    action_context: str = ""
    resolved_event: dict[str, Any] | None = None
    visibility: str = "public"
    recipient_id: str = ""
    changed: bool = False
    # Why the state is being saved (for the commit log); empty means "check".
    save_reason: str = ""


def audience(owner_id: str, entry: dict | None) -> dict[str, str]:
    private = turn_delivery.is_private(entry or {})
    return {
        "visibility": "player_private" if private else "public",
        "recipient_id": owner_id if private else "",
    }


def outcome_for(owner_id: str, entry: dict | None, **fields: Any) -> CheckOutcome:
    """Build an outcome addressed to whoever the pending entry was addressed to."""
    addressed = audience(owner_id, entry)
    outcome = CheckOutcome(**fields, visibility=addressed["visibility"], recipient_id=addressed["recipient_id"])
    if outcome.resolved_event is not None:
        outcome.resolved_event.update(addressed)
    return outcome
