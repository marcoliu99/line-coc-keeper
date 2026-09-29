"""Narrator facts come from final state and validated resolution."""

from app.domain.models import MechanicResult, StateDelta, TurnResolution
from app.models import Character, GroupState
from app.services import turn_handoff


def _state() -> GroupState:
    state = GroupState(group_id="handoff", timeline_id="timeline")
    state.characters["a"] = Character(name="Ada", owner_id="a")
    state.characters["b"] = Character(name="Ben", owner_id="b")
    return state


def _result(disposition: str, waiting_for: str = "", check_id: str = "") -> MechanicResult:
    return MechanicResult(
        True, "test", [], StateDelta(),
        check_status={"pending": {"check_id": "stale"}, "pending_luck": None, "resolved": {"roll": 42},
                      "tool_event_count": 1},
        turn_resolution=TurnResolution(disposition=disposition, waiting_for=waiting_for,
                                       check_id=check_id, validation_code="validated"),
    )


def test_referenced_other_player_check_overrides_stale_tool_observation() -> None:
    state = _state()
    state.pending_checks["a"] = {"check_id": "old", "timeline_id": "timeline"}
    state.pending_checks["b"] = {"check_id": "new", "timeline_id": "timeline",
                                  "action_context": "閃避攻擊"}
    result = _result("await_check", "legacy-user:b", "new")

    turn_handoff.prepare_narrator_handoff(state, "a", result,
                                         {"a": dict(state.pending_checks["a"])}, {}, {})

    assert result.check_status["pending"]["check_id"] == "new"
    assert result.check_status["pending"]["investigator"] == "Ben"
    assert result.check_status["waiting_for_name"] == "Ben"
    assert len(result.check_status["current_turn_state"]["pending_checks"]) == 2


def test_luck_for_selected_owner_takes_priority_without_hiding_other_player_state() -> None:
    state = _state()
    state.pending_checks["b"] = {"check_id": "check", "timeline_id": "timeline"}
    state.pending_luck_decisions["b"] = {"decision_id": "luck", "timeline_id": "timeline", "roll": 52}
    result = _result("await_luck", "legacy-user:b", "luck")

    before = {owner: dict(check) for owner, check in state.pending_checks.items()}
    turn_handoff.prepare_narrator_handoff(state, "a", result, before, {}, {})

    assert result.check_status["pending"] is None
    assert result.check_status["pending_luck"]["decision_id"] == "luck"
    assert result.check_status["resolved"] is None
    assert result.check_status["current_turn_state"]["pending_checks"][0]["check_id"] == "check"


def test_existing_actor_check_has_direct_reply_only_when_unchanged() -> None:
    state = _state()
    pending = {"check_id": "old", "timeline_id": "timeline"}
    state.pending_checks["a"] = pending
    result = _result("await_check", "legacy-user:a", "old")
    result.turn_resolution.actor_character_id = "legacy-user:a"
    result.check_status["tool_event_count"] = 0
    result.check_status["state_changed"] = False

    reply = turn_handoff.prepare_narrator_handoff(state, "a", result, {"a": dict(pending)}, {}, {})

    assert "上一筆檢定" in reply
    assert result.check_status["pending"]["check_id"] == "old"
    assert turn_handoff.prepare_narrator_handoff(state, "a", result, {}, {}, {}) == ""


def test_incomplete_claim_cannot_select_a_different_waiting_owner() -> None:
    state = _state()
    state.pending_checks["a"] = {"check_id": "actor", "timeline_id": "timeline"}
    state.pending_checks["b"] = {"check_id": "other", "timeline_id": "timeline"}
    result = _result("incomplete", "legacy-user:b", "other")
    result.turn_resolution.validation_code = "pending_identity_mismatch"

    before = {owner: dict(check) for owner, check in state.pending_checks.items()}
    turn_handoff.prepare_narrator_handoff(state, "a", result, before, {}, {})

    assert result.check_status["pending"]["check_id"] == "actor"
    assert "waiting_for_name" not in result.check_status


def test_owner_lookup_uses_character_identity_and_legacy_fallback() -> None:
    state = _state()
    assert turn_handoff.owner_for_character(state, "legacy-user:b") == "b"
    state.characters["b"].character_id = "canonical-b"
    assert turn_handoff.owner_for_character(state, "canonical-b") == "b"
    assert turn_handoff.owner_for_character(state, "unknown") is None
