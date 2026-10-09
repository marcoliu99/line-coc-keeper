"""Player rolls and Luck decisions for checks the combat engine owns.

A pending entry with ``combat_context``, ``postcombat_context`` or
``medical_context`` belongs to a managed battle or a continuing injury: its roll,
Luck and result are recorded against the battle, not just the character. The
check engine hands such entries to this adapter, which turns them into combat
engine actions (validate, roll, feed the result back) and the engine's answers
into check outcomes. It sits above both, so the check engine never imports
combat code and the combat engine never imports the check engine.

Like the check service, nothing here saves: it changes the state it is given and
returns an outcome whose ``changed`` flag (and ``save_reason``) the surrounding
transaction acts on.
"""
from __future__ import annotations

from app import combat_resources, dice, presentation
from app.checks import events, narration
from app.checks import luck as luck_policy
from app.checks.models import CheckOutcome, outcome_for
from app.keeper_tools import resource_bridge
from app.models import CombatAction, CombatCheckIdentity, GroupState
from app.services import combat_actions as act
from app.services import combat_engine


class ManagedCombatChecks:
    """The ``ManagedChecks`` implementation backed by the combat engine."""

    def resolve_check(self, state: GroupState, user_id: str, text: str, pending: dict) -> CheckOutcome:
        """Consume only a server-owned combat/continuing-state interaction."""
        validation = combat_engine.handle(state, act.ValidatePending(pending=pending, owner_id=user_id))
        if not validation["ok"]:
            return outcome_for(user_id, pending, reply_text=validation["error"])
        try:
            resource_bridge.owned_character(state, pending, user_id)
        except ValueError as error:
            return outcome_for(user_id, pending, reply_text=str(error))
        args = text.split()
        skill_arg = args[2] if len(args) > 2 else None
        if pending.get("type") == "choice":
            return self._choose(state, user_id, pending, skill_arg)
        if pending.get("type") != "skill" or (
            skill_arg and not narration.skill_names_match(pending.get("skill", ""), skill_arg)
        ):
            return outcome_for(user_id, pending, reply_text="請使用目前待處理檢定的技能或檢定按鈕。")
        try:
            character = resource_bridge.owned_character(state, pending, user_id)
        except ValueError as error:
            return outcome_for(user_id, pending, reply_text=str(error))
        before = events.character_attribute_snapshot(character)
        result = combat_engine.handle(state, act.RollPending(pending=pending, owner_id=user_id))
        state.pending_checks.pop(user_id, None)
        options = luck_policy.offer(
            result.skill_value, result.roll, result.tier, character.luck, result.required_tier,
            allowed=pending.get("allow_luck", True),
        )
        if options:
            decision = {
                **pending, "decision_id": pending["check_id"] + ":luck",
                "skill_name": pending["skill"], "display_label": None,
                "value": result.skill_value, "roll": result.roll, "original_tier": result.tier,
                "attacker_tier": None, "ranged_attacker": None,
                "options": [{"tier": o.tier, "cost": o.cost} for o in options],
            }
            state.pending_luck_decisions[user_id] = decision
            outcome = combat_engine.handle(state, act.CheckResult(
                pending_entry=decision, owner_id=user_id, result=result, final=False,
            ))
            if not outcome.get("ok"):
                return _paused(user_id, pending, f"骰值 {result.roll} 已保留；{outcome.get('error', '戰鬥暫停')}",
                               "combat_check_paused")
            resource_bridge.record_control_receipt(state, decision, user_id, character, result, pending_luck=True)
            options_text = "、".join(f"{presentation.tier_label(o.tier)}（{o.cost} 點）" for o in options)
            reply = outcome_for(
                user_id, pending,
                reply_text=(
                    f"🎲 {character.name} 的 {pending['skill']} 擲出 {result.roll} → "
                    f"{narration.tier_zh_for_result(result)}。{_defence_needs(state, pending)}目前 Luck {character.luck}；"
                    f"可用 Luck 買到 {options_text}，或維持目前結果。"
                ),
                check_id=pending["check_id"], timeline_id=pending.get("timeline_id", ""),
                decision_id=decision["decision_id"], changed=True,
            )
            reply.save_reason = "combat_check_luck"
            return reply
        outcome = combat_engine.handle(state, act.CheckResult(
            pending_entry=pending, owner_id=user_id, result=result,
        ))
        if not outcome.get("ok"):
            return _paused(user_id, pending, f"骰值 {result.roll} 已保留；{outcome.get('error', '戰鬥暫停')}",
                           "combat_check_paused")
        resource_bridge.record_control_receipt(state, pending, user_id, character, result)
        settled = _feedback(state, character, user_id, pending, result, outcome, before)
        settled.save_reason = "combat_check"
        return settled

    def _choose(self, state: GroupState, user_id: str, pending: dict, skill_arg: str | None) -> CheckOutcome:
        option = narration.match_choice_option(pending.get("options", []), skill_arg)
        if option is None:
            labels = "、".join(o["label"] for o in pending.get("options", []))
            return outcome_for(user_id, pending, reply_text=f"請選擇：{labels}")
        character = resource_bridge.owned_character(state, pending, user_id)
        before = events.character_attribute_snapshot(character)
        outcome = combat_engine.handle(state, act.Choose(
            interaction_id=pending["combat_context"]["interaction_id"],
            owner_id=user_id, choice=option["kind"],
        ))
        if not outcome.get("ok"):
            return outcome_for(user_id, pending, reply_text=outcome.get("error", "選擇遭拒"))
        chosen = f"已選擇「{option['label']}」。"
        rolled = state.pending_checks.get(user_id)
        if option["kind"] == "no_defense" and outcome.get("completed") and (rolled is None or rolled.get("type") != "skill"):
            # No roll, but the shot was settled by the choice (damage applied, the turn possibly moved on, even to
            # the next enemy's attack and its own choice): the Keeper narrates it from the receipt, as after any
            # settled roll, instead of the thread ending here. Only a skill check left for the player (the wound's
            # CON roll) keeps the turn with them first.
            settled = _settled_without_roll(state, character, user_id, pending, option, outcome, before)
            resource_bridge.record_choice_control_receipt(state, pending, user_id, option, settled.roll_feedback_text)
            return settled
        if rolled is not None and _is_defence_roll_for(rolled, pending, option):
            # The button says "choose and roll": the choice registered the defence check, so roll it now rather
            # than asking for a second click. Luck, if offered, is still the player's own decision afterwards.
            result = self.resolve_check(state, user_id, "/coc check", rolled)
            if result.reply_text:
                result.reply_text = chosen + result.reply_text
            receipt_text = result.reply_text or chosen + result.roll_feedback_text
            resource_bridge.record_choice_control_receipt(state, pending, user_id, option, receipt_text)
            result.changed = True
            result.save_reason = result.save_reason or "combat_choice"
            return result
        if rolled is not None and rolled.get("type") == "skill":
            # Not the defence this choice created (a CON check for the wound a no-defence shot just dealt): that
            # roll is the player's own next click, never folded into the choice they made.
            blow = _blow(state, pending, outcome)
            reply_text = chosen + (f"⚔️ {blow}" if blow else "攻擊已結算；") + f"接下來是你的{rolled.get('skill', '')}檢定，請按鈕擲骰。"
        else:
            reply_text = chosen + "已依系統紀錄處理；請依目前戰鬥狀態繼續。"
        resource_bridge.record_choice_control_receipt(state, pending, user_id, option, reply_text)
        result = outcome_for(user_id, pending, reply_text=reply_text, changed=True)
        result.save_reason = "combat_choice"
        return result

    def resolve_luck(self, state: GroupState, user_id: str, choice: str, pending: dict) -> CheckOutcome:
        validation = combat_engine.handle(state, act.ValidatePending(pending=pending, owner_id=user_id))
        if not validation["ok"]:
            return outcome_for(user_id, pending, reply_text=validation["error"])
        try:
            character = resource_bridge.owned_character(state, pending, user_id)
        except ValueError as error:
            return outcome_for(user_id, pending, reply_text=str(error))
        before = events.character_attribute_snapshot(character)
        spend = luck_policy.choose(pending["options"], choice, character.luck, pending["original_tier"])
        if isinstance(spend, str):
            return outcome_for(user_id, pending, reply_text="無效或無法負擔的 Luck 選項；原決定仍保留。")
        if spend.cost:
            if resource_bridge.participating(state, character):
                combat_resources.adjust_resource(
                    state, character, "luck", -spend.cost,
                    event_id=pending["decision_id"] + ":spend", reason="Player Luck decision",
                )
            else:
                character.luck -= spend.cost
        required = pending.get("difficulty", "regular")
        result = dice.SkillCheckResult(
            skill_value=pending["value"], roll=pending["roll"],
            bonus_dice=pending.get("bonus_dice", 0), penalty_dice=pending.get("penalty_dice", 0),
            tier=spend.tier, success=luck_policy.success_at(spend.tier, required), required_tier=required,
        )
        state.pending_luck_decisions.pop(user_id, None)
        outcome = combat_engine.handle(state, act.CheckResult(
            pending_entry=pending, owner_id=user_id, result=result,
        ))
        if not outcome.get("ok"):
            return _paused(
                user_id, pending, f"Luck 決定與骰值 {result.roll} 已保留；{outcome.get('error', '戰鬥暫停')}",
                "combat_luck_paused",
            )
        resource_bridge.record_control_receipt(state, pending, user_id, character, result, choice=choice)
        settled = _feedback(state, character, user_id, pending, result, outcome, before, luck_spent=spend.cost)
        settled.save_reason = "combat_luck"
        return settled


