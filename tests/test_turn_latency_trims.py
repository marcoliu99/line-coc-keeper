"""What every combat-turn request no longer carries
(docs/specs/enhancement/combat_prompt_and_router_latency_design_spec.md)."""
from __future__ import annotations

import json

from app.keeper_tools import registry
from app.models import GroupState
from app.services import combat_actions as act
from app.services import combat_engine, turn_context
from tests.test_combat_engine import (  # noqa: F401
    _battle,
    _enemy_turn,
    _load,
    _player,
    _tool,
    database,
)


def test_an_idle_conversation_projects_no_battle():
    assert turn_context.combat_projection(GroupState(group_id="idle")) == {"active": False}


def test_the_projection_keeps_this_round_and_drops_the_history():
    _battle()
    _enemy_turn([20])
    _player("/coc check 閃避", [90])
    _tool("advance_combat_turn", {"actor_id": "Cultist"})
    state = _load()
    # Push the battle into round 2 so round 1 is history.
    state.combat.round_number = 2
    state.combat.plans["old"] = {"plan_id": "old", "round_number": 1, "resolved": False}
    state.combat.plans["now"] = {"plan_id": "now", "round_number": 2, "resolved": False}
    state.combat.actions["unfinished"] = {"action_id": "unfinished", "completed": False, "round": 1}
    projection = turn_context.combat_projection(state)
    full = state.combat.to_dict()
    assert "roll_receipts" not in projection and "baseline_resources" not in projection and "events" not in projection
    assert projection["recent_events"] and all(set(e) == {"event_id", "kind", "revision", "reason"} for e in projection["recent_events"])
    assert projection["event_count"] == len(full["events"])
    assert set(projection["plans"]) == {"now"}
    assert "unfinished" in projection["actions"]
    assert all(a.get("completed") is not True or a.get("round") == 2 or a.get("kind") == "obligation"
               for a in projection["actions"].values())
    assert projection["enemy_cards"] == full["enemy_cards"] and projection["order"] == full["order"]
    assert projection["working_resources"] == full["working_resources"]
    assert len(json.dumps(projection, ensure_ascii=False)) < len(json.dumps(full, ensure_ascii=False))
    assert "roll_receipts" not in turn_context.authority_block(state)


def test_the_projection_keeps_what_the_keeper_must_copy_into_tools():
    _battle()
    run, _ = _enemy_turn([20])
    projection = turn_context.combat_projection(_load())
    assert projection["combat_id"] == run["combat_id"] and projection["interaction"]["interaction_id"] == run["interaction"]["interaction_id"]
    assert run["action_id"] in projection["actions"]
    assert all(p["plan_id"] for p in projection["plans"].values())


def test_descriptions_no_longer_send_the_keeper_to_tools_a_battle_refuses():
    assert "apply_combat_damage" not in registry.REGISTRY["adjust_character"].schema["description"]
    for name in ("damage_combatant", "apply_combat_damage", "apply_final_combat_damage", "resolve_enemy_action",
                 "add_combat_effect"):
        assert registry.REGISTRY[name].schema["description"].startswith("【正式戰鬥中會被拒絕")
    state = GroupState(group_id="idle")
    combat_engine.handle(state, act.Status())  # importable and callable: the tools themselves stay dispatchable
