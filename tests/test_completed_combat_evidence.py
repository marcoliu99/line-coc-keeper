"""A finished fight keeps enough engine evidence for narration-only corrections."""

from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import db, keeper, spoiler_policy
from app.models import GroupState
from app.repositories import group_state


def _mutate(current: GroupState, callback: Callable[[GroupState], object]) -> object:
    result = callback(current)
    return result.value if isinstance(result, keeper.ToolStateMutation) else result


def test_post_lethal_status_preserves_damage_evidence_without_public_enemy_hp() -> None:
    state = GroupState(group_id="combat-evidence", timeline_id="timeline-a")

    with (
        patch.object(keeper.mutation_admission, "assert_admitted"),
        patch.object(keeper, "mutate_tool_state", side_effect=_mutate),
        patch.object(keeper, "refresh_tool_state"),
    ):
        assert keeper._execute_tool(state, "start_combat", {}, [], [])["ok"]
        assert keeper._execute_tool(
            state, "add_npc_to_combat", {"name": "Corbitt", "dex": 50, "hp": 8}, [], [],
        )["ok"]
        damage = keeper._execute_tool(
            state, "apply_combat_damage", {"target": "Corbitt", "raw_damage": 10}, [], [],
        )
        assert damage["ok"] and damage["defeated"]
        assert keeper._execute_tool(state, "end_combat", {}, [], [])["ok"]

        restored = GroupState.from_dict(state.to_dict())
        player = keeper._execute_tool(restored, "get_combat_status", {}, [], [])
        kp = keeper._execute_tool(
            restored, "get_combat_status", {}, [], [], speaker_role="kp_assistant",
        )
        with patch.object(spoiler_policy.config, "PRIVACY_ISOLATION_ENABLED", False):
            relaxed = keeper._execute_tool(restored, "get_combat_status", {}, [], [])

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
        keeper._execute_tool(state, "start_combat", {}, [], [])
        keeper._execute_tool(state, "add_npc_to_combat", {"name": "Corbitt", "dex": 50, "hp": 8}, [], [])
        keeper._execute_tool(state, "apply_combat_damage", {"target": "Corbitt", "raw_damage": 10}, [], [])
        keeper._execute_tool(state, "end_combat", {}, [], [])
        state.timeline_id = "timeline-b"
        assert "last_ended_combat" not in keeper._execute_tool(state, "get_combat_status", {}, [], [])
        state.timeline_id = "timeline-a"
        keeper._execute_tool(state, "start_combat", {}, [], [])
        assert "last_ended_combat" not in keeper._execute_tool(state, "get_combat_status", {}, [], [])


def test_completed_combat_receipt_survives_real_state_save() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory)
        with patch.object(db, "DB_PATH", path / "state.db"), patch.object(db, "BACKUP_DIR", path / "backups"):
            db._ensure_tables()
            state = GroupState(group_id="combat-evidence-sqlite", timeline_id="timeline-a")
            group_state.save_state(state)
            with patch.object(keeper.mutation_admission, "assert_admitted"):
                keeper._execute_tool(state, "start_combat", {}, [], [])
                keeper._execute_tool(state, "add_npc_to_combat", {"name": "Corbitt", "dex": 50, "hp": 8}, [], [])
                keeper._execute_tool(state, "apply_combat_damage", {"target": "Corbitt", "raw_damage": 10}, [], [])
                keeper._execute_tool(state, "end_combat", {}, [], [])
                restored = group_state.load_state(state.group_id)
                status = keeper._execute_tool(restored, "get_combat_status", {}, [], [])

    assert status["last_ended_combat"]["last_damage"]["defeated"] is True