def _is_defence_roll_for(rolled: dict, choice: dict, option: dict) -> bool:
    """Whether ``rolled`` is the defence check the player's choice just registered for the same action.

    A no-defence choice registers none; the check left after it (the wound's CON roll) belongs to the player's
    next click.
    """
    if option.get("kind") == "no_defense" or rolled.get("type") != "skill":
        return False
    context, chosen = rolled.get("combat_context") or {}, choice.get("combat_context") or {}
    try:
        role = CombatCheckIdentity.from_serialized(str(context.get("check_role", ""))).role
    except (TypeError, ValueError):
        return False
    return role == "defense" and context.get("action_id") == chosen.get("action_id")


def _settled_without_roll(
    state: GroupState, character, user_id: str, pending: dict, option: dict, outcome: dict, before: dict,
) -> CheckOutcome:
    """A choice that settled the attack with no roll of the player's (no defence against a shot)."""
    header = f"{character.name} 選擇「{option['label']}」"
    status = "戰鬥已結算" if outcome.get("settled") else "戰鬥機械結果暫定；尚未結算"
    blow = _blow(state, pending, outcome)
    feedback = f"{header}，攻擊已由系統結算。" + ("\n⚔️ " + blow if blow else "") + f"\n【{status}】"
    return outcome_for(
        user_id, pending,
        roll_line=feedback, keeper_message=f"（{header}；{status}。僅依已儲存的戰鬥結果敘事，不要另外擲攻擊或傷害骰。）",
        roll_feedback_text=feedback, keeper_header=header, should_finalize=True,
        check_id=pending["check_id"], decision_id=pending.get("decision_id", ""),
        timeline_id=pending.get("timeline_id", ""), action_context=pending.get("action_context", ""),
        changed=True,
        resolved_event={
            **events.event_seed(
                check_id=pending["check_id"],
                timeline_id=pending.get("timeline_id", state.timeline_id or f"legacy-{state.group_id}"),
                owner_id=user_id, character_id=character.character_id, investigator=character.name,
                skill=option["label"], skill_value=0, roll=0, difficulty="regular",
                outcome=f"{option['label']}，攻擊已結算", before=before, tracked_roll_fields=(), check_context=pending,
            ),
            "no_roll": True, "provisional": not outcome.get("settled"),
            "combat_id": pending.get("combat_context", {}).get("combat_id", ""),
            "combat_receipt": {**_combat_receipt(outcome), "blow": blow},
            "luck_spent": 0,
        },
    )


