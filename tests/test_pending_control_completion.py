"""Control publication validates the persisted choice at send time."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.models import GroupState
from app.services import pending_buttons
from app.services.pending_buttons import PendingButtonIntent
from tests.state_store import MemoryTransactions


def _intent(kind: str, entry: dict) -> PendingButtonIntent:
    return PendingButtonIntent(kind, "owner", dict(entry), "Ada", "timeline", None, 0.0)


@pytest.mark.parametrize("kind", ["check", "luck"])
def test_replaced_choice_is_not_sent(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    state = GroupState(group_id="group", timeline_id="timeline")
    collection = state.pending_checks if kind == "check" else state.pending_luck_decisions
    old = {"check_id": "old"} if kind == "check" else {"decision_id": "old"}
    collection["owner"] = {"check_id": "new", "_buttons_posted": True} if kind == "check" else {
        "decision_id": "new", "_buttons_posted": True,
    }
    monkeypatch.setattr(pending_buttons, "load_state", lambda _: state)
    send = AsyncMock()

    asyncio.run(pending_buttons.publish_claimed_buttons("group", [_intent(kind, old)], send))

    send.assert_not_awaited()


def test_failed_send_releases_only_matching_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    state = GroupState(group_id="group", timeline_id="timeline")
    state.pending_checks["owner"] = {"check_id": "old", "_buttons_posted": True}
    monkeypatch.setattr(pending_buttons, "load_state", lambda _: state)
    monkeypatch.setattr(pending_buttons.state_transaction, "amutate", MemoryTransactions(state).amutate)

    asyncio.run(pending_buttons.publish_claimed_buttons(
        "group", [_intent("check", {"check_id": "old"})],
        AsyncMock(side_effect=RuntimeError("Discord failed")),
    ))

    assert "_buttons_posted" not in state.pending_checks["owner"]


def test_cancelled_send_releases_remaining_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    state = GroupState(group_id="group", timeline_id="timeline")
    state.pending_checks["owner"] = {"check_id": "old", "_buttons_posted": True}
    monkeypatch.setattr(pending_buttons, "load_state", lambda _: state)
    monkeypatch.setattr(pending_buttons.state_transaction, "amutate", MemoryTransactions(state).amutate)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(pending_buttons.publish_claimed_buttons(
            "group", [_intent("check", {"check_id": "old"})],
            AsyncMock(side_effect=asyncio.CancelledError()),
        ))

    assert "_buttons_posted" not in state.pending_checks["owner"]


def test_completion_uses_recovery_when_in_lock_claim_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    completion = pending_buttons.ControlCompletion("group", {}, {})
    monkeypatch.setattr(pending_buttons, "try_claim_pending_buttons_locked", AsyncMock(return_value=None))
    send = AsyncMock()
    recover = AsyncMock()

    async def run() -> None:
        await completion.claim_locked()
        await completion.publish(send, recover)

    asyncio.run(run())

    send.assert_not_awaited()
    recover.assert_awaited_once_with({}, {}, None)


def test_completion_publishes_claimed_intent_without_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    intent = _intent("check", {"check_id": "old"})
    completion = pending_buttons.ControlCompletion("group", {}, {})
    monkeypatch.setattr(pending_buttons, "try_claim_pending_buttons_locked", AsyncMock(return_value=[intent]))
    publish = AsyncMock()
    monkeypatch.setattr(pending_buttons, "publish_claimed_buttons", publish)
    send = AsyncMock()
    recover = AsyncMock()

    async def run() -> None:
        await completion.claim_locked()
        await completion.publish(send, recover)

    asyncio.run(run())

    publish.assert_awaited_once_with("group", [intent], send)
    recover.assert_not_awaited()
