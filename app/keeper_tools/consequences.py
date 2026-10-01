"""Restricted, source-bound consequences of finalized player checks."""
from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from app import check_lifecycle, combat_resources, dice, resolved_check_consequences
from app.keeper_tools import resource_bridge

if TYPE_CHECKING:
    from app.keeper_tools.registry import ToolCall


def _failure(error: str) -> dict[str, Any]:
    return {"ok": False, "error": error}


def _prior_result(state, identity: str, fingerprint: str) -> dict[str, Any] | None:
    previous = state.check_consequence_receipts.get(identity)
    if previous is None:
        return None
    if previous.get("fingerprint") != fingerprint:
        return _failure("同一檢定後果識別已用於不同的機制參數")
    return deepcopy(previous["result"])


def apply_resolved_check_damage(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    args = call.input
    expression = args.get("damage_expression")
    final_damage = args.get("final_damage")
    if (expression is None) == (final_damage is None):
        return _failure("傷害須提供 damage_expression 或 final_damage，且只能擇一")
    if not isinstance(args.get("cause"), str) or not args["cause"].strip():
        return _failure("傷害須說明來源事件")
    fingerprint = resolved_check_consequences.request_fingerprint(args)

    def mutate(latest):
        try:
            char, _, rule = resolved_check_consequences.authorized(
                latest, actor_id=call.actor_id, investigator=args["investigator"],
                check_id=args["source_check_id"], event_id=args["source_event_id"],
                key=args["consequence_key"], kind="damage",
            )
        except (KeyError, ValueError) as error:
            return keeper.ToolStateMutation(_failure(str(error)), should_save=False)
        identity = resolved_check_consequences.consequence_identity(
            latest, args["source_event_id"], args["consequence_key"], char.character_id
        )
        prior = _prior_result(latest, identity, fingerprint)
        if prior is not None:
            return keeper.ToolStateMutation(prior, should_save=False)
        if rule.get("damage_type") != args.get("damage_type"):
            return keeper.ToolStateMutation(_failure("傷害型別與原檢定授權不符"), should_save=False)
        if expression is not None:
            if not isinstance(expression, str) or rule.get("damage_expression") != (
                resolved_check_consequences.canonical_expression(expression)
            ):
                return keeper.ToolStateMutation(_failure("傷害骰式與原檢定授權不符"), should_save=False)
        elif type(final_damage) is not int or rule.get("final_damage") != final_damage:
            return keeper.ToolStateMutation(_failure("固定傷害與原檢定授權不符"), should_save=False)
        # A major wound may need to register CON. Refuse an occupied check slot
        # before drawing random dice, so a rejected mutation consumes no roll.
        blocker = check_lifecycle.blocker(latest, char.owner_id)
        if blocker:
            return keeper.ToolStateMutation(_failure(f"請先處理 {blocker}，再結算此傷害"), should_save=False)
        if resource_bridge.participating(latest, char) and expression is not None:
            from dataclasses import asdict
            receipt = combat_resources.record_roll(latest, identity + ':damage-roll',
                                                   lambda: asdict(dice.roll_expression(expression)))
            roll = dice.RollResult(**receipt)
        else:
            roll = dice.roll_expression(expression) if expression is not None else None
        damage = roll.total if roll is not None else final_damage
        if not isinstance(damage, int) or damage < 0:
            return keeper.ToolStateMutation(_failure("傷害結果不能為負數"), should_save=False)
        hp_before = resource_bridge.effective(latest, char).hp
        hp_after, major_wound, wound_roll, blocked = keeper.apply_character_delta_in_state(
            latest, char, "hp", -damage, "hp", "hp_max",
            entry_point="apply_resolved_check_damage", event_id=identity, reason=args["cause"],
        )
        if blocked is not None:
            raise RuntimeError("重傷檢定在傷害計算後遭到阻擋")
        result = {
            "ok": True, "investigator": char.name, "damage": damage,
            "provisional": resource_bridge.participating(latest, char),
            "damage_type": args["damage_type"], "hp_before": hp_before,
            "hp_after": hp_after, "source_check_id": args["source_check_id"],
            "source_event_id": args["source_event_id"],
            "consequence_key": args["consequence_key"], "major_wound": major_wound,
            "major_wound_check": wound_roll,
            "expression": roll.expression if roll else None,
            "rolls": roll.rolls if roll else None,
            "modifier": roll.modifier if roll else None,
        }
        latest.check_consequence_receipts[identity] = {
            "fingerprint": fingerprint, "result": deepcopy(result),
        }
        return result

    return keeper.mutate_tool_state(call.state, mutate)


def create_triggered_check(call: ToolCall) -> dict[str, Any]:
    from app import keeper

    args = call.input
    fingerprint = resolved_check_consequences.request_fingerprint(args)

    def mutate(latest):
        try:
            char, origin, rule = resolved_check_consequences.authorized(
                latest, actor_id=call.actor_id, investigator=args["investigator"],
                check_id=args["trigger_check_id"], event_id=args["trigger_event_id"],
                key=args["consequence_key"], kind="check",
            )
        except (KeyError, ValueError) as error:
            return keeper.ToolStateMutation(_failure(str(error)), should_save=False)
        identity = resolved_check_consequences.consequence_identity(
            latest, args["trigger_event_id"], args["consequence_key"], char.character_id
        )
        prior = _prior_result(latest, identity, fingerprint)
        if prior is not None:
            if prior.get("ok"):
                current = latest.pending_checks.get(char.owner_id) or {}
                prior["pending"] = current.get("check_id") == prior.get("check_id")
                prior["duplicate"] = True
            return keeper.ToolStateMutation(prior, should_save=False)
        skill = args.get("skill")
        difficulty = args.get("difficulty", "regular")
        if skill != rule.get("skill") or difficulty != rule.get("difficulty"):
            return keeper.ToolStateMutation(_failure("新檢定與原檢定授權不符"), should_save=False)
        if not isinstance(skill, str) or skill.strip().casefold() == str(origin.get("skill", "")).strip().casefold():
            return keeper.ToolStateMutation(_failure("不能重建已結算的來源檢定"), should_save=False)
        if not isinstance(args.get("trigger_condition"), str) or not args["trigger_condition"].strip():
            return keeper.ToolStateMutation(_failure("缺少後續檢定的觸發說明"), should_save=False)
        skill_value = keeper.resolve_skill_value(char, skill, register_unknown=False)
        candidate: dict[str, Any] = {
            "type": "skill", "skill": skill, "skill_value": skill_value,
            "bonus_dice": 0, "penalty_dice": 0, "difficulty": difficulty,
            "pushed": False, "source_check_id": args["trigger_check_id"],
            "source_event_id": args["trigger_event_id"],
        }
        if rule.get("next_consequences"):
            candidate["consequences"] = deepcopy(rule["next_consequences"])
        registered = check_lifecycle.register(
            latest, char.owner_id, candidate,
            source={"action_context": str(args.get("action_context", ""))[:240]},
        )
        if registered.status != "admitted" or registered.pending is None:
            return keeper.ToolStateMutation(
                _failure(f"仍有待處理檢定或 Luck 決定：{registered.blocker}"), should_save=False
            )
        result = {
            "ok": True, "pending": True, "investigator": char.name,
            "skill": skill, "skill_value": skill_value, "difficulty": difficulty,
            "check_id": registered.check_id, "timeline_id": registered.timeline_id,
            "source_check_id": args["trigger_check_id"],
            "source_event_id": args["trigger_event_id"],
            "consequence_key": args["consequence_key"],
        }
        latest.check_consequence_receipts[identity] = {
            "fingerprint": fingerprint, "result": deepcopy(result),
        }
        return result

    return keeper.mutate_tool_state(call.state, mutate)