def _combat_receipt(outcome: dict) -> dict:
    return {
        "combat_id": outcome.get("combat_id"), "action_id": outcome.get("action_id"),
        "phase": outcome.get("phase"), "completed": outcome.get("completed"),
        "auto_advanced": outcome.get("auto_advanced"), "auto_advance_error": outcome.get("auto_advance_error"),
        "follow_up": (outcome.get("result") or {}).get("follow_up") if isinstance(outcome.get("result"), dict) else None,
        "settlement_ready": outcome.get("settlement_ready"),
        "scenario_ended": outcome.get("scenario_ended"),
    }


def _paused(user_id: str, pending: dict, text: str, reason: str) -> CheckOutcome:
    """A roll that is kept but whose battle step could not complete yet."""
    outcome = outcome_for(user_id, pending, reply_text=text, changed=True)
    outcome.save_reason = reason
    return outcome


def _feedback(
    state: GroupState, character, user_id: str, pending: dict, result, outcome: dict, before: dict,
    *, luck_spent: int = 0,
) -> CheckOutcome:
    label = pending.get("skill_name") or pending.get("skill", "")
    tier = narration.tier_zh_for_result(result)
    provisional = bool(pending.get("combat_context")) and not outcome.get("settled")
    suffix = ("【戰鬥已結算】" if outcome.get("settled") else
              "【戰鬥機械結果暫定；尚未結算】" if provisional else "【戰鬥後待履行事項已處理】")
    feedback, header = narration.build_split_check_feedback(
        character.name, label, str(result.skill_value), result.roll, tier,
    )
    blow = _blow(state, pending, outcome)
    shown = blow if blow and not _is_injury_roll(pending) else ""  # the blow was shown with the roll that took it
    feedback += ("\n⚔️ " + shown if shown else "") + "\n" + suffix
    settled = outcome_for(
        user_id, pending,
        roll_line=feedback, keeper_message=f"（{header}；{suffix}。僅依已儲存的戰鬥結果敘事，不要另外擲攻擊或傷害骰。）",
        roll_feedback_text=feedback, keeper_header=header, should_finalize=True,
        check_id=pending["check_id"], decision_id=pending.get("decision_id", ""),
        timeline_id=pending.get("timeline_id", ""), action_context=pending.get("action_context", ""),
        changed=True,
        resolved_event={
            **events.event_seed(
                check_id=pending["check_id"],
                timeline_id=pending.get("timeline_id", state.timeline_id or f"legacy-{state.group_id}"),
                owner_id=user_id, character_id=character.character_id, investigator=character.name,
                skill=label, skill_value=result.skill_value, roll=result.roll, difficulty=result.required_tier,
                outcome=f"{result.tier} " + ("成功" if result.success else "失敗"), before=before,
                tracked_roll_fields=(), check_context=pending, success=result.success,
            ),
            "provisional": provisional, "combat_id": pending.get("combat_context", {}).get("combat_id", ""),
            "combat_receipt": {**_combat_receipt(outcome), "blow": blow},
            "luck_spent": luck_spent,
        },
    )
    return settled


