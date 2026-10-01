"""A finished fight keeps enough engine evidence for narration-only corrections."""

from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import db, dice, keeper, spoiler_policy
from app.keeper_tools import registry
from app.models import GroupState
from app.repositories import group_state


def _mutate(current: GroupState, callback: Callable[[GroupState], object]) -> object:
    result = callback(current)
    return result.value if isinstance(result, keeper.ToolStateMutation) else result



def _tool(state, name, arguments, *, speaker_role='player'):
    return registry.REGISTRY[name].handler(registry.ToolCall(state, arguments, [], [], speaker_role, name))


def _reviewed_lethal_hit(state):
    target = next(p for p in state.combat.order if p.side == 'enemy')
    with patch.object(dice.random, 'randint', return_value=10):
        return _tool(state, 'declare_combat_effect', {
            'combat_id': state.combat.combat_id, 'effect_id': 'reviewed:lethal',
            'target_id': target.combatant_id, 'severity_id': 'severe', 'scope': 'incident',
            'stop_condition': 'single reviewed incident completed', 'reason': 'reviewed severe damage table'})


def _settle(state):
    preview = _tool(state, 'end_combat', {})
    assert state.combat.active  # The historical end tool now requests only a preview.
    return _tool(state, 'confirm_combat_settlement', {
        'combat_id': state.combat.combat_id, 'settlement_id': preview['preview']['settlement_id'],
        'reason': 'bot controller confirms reviewed settlement'})

def test_post_lethal_status_preserves_damage_evidence_without_public_enemy_hp() -> None:
    state = GroupState(group_id="combat-evidence", timeline_id="timeline-a")

    with (
        patch.object(keeper.mutation_admission, "assert_admitted"),
        patch.object(keeper, "mutate_tool_state", side_effect=_mutate),
        patch.object(keeper, "refresh_tool_state"),
    ):
        assert _tool(state, "start_combat", {})["ok"]
        assert _tool(
            state, "add_npc_to_combat", {"name": "Corbitt", "dex": 50, "hp": 8},
        )["ok"]
        damage = _reviewed_lethal_hit(state)
        assert damage["ok"] and damage["defeated"]
        assert _settle(state)["ok"]

        restored = GroupState.from_dict(state.to_dict())
        player = _tool(restored, "get_combat_status", {})
        kp = _tool(
            restored, "get_combat_status", {}, speaker_role="kp_assistant",
        )
        with patch.object(spoiler_policy.config, "PRIVACY_ISOLATION_ENABLED", False):
            relaxed = _tool(restored, "get_combat_status", {})

    assert not restored.combat.active
    assert player["last_ended_combat"]["last_damage"]["target"] == "Corbitt"
    assert player["last_ended_combat"]["last_damage"]["defeated"] is True
    assert "hp_after" not in player["last_ended_combat"]["last_damage"]
    assert "hp" not in player["last_ended_combat"]["combatants"][0]
    assert kp["last_ended_combat"]["last_damage"]["hp_after"] == 0
    assert relaxed["last_ended_combat"]["last_damage"]["hp_after"] == 0


def test_new_combat_or_timeline_does_not_reuse_previous_evidence() -> None:
    state = GroupState(group_id="combat-evidence-reset", timeline_id="timeline-a")
    with (
        patch.object(keeper.mutation_admission, "assert_admitted"),
        patch.object(keeper, "mutate_tool_state", side_effect=_mutate),
        patch.object(keeper, "refresh_tool_state"),
    ):
        _tool(state, "start_combat", {})
        _tool(state, "add_npc_to_combat", {"name": "Corbitt", "dex": 50, "hp": 8})
        _reviewed_lethal_hit(state)
        _settle(state)
        state.timeline_id = "timeline-b"
        assert "last_ended_combat" not in _tool(state, "get_combat_status", {})
        state.timeline_id = "timeline-a"
        _tool(state, "start_combat", {})
        assert "last_ended_combat" not in _tool(state, "get_combat_status", {})


def test_end_combat_does_not_relabel_damage_from_another_scenario() -> None:
    state = GroupState(group_id="combat-evidence-scenario", timeline_id="timeline-a", scenario_title="Old")
    with (
        patch.object(keeper.mutation_admission, "assert_admitted"),
        patch.object(keeper, "mutate_tool_state", side_effect=_mutate),
        patch.object(keeper, "refresh_tool_state"),
    ):
        _tool(state, "start_combat", {})
        _tool(state, "add_npc_to_combat", {"name": "Corbitt", "dex": 50, "hp": 8})
        _reviewed_lethal_hit(state)
        state.scenario_title = "New"
        _settle(state)
        status = _tool(state, "get_combat_status", {})

    assert status["last_ended_combat"]["last_damage"] == {}


def test_completed_combat_receipt_survives_real_state_save() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory)
        with patch.object(db, "DB_PATH", path / "state.db"), patch.object(db, "BACKUP_DIR", path / "backups"):
            db._ensure_tables()
            state = GroupState(group_id="combat-evidence-sqlite", timeline_id="timeline-a")
            group_state.save_state(state)
            with patch.object(keeper.mutation_admission, "assert_admitted"):
                _tool(state, "start_combat", {})
                _tool(state, "add_npc_to_combat", {"name": "Corbitt", "dex": 50, "hp": 8})
                _reviewed_lethal_hit(state)
                _settle(state)
                restored = group_state.load_state(state.group_id)
                status = _tool(restored, "get_combat_status", {})

    assert status["last_ended_combat"]["last_damage"]["defeated"] is True
