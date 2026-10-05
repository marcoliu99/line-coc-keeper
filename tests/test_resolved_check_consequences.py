"""State and identity guarantees for consequences of settled checks."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app import db, resolved_check_consequences, tool_dispatch
from app.checks import events as check_events
from app.commands.handlers import checks as check_commands
from app.keeper_tools import registry
from app.keeper_tools import registry as tool_registry
from app.models import Character, GroupState
from app.repositories import group_state
from app.services import prompt_config

SOURCE = (
    "if successful the player may attempt a Dodge roll to avoid being hit by the bed. "
    "If the investigator is struck by the bed, the fall costs the victim 1D6 + 2 hit points."
)


@pytest.fixture
def game(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr(db, "BACKUP_DIR", tmp_path / "backups")
    db._ensure_tables()
    state = GroupState(group_id="consequences", timeline_id="timeline-a", active=True,
                       scenario_text=SOURCE, characters={
                           "u1": Character(name="Alicia", owner_id="u1", hp=10, hp_max=10,
                                           skills={"偵查": 60, "閃避": 45}),
                           "u2": Character(name="Marco", owner_id="u2", hp=10, hp_max=10),
                       })
    group_state.save_state(state)
    return state


def _origin(state: GroupState, *, success: bool, authorization: dict) -> None:
    state.check_consequence_origins["spot-1"] = {
        "event_id": "spot-1", "check_id": "spot-1", "timeline_id": "timeline-a",
        "owner_id": "u1", "character_id": "legacy-user:u1", "investigator": "Alicia",
        "skill": "偵查", "success": success, "authorizations": [authorization],
    }
    group_state.save_state(state)


def _invoke(state: GroupState, name: str, arguments: dict, *, actor_id: str = "u1") -> dict:
    return tool_dispatch.execute_tool(state, name, arguments, [], [], actor_id=actor_id)


def test_consequence_tools_are_only_offered_to_resolved_followup():
    names = {tool["name"] for tool in tool_registry.TOOLS}
    assert "apply_resolved_check_damage" not in names
    assert "create_triggered_check" not in names
    assert {"apply_resolved_check_damage", "create_triggered_check"} <= registry.RESOLVED_CHECK_FOLLOWUP_TOOL_NAMES


def test_new_dodge_pending_is_not_rewritten_as_original_check_failure():
    result = {"investigator": "Alicia", "skill": "偵查", "roll": 42, "outcome": "成功"}
    narrative = "你發現床正襲來；新的閃避檢定尚未結算，請按檢定按鈕。"
    assert prompt_config.enforce_resolved_check_consistency(
        narrative, result, new_pending_check=True,
    ) == narrative


def test_resolved_damage_roll_and_hp_commit_are_one_idempotent_result(game):
    _origin(game, success=False, authorization={
        "key": "bed:hit", "kind": "damage", "when": "failure",
        "damage_expression": "1d6+2", "damage_type": "impact", "source_quote": SOURCE,
    })
    request = {
        "investigator": "Alicia", "damage_expression": "1d6+2", "damage_type": "impact",
        "source_check_id": "spot-1", "source_event_id": "spot-1",
        "consequence_key": "bed:hit", "cause": "The bed throws Alicia through the window",
    }

    with patch("app.dice.random.randint", return_value=2) as random_roll:
        first = _invoke(game, "apply_resolved_check_damage", request)
    with patch("app.dice.random.randint", side_effect=AssertionError("damage rerolled")):
        second = _invoke(group_state.load_state(game.group_id), "apply_resolved_check_damage", request)

    assert first["ok"] and second["ok"]
    assert first["damage"] == second["damage"] == 4
    assert first["rolls"] == second["rolls"] == [2]
    assert first["hp_after"] == second["hp_after"] == 6
    assert group_state.load_state(game.group_id).get_active_character("u1").hp == 6
    random_roll.assert_called_once_with(1, 6)


def test_damage_rejects_wrong_actor_and_outcome_without_roll(game):
    _origin(game, success=True, authorization={
        "key": "bed:hit", "kind": "damage", "when": "failure",
        "damage_expression": "1d6+2", "damage_type": "impact", "source_quote": SOURCE,
    })
    request = {
        "investigator": "Alicia", "damage_expression": "1d6+2", "damage_type": "impact",
        "source_check_id": "spot-1", "source_event_id": "spot-1",
        "consequence_key": "bed:hit", "cause": "bed strike",
    }
    with patch("app.dice.random.randint", side_effect=AssertionError("unauthorized roll")):
        assert not _invoke(game, "apply_resolved_check_damage", request)["ok"]
        assert not _invoke(game, "apply_resolved_check_damage", request, actor_id="u2")["ok"]
    assert group_state.load_state(game.group_id).get_active_character("u1").hp == 10


def test_successful_spot_hidden_creates_distinct_pending_dodge_even_with_autoroll(game):
    game.autoroll_checks = True
    _origin(game, success=True, authorization={
        "key": "bed:dodge", "kind": "check", "when": "success", "skill": "閃避",
        "difficulty": "regular", "source_quote": SOURCE,
    })
    request = {
        "investigator": "Alicia", "skill": "閃避", "difficulty": "regular",
        "trigger_check_id": "spot-1", "trigger_event_id": "spot-1",
        "trigger_condition": "Spot Hidden success reveals the flying bed",
        "consequence_key": "bed:dodge", "action_context": "察覺床架襲來後閃避",
    }

    first = _invoke(game, "create_triggered_check", request)
    second = _invoke(group_state.load_state(game.group_id), "create_triggered_check", request)
    pending = group_state.load_state(game.group_id).pending_checks["u1"]

    assert first["ok"] and first["pending"]
    assert first["check_id"] == second["check_id"] == pending["check_id"]
    assert pending["check_id"] != "spot-1"
    assert pending["skill"] == "閃避"
    assert pending["source_check_id"] == "spot-1"


def test_triggered_check_cannot_repeat_the_original_skill(game):
    _origin(game, success=True, authorization={
        "key": "bed:repeat", "kind": "check", "when": "success", "skill": "偵查",
        "difficulty": "regular", "source_quote": SOURCE,
    })
    request = {
        "investigator": "Alicia", "skill": "偵查", "difficulty": "regular",
        "trigger_check_id": "spot-1", "trigger_event_id": "spot-1",
        "trigger_condition": "repeat Spot Hidden", "consequence_key": "bed:repeat",
        "action_context": "重新偵查",
    }

    assert not _invoke(game, "create_triggered_check", request)["ok"]
    assert not group_state.load_state(game.group_id).pending_checks


def test_manual_roll_carries_source_plan_through_luck_to_settled_origin(game):
    game.characters["u1"].luck = 50
    group_state.save_state(game)
    plan = {
        "key": "bed:dodge", "kind": "check", "when": "success", "skill": "閃避",
        "difficulty": "regular", "source_quote": SOURCE,
    }
    registered = _invoke(game, "skill_check", {
        "investigator": "Alicia", "skill": "偵查", "consequences": [plan],
    })
    assert registered["ok"] and registered["pending"]
    with patch("app.dice.roll_percentile_with_dice_pool", return_value=59):
        waiting = check_commands.resolve_check(game.group_id, "u1", "/coc check")
    assert not waiting.should_finalize
    assert group_state.load_state(game.group_id).pending_luck_decisions["u1"]["consequences"][0]["key"] == "bed:dodge"
    settled = check_commands.resolve_luck(game.group_id, "u1", "skip")
    assert settled.should_finalize
    assert settled.resolved_event["success"] is True
    assert settled.resolved_event["consequences"][0]["key"] == "bed:dodge"
    check_events.persist_consequence_origin(game.group_id, settled.resolved_event)
    saved = group_state.load_state(game.group_id)
    assert saved.check_consequence_origins[settled.resolved_event["event_id"]]["owner_id"] == "u1"


def test_source_plan_rejects_invented_damage_and_old_timeline(game):
    with pytest.raises(ValueError, match="骰式未出現"):
        resolved_check_consequences.normalize_authorizations(game, [{
            "key": "bed:hit", "kind": "damage", "when": "failure",
            "source_quote": SOURCE, "damage_type": "impact", "damage_expression": "5d10",
        }])
    _origin(game, success=False, authorization={
        "key": "bed:hit", "kind": "damage", "when": "failure",
        "damage_expression": "1d6+2", "damage_type": "impact", "source_quote": SOURCE,
    })
    game.timeline_id = "timeline-b"
    group_state.save_state(game)
    request = {
        "investigator": "Alicia", "damage_expression": "1d6+2", "damage_type": "impact",
        "source_check_id": "spot-1", "source_event_id": "spot-1",
        "consequence_key": "bed:hit", "cause": "bed strike",
    }
    with patch("app.dice.random.randint", side_effect=AssertionError("stale roll")):
        assert not _invoke(game, "apply_resolved_check_damage", request)["ok"]


def test_major_wound_damage_and_con_check_commit_together(game):
    _origin(game, success=False, authorization={
        "key": "bed:hit", "kind": "damage", "when": "failure",
        "damage_expression": "1d6+2", "damage_type": "impact", "source_quote": SOURCE,
    })
    request = {
        "investigator": "Alicia", "damage_expression": "1d6+2", "damage_type": "impact",
        "source_check_id": "spot-1", "source_event_id": "spot-1",
        "consequence_key": "bed:hit", "cause": "bed strike",
    }
    with patch("app.dice.random.randint", return_value=3):
        result = _invoke(game, "apply_resolved_check_damage", request)
    saved = group_state.load_state(game.group_id)
    assert result["ok"] and result["major_wound"] is True
    assert saved.characters["u1"].hp == 5
    assert saved.pending_checks["u1"]["skill"] == "CON"
    with patch("app.dice.random.randint", side_effect=AssertionError("rerolled")):
        retry = _invoke(saved, "apply_resolved_check_damage", request)
    assert retry["damage"] == 5
    assert group_state.load_state(game.group_id).pending_checks["u1"]["check_id"] == saved.pending_checks["u1"]["check_id"]


def test_autoroll_plan_runs_restricted_narrator_without_releasing_mutation_lock(game):
    import asyncio
    from unittest.mock import AsyncMock

    from app.agents import reply_pipeline, supervisor
    from app.domain.models import AgentMessage, MechanicResult, StateDelta

    game.autoroll_checks = True
    game.characters['u1'].luck = 0
    group_state.save_state(game)
    context = AgentMessage({'state': game, 'user_id': 'u1', 'display_name': 'Alicia',
                            'speaker_role': 'player', 'text': '避開床', 'resolved_location': None})

    async def execute(_message):
        _invoke(game, 'skill_check', {'investigator': 'Alicia', 'skill': '閃避',
            'consequences': [{'key': 'bed:hit', 'kind': 'damage', 'when': 'failure',
                'damage_expression': '1d6+2', 'damage_type': 'impact', 'source_quote': SOURCE}]})
        return MechanicResult(False, 'check', [], StateDelta())

    async def narrate(message):
        assert message.payload['turn_kind'] == 'resolved_check_followup'
        origin = message.payload['resolved_check_context']
        result = _invoke(game, 'apply_resolved_check_damage', {
            'investigator': 'Alicia', 'source_check_id': origin['check_id'],
            'source_event_id': origin['event_id'], 'consequence_key': 'bed:hit',
            'damage_expression': '1d6+2', 'damage_type': 'impact', 'cause': 'bed strike'})
        assert result['ok']
        return '床撞中你，傷害已結算。', [], []

    handoff = AsyncMock()
    with (patch.object(supervisor.context_builder, 'build_context', AsyncMock(return_value=context)),
          patch.object(supervisor.executor, 'run_executor', side_effect=execute),
          patch.object(supervisor.narrator, 'run_narrator', side_effect=narrate),
          patch.object(reply_pipeline.guard, 'enforce_narrative_safety', AsyncMock(side_effect=lambda _, text: text)),
          patch('app.dice.random.randint', side_effect=[9, 9, 2])):
        asyncio.run(supervisor.run_turn(game, 'u1', 'Alicia', '我要閃避', None, 'player', game.group_id, handoff=handoff))
    assert group_state.load_state(game.group_id).characters['u1'].hp == 6
    handoff.to_narration.assert_not_called()