def _attack_action(state: GroupState, pending: dict) -> CombatAction | None:
    """The attack a player's attack roll, defence (choice or roll) or the wound's CON roll after it belongs to; None
    for any other check."""
    context = pending.get("combat_context") or {}
    try:
        role = CombatCheckIdentity.from_serialized(str(context.get("check_role", ""))).role
    except (TypeError, ValueError):
        return None
    action = state.combat.actions.get(str(context.get("action_id", "")))
    if role == "injury" and action:
        # The CON roll a heavy blow owes carries the blow only after a no-defence choice: a defence roll has already
        # been narrated with it, and narrating it again after the CON roll would tell the player the hit twice.
        parent = state.combat.actions.get(str(action.get("parent_action_id", "")))
        return parent if parent and parent.get("defense_kind") == "no_defense" else None
    return action if role in {"attack", "defense", "defense_choice"} else None


def _is_injury_roll(pending: dict) -> bool:
    return str((pending.get("combat_context") or {}).get("check_role", "")).startswith("injury")


def _actor_name(state: GroupState, action: CombatAction) -> str:
    return next((c.display_name for c in state.combat.order if c.combatant_id == action.get("actor_id")), "對方")


def _defence_needs(state: GroupState, pending: dict) -> str:
    """What a melee defence roll must reach against the blow already rolled, so spending Luck is an informed choice.

    A skill success alone does not keep a blow off: the defence has to match (Dodge) or beat (Fight Back) the
    attacker's tier.
    """
    action = None if _is_injury_roll(pending) else _attack_action(state, pending)
    if not action or action.get("defense_kind") not in {"dodge", "counter"}:
        return ""
    tier = ((action.get("checks") or {}).get("attack") or {}).get("tier")
    if tier not in dice.TIER_RANK:
        return ""
    counter = action["defense_kind"] == "counter"
    head = f"{_actor_name(state, action)}的攻擊是「{presentation.tier_label(tier)}」"
    if not counter and dice.TIER_RANK[tier] <= dice.TIER_RANK["fail"]:
        return head + "，這一擊打不中你。"
    needed = dice.defence_tier_needed(tier, counter)
    if needed is None:
        return head + "，反擊贏不了這一擊。"
    return head + f"，要「{presentation.tier_label(needed)}」以上{'反擊才打得中' if counter else '才躲得開'}。"


