"""The combat rules as tests call them, with the mode chosen the way the engine chooses it.

Tests of the turn and damage rules drive them directly on a ``GroupState``. The
rules no longer decide which mode a battle is in, so these wrappers hand them
the managed implementation — and refuse, loudly, a battle that was not started
through the production path, the same way ``CombatEngine`` does.
"""
from __future__ import annotations

from typing import Any

from app import combat, combat_flow, combat_resources
from app.models import Character, GroupState
from app.services import combat_actions as act
from app.services import combat_engine


def ops_for(state: GroupState) -> combat.ModeOps:
    """The managed rules for a managed (or idle) state; never a guess for anything else."""
    if not combat_resources.is_managed(state):
        raise AssertionError("test state was not started through combat.begin_combat / start_combat")
    return combat_flow.MANAGED_OPS


def advance_turn(state: GroupState) -> dict[str, Any]:
    return combat.advance_turn(state, ops=ops_for(state))


def process_timing(state: GroupState, timing: str, target_id: str = "") -> list[dict[str, Any]]:
    return combat.process_timing(state, timing, target_id, ops=ops_for(state))


def plan_enemy_turn(state: GroupState, enemy_name: str = "") -> dict[str, Any]:
    return combat_engine.handle(state, act.PlanEnemy(enemy_name))


def resolve_enemy_action(state: GroupState, plan_id: str, outcome: dict[str, Any] | None = None) -> dict[str, Any]:
    return combat_engine.handle(state, act.ResolveEnemy(plan_id=plan_id, outcome=outcome))


def apply_combat_damage(
    state: GroupState, target_name: str, raw_damage: int, *, damage_type: str = "physical",
    tags: list[str] | None = None, source_id: str = "", bypass_armor: bool = False,
    entry_point: str = "apply_combat_damage", event_id: str = "",
) -> dict[str, Any]:
    return combat_engine.handle(state, act.ApplyDamage(
        target=target_name, raw_damage=raw_damage, damage_type=damage_type, tags=tags, source_id=source_id,
        bypass_armor=bypass_armor, entry_point=entry_point, event_id=event_id,
    ))


def apply_final_combat_damage(
    state: GroupState, target_name: str, final_damage: int, *, damage_type: str = "physical",
    tags: list[str] | None = None, source_id: str = "",
) -> dict[str, Any]:
    return apply_combat_damage(
        state, target_name, final_damage, damage_type=damage_type, tags=tags, source_id=source_id,
        bypass_armor=True, entry_point="apply_final_combat_damage",
    )


def damage_combatant(state: GroupState, name: str, delta: int) -> dict[str, Any]:
    return combat_engine.handle(state, act.DamageCombatant(name, delta))


def managed_single_hit(
    state: GroupState, character: Character, damage: int, *, event_id: str, reason: str,
) -> dict[str, Any]:
    return combat_engine.handle(state, act.SingleHit(
        character=character, damage=damage, event_id=event_id, reason=reason,
    ))


def retire_active_character(state: GroupState, owner_id: str, name: str | None = None) -> Character:
    return state.retire_active_character(owner_id, name, finish_turn=combat_engine.finish_retired_turn)
