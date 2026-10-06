"""The one entry point to a battle: ``CombatEngine.handle(state, action)``.

A battle is in one of three modes:

* ``IDLE``    — no battle is running.
* ``MANAGED`` — running under the working-resource pipeline (every battle
  started today): receipts for every roll, resources held as provisional
  ``working_resources`` until a settlement, owned waits for a person's input.
* ``LEGACY``  — a battle saved before that pipeline, with immediate
  persistence. It can still be looked at, advanced and closed explicitly; it is
  never silently converted.

The mode is read **once**, here, when an action arrives, and the matching
implementation is chosen (``combat_flow.MANAGED_OPS`` or ``combat.LEGACY_OPS``
for the rules that differ per mode). The rules in ``combat`` and
``combat_flow`` never ask which mode they are in, and the two modules no longer
import each other: ``combat_flow`` builds on ``combat``, and only this engine
knows both.

Commands, Keeper tools and the check engine's combat port talk to the engine,
so a tool, a button and a retry all reach the same action ledger and the same
settlement entries. One transaction (``state_transaction``) wraps a whole
action, including the check it waits on and any resource change it makes; an
action that needs a person's answer returns with that wait saved, and the
answer arrives as a new action carrying the same identity.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from copy import deepcopy
from enum import Enum
from typing import Any, TypeVar, cast

from app import combat, combat_flow, combat_resources, dice
from app.models import GroupState
from app.services import combat_actions as act

_logger = logging.getLogger(__name__)

R = TypeVar("R")

NO_BATTLE = "目前沒有進行中的戰鬥"
RAW_OUTCOME_REJECTED = (
    "Managed/source-bound combat requires its action or explicit effect runner; "
    "legacy raw outcome is not authoritative"
)


class Mode(str, Enum):
    IDLE = "idle"
    LEGACY = "legacy"
    MANAGED = "managed"


def mode_of(state: GroupState) -> Mode:
    """The mode of the battle in ``state`` right now."""
    if not state.combat.active:
        return Mode.IDLE
    return Mode.MANAGED if combat_resources.is_managed(state) else Mode.LEGACY


def _ops(mode: Mode) -> combat.ModeOps:
    return combat_flow.MANAGED_OPS if mode is Mode.MANAGED else combat.LEGACY_OPS


def _live_managed(state: GroupState, mode: Mode) -> dict[str, Any] | None:
    """Refuse an action that only makes sense inside a running managed battle.

    Without this, asking an idle conversation to run an action used to
    fabricate an empty battle as a side effect. A battle saved before the
    pipeline still raises the admission error it always did.
    """
    if mode is Mode.MANAGED:
        return None
    if mode is Mode.LEGACY:
        raise combat_resources.CombatAdmissionError(combat_resources.LEGACY_NEEDS_ADMISSION)
    return {"ok": False, "error": NO_BATTLE}


def authorize(state: GroupState, combat_id: str, reason: str) -> str:
    """An administrative command must name the current battle and give a reason."""
    if str(combat_id or "") != state.combat.combat_id:
        raise ValueError("Administrative command requires the current combat_id")
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("Explicit controller reason is required")
    return reason


# --------------------------------------------------------------------------
# Handlers. Each takes the state, the action and the mode read for it.
# --------------------------------------------------------------------------

Handler = Callable[[GroupState, Any, Mode], Any]


def _start(state: GroupState, action: act.Start, mode: Mode) -> None:
    combat.begin_combat(state)


def _add_combatant(state: GroupState, action: act.AddCombatant, mode: Mode) -> combat.AddedCombatant:
    return combat.add_combatant(
        state, action.name, action.dex, action.hp, is_ally=action.is_ally, armor=action.armor,
        attacks=action.attacks, abilities=action.abilities, force_new_instance=action.force_new_instance,
        source=action.source, skills=action.skills,
    )


def _status(state: GroupState, action: act.Status, mode: Mode) -> str:
    return combat.status_text(state, action.include_private, provisional=mode is Mode.MANAGED)


def _obligations(state: GroupState, action: act.Obligations, mode: Mode) -> list[Any]:
    return (
        combat_flow.postcombat_obligations(state) if mode is Mode.MANAGED
        else list(state.postcombat_obligations)
    )


def _advance(state: GroupState, action: act.Advance, mode: Mode) -> dict[str, Any]:
    if mode is Mode.MANAGED:
        return combat_flow.advance_combat(state, actor_id=action.actor_id, event_id=action.event_id, skip=action.skip)
    return combat.advance_turn(state, ops=_ops(mode))


def _plan_enemy(state: GroupState, action: act.PlanEnemy, mode: Mode) -> dict[str, Any]:
    return combat.plan_enemy_turn(state, action.enemy_name, ops=_ops(mode))


def _run_enemy_plan(state: GroupState, action: act.RunEnemyPlan, mode: Mode) -> dict[str, Any]:
    return combat_flow.run_enemy_plan(state, action.plan_id)


def _resolve_enemy(state: GroupState, action: act.ResolveEnemy, mode: Mode) -> dict[str, Any]:
    if mode is Mode.MANAGED:
        if action.outcome is not None:
            return {"ok": False, "error": "Managed actions do not accept caller hit/damage results"}
        return combat_flow.run_enemy_plan(state, action.plan_id)
    return combat.resolve_enemy_action(state, action.plan_id, outcome=action.outcome)


def _set_initiative(state: GroupState, action: act.SetInitiative, mode: Mode) -> dict[str, Any]:
    reason = authorize(state, action.combat_id, action.reason)
    return _live_managed(state, mode) or combat_flow.set_initiative(
        state, actor_ids=action.actor_ids, event_id=action.event_id, reason=reason,
    )


def _finish_retired_turn(state: GroupState, action: act.FinishRetiredTurn, mode: Mode) -> None:
    combat.finish_retired_current_turn(
        state, old_order=action.old_order, old_index=action.old_index,
        removed_ids=action.removed_ids, ops=_ops(mode),
    )


def _declare(state: GroupState, action: act.Declare, mode: Mode) -> dict[str, Any]:
    return _live_managed(state, mode) or combat_flow.declare_action(
        state, action_id=action.action_id, actor_id=action.actor_id, target_id=action.target_id,
        weapon_reference=action.weapon_reference, action_kind=action.action_kind,
        distance_yards=action.distance_yards, scenario_definitions=action.scenario_definitions,
        weapon_instance=action.weapon_instance,
    )


def _run(state: GroupState, action: act.Run, mode: Mode) -> dict[str, Any]:
    return _live_managed(state, mode) or combat_flow.run_action(state, action.action_id)


def _choose(state: GroupState, action: act.Choose, mode: Mode) -> dict[str, Any]:
    return combat_flow.submit_choice(
        state, interaction_id=action.interaction_id, owner_id=action.owner_id, choice=action.choice,
    )


def _choice_receipt(state: GroupState, action: act.ChoiceReceipt, mode: Mode) -> dict[str, Any] | None:
    return combat_flow.choice_receipt(
        state, interaction_id=action.interaction_id, owner_id=action.owner_id, choice=action.choice,
    )


def _validate_pending(state: GroupState, action: act.ValidatePending, mode: Mode) -> dict[str, Any]:
    return combat_flow.validate_pending_context(state, action.pending, action.owner_id)


def _roll_pending(state: GroupState, action: act.RollPending, mode: Mode) -> dice.SkillCheckResult:
    return combat_flow.roll_pending_check(state, action.pending, action.owner_id)


def _check_result(state: GroupState, action: act.CheckResult, mode: Mode) -> dict[str, Any]:
    return combat_flow.on_authoritative_check_result(
        state, pending_entry=action.pending_entry, owner_id=action.owner_id, result=action.result,
        final=action.final,
    )


def _apply_damage(state: GroupState, action: act.ApplyDamage, mode: Mode) -> dict[str, Any]:
    return combat.apply_combat_damage(
        state, action.target, action.raw_damage, ops=_ops(mode), damage_type=action.damage_type,
        tags=action.tags, source_id=action.source_id, bypass_armor=action.bypass_armor,
        entry_point=action.entry_point, event_id=action.event_id,
    )


def _damage_combatant(state: GroupState, action: act.DamageCombatant, mode: Mode) -> dict[str, Any]:
    return combat.damage_combatant(state, action.name, action.delta, ops=_ops(mode))


def _single_hit(state: GroupState, action: act.SingleHit, mode: Mode) -> dict[str, Any]:
    return _live_managed(state, mode) or combat_flow.managed_single_hit(
        state, action.character, action.damage, event_id=action.event_id, reason=action.reason,
    )


def _add_effect(state: GroupState, action: act.AddEffect, mode: Mode) -> dict[str, Any]:
    return combat.add_combat_effect(
        state, action.target, action.label, timing=action.timing, damage=action.damage,
        damage_type=action.damage_type, remaining_rounds=action.remaining_rounds, tags=action.tags or [],
        source_id=action.source_id, public_description=action.public_description,
    )


def _declare_effect(state: GroupState, action: act.DeclareEffect, mode: Mode) -> dict[str, Any]:
    reason = authorize(state, action.combat_id, action.reason)
    return _live_managed(state, mode) or combat_flow.declare_effect(
        state, effect_id=action.effect_id, target_id=action.target_id, severity_id=action.severity_id,
        scope=action.scope, reason=reason, stop_condition=action.stop_condition, timing=action.timing,
        special_rule=action.special_rule, defense=action.defense,
    )


def _stop_effect(state: GroupState, action: act.StopEffect, mode: Mode) -> dict[str, Any]:
    reason = str(action.reason or "").strip()
    if not reason:
        raise ValueError("Explicit controller reason is required")
    return combat_flow.stop_effect(
        state, combat_id=action.combat_id, effect_id=action.effect_id, event_id=action.event_id,
        reason=reason,
    )


def _run_effect(state: GroupState, action: act.RunEffect, mode: Mode) -> dict[str, Any]:
    return _live_managed(state, mode) or combat_flow.run_effect(state, action.effect_id)


def _rule(state: GroupState, action: act.Rule, mode: Mode) -> dict[str, Any]:
    reason = authorize(state, action.combat_id, action.reason)
    return _live_managed(state, mode) or combat_flow.resolve_ruling(
        state, action_id=action.action_id, event_id=action.event_id, reason=reason,
        decision=action.decision, weapon_reference=action.weapon_reference,
        distance_yards=action.distance_yards, scenario_definitions=action.scenario_definitions,
        weapon_instance=action.weapon_instance,
    )


def _reconcile_correction(state: GroupState, action: act.ReconcileCorrection, mode: Mode) -> dict[str, Any]:
    reason = authorize(state, action.combat_id, action.reason)
    return _live_managed(state, mode) or combat_flow.reconcile_correction(
        state, event_id=action.event_id, reason=reason, injury_by_character=action.injury_by_character,
        acknowledge_action_ids=action.acknowledge_action_ids,
        acknowledge_check_ids=action.acknowledge_check_ids,
    )


def _stabilize(state: GroupState, action: act.Stabilize, mode: Mode) -> dict[str, Any]:
    return combat_flow.stabilize_investigator(
        state, character_id=action.character_id, source_check_id=action.source_check_id,
        event_id=action.event_id, reason=action.reason,
    )


def _request_stabilization(state: GroupState, action: act.RequestStabilization, mode: Mode) -> dict[str, Any]:
    return combat_flow.request_stabilization_check(
        state, healer_character_id=action.healer_character_id, character_id=action.character_id,
        event_id=action.event_id, reason=action.reason,
    )


def _process_postcombat(state: GroupState, action: act.ProcessPostcombat, mode: Mode) -> dict[str, Any]:
    return combat_flow.process_postcombat(state, logical_round=action.logical_round, event_id=action.event_id)


def _preview_settlement(state: GroupState, action: act.PreviewSettlement, mode: Mode) -> dict[str, Any]:
    preview = combat_resources.get_settlement(state, obligations=combat_flow.postcombat_obligations(state))
    return {"ok": True, "preview": preview, "provisional": True}


def _confirm_settlement(state: GroupState, action: act.ConfirmSettlement, mode: Mode) -> dict[str, Any]:
    if not str(action.reason or "").strip():
        raise ValueError("Explicit controller reason is required")
    old = state.closed_combat_receipts.get(str(action.combat_id or ""), {})
    first_commit = old.get("settlement_id") != action.settlement_id
    if first_commit:
        authorize(state, action.combat_id, action.reason)
    receipt = combat_resources.commit_settlement(state, action.settlement_id)
    if first_commit:
        report = state.last_combat_report
        scoped = (
            report.get("timeline_id") == state.timeline_id
            and report.get("scenario_library_id") == state.scenario_library_id
            and report.get("scenario_title") == state.scenario_title
        )
        last_damage = deepcopy(report.get("last_damage", {})) if scoped else {}
        if last_damage.get("public_summary"):
            last_damage["public_summary"] = last_damage["public_summary"].replace("（戰鬥暫定）", "（已結算）")
        state.last_combat_report = {
            "timeline_id": state.timeline_id, "scenario_library_id": state.scenario_library_id,
            "scenario_title": state.scenario_title, "combat_id": receipt["combat_id"],
            "settlement_id": receipt["settlement_id"], "ended": True, "provisional": False,
            "combatants": [
                {"name": member.display_name, "side": member.side, "defeated": member.defeated,
                 "hp": member.hp, "hp_max": member.hp_max}
                for member in state.combat.order
            ],
            "last_damage": last_damage,
        }
    return {"ok": True, "receipt": receipt, "provisional": False}


def _rollback(state: GroupState, action: act.Rollback, mode: Mode) -> dict[str, Any]:
    reason = str(action.reason or "").strip()
    if not reason:
        raise ValueError("Explicit controller reason is required")
    old = state.closed_combat_receipts.get(str(action.combat_id or ""), {})
    if old.get("rollback_event_id") != action.event_id:
        reason = authorize(state, action.combat_id, action.reason)
    receipt = combat_resources.rollback_combat(state, event_id=action.event_id, reason=reason)
    return {"ok": True, "receipt": receipt, "rolled_back": True}


def _correct_event(state: GroupState, action: act.CorrectEvent, mode: Mode) -> dict[str, Any]:
    reason = authorize(state, action.combat_id, action.reason)
    receipt = combat_resources.correct_event(
        state, action.target_event_id, event_id=action.event_id, changes=action.changes, reason=reason,
    )
    return {"ok": True, "receipt": receipt, "phase": state.combat.phase, "provisional": True}


def _reconcile_baseline(state: GroupState, action: act.ReconcileBaseline, mode: Mode) -> dict[str, Any]:
    reason = authorize(state, action.combat_id, action.reason)
    receipt = combat_resources.reconcile_baseline(
        state, action.character, event_id=action.event_id,
        decision=cast(Any, action.decision), reason=reason,
    )
    return {"ok": True, "receipt": receipt, "provisional": True}


def _close_legacy(state: GroupState, action: act.CloseLegacy, mode: Mode) -> dict[str, Any]:
    return combat.close_legacy_combat(state, event_id=action.event_id, reason=action.reason)


_HANDLERS: dict[type, Handler] = {
    act.Start: _start,
    act.AddCombatant: _add_combatant,
    act.Status: _status,
    act.Obligations: _obligations,
    act.Advance: _advance,
    act.PlanEnemy: _plan_enemy,
    act.RunEnemyPlan: _run_enemy_plan,
    act.ResolveEnemy: _resolve_enemy,
    act.SetInitiative: _set_initiative,
    act.FinishRetiredTurn: _finish_retired_turn,
    act.Declare: _declare,
    act.Run: _run,
    act.Choose: _choose,
    act.ChoiceReceipt: _choice_receipt,
    act.ValidatePending: _validate_pending,
    act.RollPending: _roll_pending,
    act.CheckResult: _check_result,
    act.ApplyDamage: _apply_damage,
    act.DamageCombatant: _damage_combatant,
    act.SingleHit: _single_hit,
    act.AddEffect: _add_effect,
    act.DeclareEffect: _declare_effect,
    act.StopEffect: _stop_effect,
    act.RunEffect: _run_effect,
    act.Rule: _rule,
    act.ReconcileCorrection: _reconcile_correction,
    act.Stabilize: _stabilize,
    act.RequestStabilization: _request_stabilization,
    act.ProcessPostcombat: _process_postcombat,
    act.PreviewSettlement: _preview_settlement,
    act.ConfirmSettlement: _confirm_settlement,
    act.Rollback: _rollback,
    act.CorrectEvent: _correct_event,
    act.ReconcileBaseline: _reconcile_baseline,
    act.CloseLegacy: _close_legacy,
}


class CombatEngine:
    """Stateless; every call works on the state it is given, inside the caller's transaction."""

    def handle(self, state: GroupState, action: act.Action[R]) -> R:
        handler = _HANDLERS.get(type(action))
        if handler is None:
            raise TypeError(f"unknown combat action {type(action).__name__}")
        mode = mode_of(state)
        return cast(R, handler(state, action, mode))


ENGINE = CombatEngine()


def handle(state: GroupState, action: act.Action[R]) -> R:
    """``ENGINE.handle`` as a function, for callers that do not need to hold the engine."""
    return ENGINE.handle(state, action)


def finish_retired_turn(
    state: GroupState, *, old_order: list[Any], old_index: int, removed_ids: set[str],
) -> None:
    """The turn finisher ``GroupState.retire_active_character`` is given, so the model never imports combat."""
    ENGINE.handle(state, act.FinishRetiredTurn(old_order=old_order, old_index=old_index, removed_ids=removed_ids))