def _blow(state: GroupState, pending: dict, outcome: dict) -> str:
    """Whether the blow this roll settled landed, and what it did, in the table's words.

    A Dodge that rolls a success can still be hit (the attacker rolled a higher tier), so the defender's tier alone
    misleads both the player and the narrator.
    """
    action = _attack_action(state, pending)
    result = outcome.get("result")
    if not action or not isinstance(result, dict) or "hit" not in result:
        return ""
    if result.get("malfunction"):
        return "武器故障，這一擊沒有打出去。"
    checks = action.get("checks") or {}
    attack, defence = (checks.get("attack") or {}).get("tier"), (checks.get("defense") or {}).get("tier")
    kind = action.get("defense_kind")
    head = ""
    if kind in {"dodge", "counter"} and attack in dice.TIER_RANK and defence in dice.TIER_RANK:
        attacker, defender = _actor_name(state, action), next(
            (c.display_name for c in state.combat.order if c.combatant_id == action.get("target_id")), "防守方")
        head = (f"{attacker}的攻擊「{presentation.tier_label(attack)}」對上"
                f"{defender}的{'反擊' if kind == 'counter' else '閃避'}「{presentation.tier_label(defence)}」：")
    if not result["hit"]:
        return head + "這一擊沒有命中。"
    damage = result.get("damage") or {}
    landed = "反擊得手" if result.get("opposed") == "defender_wins" and kind == "counter" else "命中"
    absorbed = bool(damage.get("armor_label")) and (damage.get("armor_reduction") or 0) > 0
    if absorbed and not damage.get("final_damage"):
        return head + f"{landed}，但傷害全被 {damage.get('target', '目標')} 的護甲擋下。"
    blocked = "（部分被護甲擋下）" if absorbed else ""
    return head + f"{landed}，{damage.get('target', '目標')} 受到 {damage.get('final_damage', 0)} 點傷害{blocked}。"
