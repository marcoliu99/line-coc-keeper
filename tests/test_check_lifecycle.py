"""Player-owned check registration through the public lifecycle interface."""
from app import check_lifecycle
from app.models import Character, GroupState


def test_group_opening_registration_is_atomic_when_one_player_has_luck() -> None:
    state = GroupState(group_id="opening", characters={
        "first": Character(name="一號", owner_id="first"),
        "second": Character(name="二號", owner_id="second"),
    })
    state.pending_luck_decisions["second"] = {"decision_id": "existing", "options": []}
    candidates = {
        owner: {"type": "skill", "skill": "偵查", "skill_value": 50}
        for owner in state.characters
    }

    result = check_lifecycle.register_many(state, candidates)

    assert result["second"].blocker == "pending_luck_decision"
    assert state.pending_checks == {}
    assert state.timeline_id == ""
    assert state.pending_luck_decisions["second"]["decision_id"] == "existing"


def test_group_opening_registration_assigns_distinct_persisted_identities() -> None:
    state = GroupState(group_id="opening", characters={
        "first": Character(name="一號", owner_id="first"),
        "second": Character(name="二號", owner_id="second"),
    })
    candidates = {
        owner: {"type": "skill", "skill": "偵查", "skill_value": 50}
        for owner in state.characters
    }

    result = check_lifecycle.register_many(state, candidates)

    assert all(entry.status == "admitted" for entry in result.values())
    assert result["first"].check_id != result["second"].check_id
    assert all(entry.timeline_id == state.timeline_id for entry in result.values())
    assert set(state.pending_checks) == {"first", "second"}


def test_repeating_a_manual_check_keeps_the_persisted_identity() -> None:
    state = GroupState(group_id="checks", characters={"first": Character(name="一號", owner_id="first")})
    candidate = {"type": "skill", "skill": "偵查", "skill_value": 50,
                 "bonus_dice": 0, "penalty_dice": 0, "difficulty": "regular", "pushed": False}
    first = check_lifecycle.register(state, "first", candidate, duplicate="identical")
    second = check_lifecycle.register(state, "first", candidate, duplicate="identical")

    assert first.should_save
    assert second.status == "identical" and not second.should_save
    assert second.check_id == first.check_id
    assert len(state.pending_checks) == 1


def test_cached_autoroll_cannot_override_a_different_luck_decision() -> None:
    state = GroupState(group_id="checks", timeline_id="timeline-1",
                       characters={"first": Character(name="一號", owner_id="first")})
    state.pending_luck_decisions["first"] = {"check_id": "other-check"}
    cached = {"timeline_id": "timeline-1", "check_id": "cached-check", "pending_luck": True}

    assert check_lifecycle.reusable_cached_result(state, "first", cached) is None
    assert check_lifecycle.admit(state, "first").blocker == "pending_luck_decision"


def test_stale_pending_timeline_cannot_be_reused() -> None:
    state = GroupState(group_id="checks", timeline_id="new-timeline",
                       characters={"first": Character(name="一號", owner_id="first")})
    state.pending_checks["first"] = {
        "type": "skill", "skill": "偵查", "skill_value": 50,
        "bonus_dice": 0, "penalty_dice": 0, "difficulty": "regular",
        "pushed": False, "timeline_id": "old-timeline", "check_id": "old-check",
    }
    candidate = {key: value for key, value in state.pending_checks["first"].items()
                 if key not in {"timeline_id", "check_id"}}
    result = check_lifecycle.register(state, "first", candidate, duplicate="identical")
    assert result.status == "blocked"
    assert result.check_id == ""
    assert state.pending_checks["first"]["check_id"] == "old-check"


def test_registration_refuses_an_owner_without_an_investigator() -> None:
    state = GroupState(group_id="checks")
    candidate = {"type": "skill", "skill": "偵查", "skill_value": 50}
    try:
        check_lifecycle.register(state, "missing", candidate)
    except ValueError as error:
        assert "no investigator owns check" in str(error)
    else:
        raise AssertionError("missing owner was admitted")
    assert state.pending_checks == {}
