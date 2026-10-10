"""Applying the check rules to a ``GroupState``.

Every function here takes the state a *transaction* hands it (``ctx.state`` from
``app.repositories.state_transaction``) and changes it in place; the caller
commits. None of them saves, takes a lock, or talks to Discord, the Keeper or an
LLM. The result is a :class:`~app.checks.models.CheckOutcome` whose ``changed``
flag says whether the transaction has anything to write.

A player's roll (``resolve_player_check``), a Luck decision
(``resolve_luck_decision``) and the Keeper's autoroll (``autoroll_skill_check``,
``autoroll_sanity_check``) all go through the same helpers for tier, Luck offer,
Luck spend, madness chain and event seed, so the same check cannot resolve
differently depending on which door it came through.

Checks owned by the combat engine (a pending entry with ``combat_context``,
``postcombat_context`` or ``medical_context``) are handed to a ``ManagedChecks``
adapter the caller supplies; this module does not import combat code.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from app import check_lifecycle, observability
from app.check_identity import (
    effective_check_id,
    effective_decision_id,
    new_check_id,
    new_decision_id,
)
from app.checks import events, narration, rules
from app.checks import luck as luck_policy
from app.checks.dice_port import DEFAULT_DICE, DicePort
from app.checks.models import CheckOutcome, audience, outcome_for
from app.checks.skills import resolve_skill_value
from app.keeper_tools import resource_bridge
from app.models import Character, GroupState
from app.services import opposed_checks

_logger = logging.getLogger(__name__)

STALE_CHECK_TEXT = "這個檢定所屬的劇情時間線已經失效，請依目前劇情重新操作。"
STALE_LUCK_TEXT = "這個 Luck 決定所屬的劇情時間線已經失效，請依目前劇情重新操作。"
MANAGED_UNAVAILABLE_TEXT = "這是戰鬥流程持有的互動，請依戰鬥狀態操作。"


class ManagedChecks(Protocol):
    """Resolves a pending entry owned by the combat engine."""

    def resolve_check(self, state: GroupState, user_id: str, text: str, pending: dict) -> CheckOutcome: ...

    def resolve_luck(self, state: GroupState, user_id: str, choice: str, pending: dict) -> CheckOutcome: ...


def is_managed_entry(entry: dict | None) -> bool:
    entry = entry or {}
    return bool(entry.get("combat_context") or entry.get("postcombat_context") or entry.get("medical_context"))


def _timeline(state: GroupState) -> str:
    return state.timeline_id or f"legacy-{state.group_id}"


def _recent_player_action(state: GroupState) -> str:
    recent = next(
        (
            str(entry.get("content", "")).strip()
            for entry in reversed(state.log)
            if entry.get("role") == "user" and str(entry.get("content", "")).strip()
        ),
        "",
    )
    return recent[:237] + "..." if len(recent) > 240 else recent


def _origin_fields(state: GroupState) -> dict[str, Any]:
    origin = observability.current_context()
    return {
        "origin_revision": state.state_revision + 1,
        "origin_turn_id": str(origin.get("turn_id", "")),
        "origin_request_id": str(origin.get("request_id", "")),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _outcome_label(result: Any, opposed: dict | None = None, *, luck_spent: int = 0) -> str:
    text = f"{result.tier} {'成功' if result.success else '失敗'}"
    if opposed:
        text += "；" + opposed_checks.public_text(opposed).rstrip("。")
    if luck_spent:
        text += f"；花費 Luck {luck_spent}"
    return text


# --------------------------------------------------------------------------
# A player's roll
# --------------------------------------------------------------------------

def resolve_player_check(
    state: GroupState, user_id: str, text: str, *,
    dice_port: DicePort = DEFAULT_DICE, managed: ManagedChecks | None = None,
) -> CheckOutcome:
    """Resolve ``/coc check`` or a check button for ``user_id`` on the latest state.

    Consumes the player's pending entry and rolls, or refuses and leaves the
    entry exactly as it was. The roll is drawn only after every refusal above it.
    """
    audience_entry = dict(state.pending_checks.get(user_id) or {})
    if not state.active:
        return outcome_for(user_id, audience_entry, reply_text="目前沒有進行中的遊戲。")
    char = state.get_active_character(user_id)
    if not char:
        return outcome_for(
            user_id, audience_entry,
            reply_text="你還沒有調查員角色，先輸入「/coc pc 角色名 職業」建立角色吧！",
        )
    char = resource_bridge.effective(state, char)
    attributes_before = events.character_attribute_snapshot(char)
    owned = state.pending_checks.get(user_id) or {}
    if is_managed_entry(owned):
        if managed is None:
            return outcome_for(user_id, owned, reply_text=MANAGED_UNAVAILABLE_TEXT)
        return managed.resolve_check(state, user_id, text, owned)

    parts = text.split()
    skill_arg: str | None = parts[2] if len(parts) > 2 else None
    pending = state.pending_checks.pop(user_id, None)
    timeline_id = _timeline(state)
    if pending:
        # Treat explicit null as an absent legacy timeline. Converting it with
        # str(...) would produce "None" and reject an otherwise valid entry.
        pending_timeline_id = str(pending.get("timeline_id") or "").strip()
        if pending_timeline_id and pending_timeline_id != timeline_id:
            observability.event(
                "check.result.stale", level=logging.WARNING, reason="timeline_mismatch",
                requested_timeline_id=pending_timeline_id, current_timeline_id=timeline_id,
                owner_id_hash=observability.safe_identifier(user_id),
            )
            outcome = outcome_for(user_id, audience_entry, reply_text=STALE_CHECK_TEXT)
            outcome.changed = True  # the stale entry is dropped
            return outcome
    # Keep the original entry separate from `pending`: a valid choice consumes
    # the pending entry into the selected option, but its identity and action
    # context must still follow that same persisted request. Metadata is
    # computed only after the skill/type match has been validated, so a
    # mismatched command can never reuse old data for a fresh roll.
    pending_entry = pending

    choice: _Choice | None = None
    if pending and pending.get("type") == "choice":
        if skill_arg is None:
            state.pending_checks[user_id] = pending
            options_text = "、".join(f"{o['label']}（{o['skill']} {o['skill_value']}%）" for o in pending["options"])
            return outcome_for(
                user_id, audience_entry,
                reply_text=f"這是需要選擇的檢定，請輸入「/coc check <選項名稱>」，可選：{options_text}",
            )
        matched = narration.match_choice_option(pending["options"], skill_arg)
        if not matched:
            state.pending_checks[user_id] = pending
            options_text = "、".join(o["label"] for o in pending["options"])
            return outcome_for(user_id, audience_entry, reply_text=f"沒有「{skill_arg}」這個選項，可選：{options_text}")
        choice = _Choice.from_option(matched, pending)
        pending = None
    elif skill_arg is None:
        if not pending:
            return outcome_for(
                user_id, audience_entry,
                reply_text=(
                    "目前沒有待處理的選擇。請先讓 Keeper 建立檢定；玩家用 /coc check 或按鈕擲骰，"
                    "不要在沒有待處理請求時重複送出。"
                ),
            )
    elif pending and pending.get("type") == "sanity":
        state.pending_checks[user_id] = pending
        return outcome_for(
            user_id, audience_entry,
            reply_text="目前等待的是理智檢定，請不要自行指定技能；這筆舊版檢定會由系統處理。",
        )
    elif not (pending and pending.get("type") == "skill"
              and narration.skill_names_match(pending.get("skill", ""), skill_arg)):
        if pending:
            state.pending_checks[user_id] = pending
        return outcome_for(
            user_id, audience_entry,
            reply_text="沒有這個待處理的選擇。請使用正確的選項名稱；角色檢定由玩家用 /coc check 或按鈕擲骰。",
        )

    check_id = effective_check_id(user_id, pending_entry, timeline_id) if pending_entry else new_check_id()
    action_context = str(pending_entry.get("action_context", "")).strip() if pending_entry else ""
    if not action_context:
        action_context = _recent_player_action(state)

    roll = _PlayerRoll(
        state=state, user_id=user_id, char=char, audience_entry=audience_entry,
        pending_entry=pending_entry, check_id=check_id, timeline_id=timeline_id,
        action_context=action_context, attributes_before=attributes_before, dice_port=dice_port,
    )
    if pending and pending.get("type") == "sanity":
        return _settle_sanity(roll, pending)
    return _settle_skill(roll, pending, choice)


class _Choice:
    """The option a player picked from a Dodge / Fight Back style prompt."""

    def __init__(
        self, *, skill_name: str, display_label: str, value: int, bonus: int, penalty: int,
        attacker_tier: str | None, is_counter: bool, ranged_attacker: dict[str, int] | None,
    ) -> None:
        self.skill_name = skill_name
        self.display_label = display_label
        self.value = value
        self.bonus = bonus
        self.penalty = penalty
        self.attacker_tier = attacker_tier
        self.is_counter = is_counter
        self.ranged_attacker = ranged_attacker

    @classmethod
    def from_option(cls, matched: dict, pending: dict) -> _Choice:
        from app import dice

        ranged_attacker: dict[str, int] | None = None
        if pending.get("is_ranged"):
            # The attacker's shot is rolled only after the defender's own dive
            # result is known, not pre-rolled like a melee attacker_tier.
            ranged_attacker = {
                "skill_value": int(pending.get("attacker_skill_value", 0)),
                "bonus_dice": int(pending.get("attacker_bonus_dice", 0)),
                "penalty_dice": int(pending.get("attacker_penalty_dice", 0)),
            }
        return cls(
            skill_name=matched["skill"], display_label=matched["label"],
            value=int(matched["skill_value"]), bonus=int(matched["bonus_dice"]),
            penalty=int(matched["penalty_dice"]), attacker_tier=pending.get("attacker_tier"),
            is_counter=dice.is_counter_option(matched), ranged_attacker=ranged_attacker,
        )


class _PlayerRoll:
    """Everything one player roll needs once the request has been validated."""

    def __init__(
        self, *, state: GroupState, user_id: str, char: Character, audience_entry: dict,
        pending_entry: dict | None, check_id: str, timeline_id: str, action_context: str,
        attributes_before: dict[str, int], dice_port: DicePort,
    ) -> None:
        self.state = state
        self.user_id = user_id
        self.char = char
        self.audience_entry = audience_entry
        self.pending_entry = pending_entry
        self.check_id = check_id
        self.timeline_id = timeline_id
        self.action_context = action_context
        self.attributes_before = attributes_before
        self.dice_port = dice_port

    def reconcile(self, *, event_id: str | None = None, reason: str = "Authoritative player check") -> None:
        resource_bridge.reconcile(
            self.state, self.char, event_id=event_id or f"{self.check_id}:resources", reason=reason,
        )

    def settled(self, **fields: Any) -> CheckOutcome:
        outcome = outcome_for(
            self.user_id, self.audience_entry, should_finalize=True, check_id=self.check_id,
            timeline_id=self.timeline_id, action_context=self.action_context, changed=True, **fields,
        )
        return outcome


def _settle_sanity(roll: _PlayerRoll, pending: dict) -> CheckOutcome:
    state, char, user_id = roll.state, roll.char, roll.user_id
    san_before = char.san
    sanity_result = roll.dice_port.sanity_check(
        san_before, pending.get("loss_success", "0"), pending.get("loss_failure", "1d4")
    )
    char.san = sanity_result.san_after
    outcome = "通過" if sanity_result.check.success else "失敗"
    roll_line = (
        f"🎲 {char.name} 的理智檢定：SAN {san_before}，擲出 {sanity_result.check.roll} → {outcome}，"
        f"損失 {sanity_result.loss} 點理智（現在 SAN {sanity_result.san_after}）"
    )

    if sanity_result.risk_of_madness and not state.autoroll_checks:
        int_value = resolve_skill_value(char, "INT")
        chained_context = f"{roll.action_context}；因 SAN 損失需要做 INT 檢定"
        if len(chained_context) > 240:
            chained_context = chained_context[:237] + "..."
        state.pending_checks[user_id] = {
            **audience(user_id, roll.audience_entry),
            "type": "skill", "skill": "INT", "skill_value": int_value,
            "bonus_dice": 0, "penalty_dice": 0, "difficulty": "regular",
            "madness_trigger": True, "madness_realtime": True,
            "check_id": new_check_id(), "timeline_id": roll.timeline_id,
            "caused_by_check_id": roll.check_id,
            "action_context": chained_context,
            **_origin_fields(state),
        }
        roll_line += (
            "\n⚠️ 這次損失達到 5 點以上，觸發 COC7e「短暫瘋狂」規則：請玩家再用 /coc check INT。"
        )
        keeper_message = (
            f"（{char.name} 的 SAN 檢定已確定：擲出 {sanity_result.check.roll} → {outcome}，"
            f"損失 {sanity_result.loss} 點，現在 SAN {sanity_result.san_after}；因為損失達到 5 點以上，"
            "已建立待處理 INT 檢定，等待玩家擲骰後再判定是否短暫瘋狂。）"
        )
    elif sanity_result.risk_of_madness:
        int_value = resolve_skill_value(char, "INT")
        int_result = roll.dice_port.skill_check(int_value)
        if int_result.success:
            madness = roll.dice_port.roll_madness(realtime=True)
            roll_line += (
                f"\n⚠️ 損失達到 5 點，INT {int_value}% 擲出 {int_result.roll}，觸發短暫瘋狂："
                f"症狀「{madness['symptom']}」（持續約{madness['duration']}）。"
            )
            keeper_message = (
                f"（{char.name} 的 SAN 檢定已確定：擲出 {sanity_result.check.roll} → {outcome}，"
                f"損失 {sanity_result.loss} 點，現在 SAN {sanity_result.san_after}；後續 INT 檢定"
                f"擲出 {int_result.roll}，觸發短暫瘋狂，症狀是「{madness['symptom']}」，持續約"
                f"{madness['duration']}。請照這個既定結果敘事，不要重新判定。）"
            )
        else:
            roll_line += f"\n⚠️ 損失達到 5 點，INT {int_value}% 擲出 {int_result.roll}，未觸發短暫瘋狂。"
            keeper_message = (
                f"（{char.name} 的 SAN 檢定已確定：擲出 {sanity_result.check.roll} → {outcome}，"
                f"損失 {sanity_result.loss} 點，現在 SAN {sanity_result.san_after}；後續 INT 檢定"
                f"擲出 {int_result.roll}，未觸發短暫瘋狂。請照這個既定結果敘事，不要重新判定。）"
            )
    else:
        keeper_message = (
            f"（{char.name} 的理智檢定已由玩家觸發擲骰：SAN {san_before} 擲出 {sanity_result.check.roll} → {outcome}，"
            f"損失 {sanity_result.loss} 點理智，現在 SAN {sanity_result.san_after}。這是已經確定的結果，"
            f"請根據這個結果描述角色的反應與後續發展，不要重新判定或改變這個結果。）"
        )
    roll_feedback_text, keeper_header = narration.build_split_check_feedback(
        char.name, "理智檢定", f"SAN {san_before}", sanity_result.check.roll, outcome
    )
    roll.reconcile()
    return roll.settled(
        roll_line=roll_line, keeper_message=keeper_message,
        roll_feedback_text=roll_feedback_text, keeper_header=keeper_header,
        resolved_event=events.event_seed(
            check_id=roll.check_id, timeline_id=roll.timeline_id, owner_id=roll.user_id,
            character_id=char.character_id, investigator=char.name, skill="SAN", skill_value=san_before,
            roll=sanity_result.check.roll, difficulty="regular", outcome=outcome,
            before=roll.attributes_before, tracked_roll_fields=("san",),
        ),
    )


def _settle_skill(roll: _PlayerRoll, pending: dict | None, choice: _Choice | None) -> CheckOutcome:
    state, char, user_id = roll.state, roll.char, roll.user_id
    is_pushed = False
    attacker_tier = None
    ranged_attacker: dict[str, int] | None = None
    difficulty = "regular"  # offer_check_choice options and a self-initiated /coc check with no
    # pending Keeper request have no difficulty concept — only a Keeper-registered plain skill_check
    # can set this above "regular".
    madness_trigger = False  # only set True for the INT check chained onto a >=5 SAN loss
    madness_realtime = True
    major_wound_trigger = False  # the CON check chained onto a major wound: unlike madness, success is
    # an ordinary good outcome, so it flows through the normal Luck path; only the narration needs to know.
    is_counter = False
    if choice is not None:
        skill_name, value, bonus, penalty = choice.skill_name, choice.value, choice.bonus, choice.penalty
        display_label = choice.display_label
        attacker_tier = choice.attacker_tier
        ranged_attacker = choice.ranged_attacker
        is_counter = choice.is_counter
    else:
        if not pending:
            return outcome_for(
                user_id, roll.audience_entry,
                reply_text="目前沒有待處理的檢定。請先描述行動讓 Keeper 建立檢定，再用 /coc check 或按鈕擲骰。",
            )
        skill_name, value, bonus, penalty = (
            pending["skill"], pending["skill_value"], pending["bonus_dice"], pending["penalty_dice"],
        )
        is_pushed = bool(pending.get("pushed", False))
        difficulty = pending.get("difficulty", "regular")
        madness_trigger = bool(pending.get("madness_trigger", False))
        madness_realtime = bool(pending.get("madness_realtime", True))
        major_wound_trigger = bool(pending.get("major_wound_trigger", False))
        display_label = None
    value = int(value or 0)
    bonus = int(bonus or 0)
    penalty = int(penalty or 0)

    if madness_trigger:
        return _settle_madness(roll, value, bonus, penalty, difficulty, madness_realtime)

    kind: luck_policy.CheckKind = "major_wound" if major_wound_trigger else "choice" if choice is not None else "skill"
    rolled = rules.roll_skill(
        roll.dice_port, skill_value=value, bonus_dice=bonus, penalty_dice=penalty, difficulty=difficulty,
        luck_balance=char.luck,
        luck_allowed=luck_policy.luck_allowed(kind, pushed=is_pushed),
    )
    skill_result = rolled.result
    entry = roll.pending_entry or {}

    # Luck-spend is offered whenever there is at least one tier-improving option
    # the player can afford; there is no cost cap (see
    # docs/specs/enhancement/enhancement-luck-buyup-always-offered.md).
    if rolled.luck_options:
        decision = {
            **audience(user_id, roll.audience_entry),
            "decision_id": new_decision_id(), "check_id": roll.check_id, "timeline_id": roll.timeline_id,
            **_origin_fields(state),
            "action_context": roll.action_context,
            "skill_name": skill_name, "display_label": display_label, "is_counter": is_counter,
            "value": value, "roll": skill_result.roll, "bonus_dice": bonus, "penalty_dice": penalty,
            "original_tier": skill_result.tier, "attacker_tier": attacker_tier, "difficulty": difficulty,
            "options": [{"tier": o.tier, "cost": o.cost} for o in rolled.luck_options],
            "major_wound_trigger": major_wound_trigger,
            "ranged_attacker": ranged_attacker,
            "opposed": entry.get("opposed"),
            "player_declaration": entry.get("player_declaration", ""),
            "action_basis": entry.get("action_basis", ""),
            "consequences": entry.get("consequences", []),
            "medical_context": dict(entry.get("medical_context") or {}),
        }
        if entry.get("source_check_id"):
            # A triggered check keeps pointing at the settled check that made it necessary.
            decision["source_check_id"] = entry["source_check_id"]
            decision["source_event_id"] = entry.get("source_event_id", "")
        state.pending_luck_decisions[user_id] = decision
        options_text = "、".join(
            f"花 {o.cost} 點 Luck → {narration.CHECK_TIER_ZH[o.tier]}" for o in rolled.luck_options
        )
        dice_note = f"（獎勵骰x{bonus}）" if bonus else f"（懲罰骰x{penalty}）" if penalty else ""
        check_label = (
            f"選擇「{display_label}」（{skill_name}）" if display_label is not None else f"「{skill_name}」"
        )
        attacker_note = (
            f"\n⚔️ 攻擊方擲出 → {narration.CHECK_TIER_ZH[attacker_tier]}" if attacker_tier is not None else ""
        )
        return outcome_for(
            user_id, roll.audience_entry,
            reply_text=(
                f"🎲 {char.name} 的{check_label}檢定：{value}%{dice_note}，擲出 {skill_result.roll} → "
                f"{narration.tier_zh_for_result(skill_result)}{attacker_note}\n"
                f"目前 Luck {char.luck} 點，要花 Luck 買到更好的結果嗎？可選：{options_text}\n"
                f"（點下面按鈕，或輸入「/coc luck skip」維持目前結果、「/coc luck regular/hard/extreme」花費對應點數）"
            ),
            check_id=roll.check_id, timeline_id=roll.timeline_id, action_context=roll.action_context,
            decision_id=decision["decision_id"], changed=True,
        )

    # Roll the ranged attacker's shot exactly once here (see
    # rules.resolve_ranged_defense_outcome) — reused below for both the
    # narration and the split feedback instead of letting each side call it.
    ranged_opposed_text = (
        rules.resolve_ranged_defense_outcome(char.name, skill_result.success, ranged_attacker, roll.dice_port)
        if ranged_attacker is not None else None
    )
    if major_wound_trigger and not skill_result.success:
        rules.apply_major_wound_failure(char)
    roll_line, keeper_message = narration.build_check_narration(
        char, skill_name, display_label, value, skill_result, bonus, penalty, attacker_tier=attacker_tier,
        major_wound_trigger=major_wound_trigger, ranged_opposed_text=ranged_opposed_text, is_counter=is_counter,
    )
    opposed_text = ""
    if attacker_tier is not None:
        opposed_text = narration.describe_opposed_outcome(char.name, is_counter, skill_result.tier, attacker_tier)
    elif ranged_opposed_text:
        opposed_text = ranged_opposed_text
    scenario_opposed = opposed_checks.resolve(entry.get("opposed"), skill_result.tier)
    if scenario_opposed:
        opposed_text = opposed_checks.public_text(scenario_opposed)
        roll_line += "\n" + opposed_text
        keeper_message += "\n" + opposed_text
    roll_feedback_text, keeper_header = narration.build_split_check_feedback(
        char.name, display_label or skill_name, str(value), skill_result.roll,
        narration.tier_zh_for_result(skill_result), opposed_text,
    )
    roll.reconcile()
    return roll.settled(
        roll_line=roll_line, keeper_message=keeper_message,
        roll_feedback_text=roll_feedback_text, keeper_header=keeper_header,
        resolved_event=events.event_seed(
            check_id=roll.check_id, timeline_id=roll.timeline_id, owner_id=user_id,
            character_id=char.character_id, investigator=char.name, skill=skill_name, skill_value=value,
            roll=skill_result.roll, difficulty=difficulty,
            outcome=_outcome_label(skill_result, scenario_opposed),
            before=roll.attributes_before, tracked_roll_fields=(),
            check_context=roll.pending_entry, opposed_outcome=scenario_opposed,
            success=(scenario_opposed["winner"] == "player" if scenario_opposed else skill_result.success),
        ),
    )


def _settle_madness(
    roll: _PlayerRoll, value: int, bonus: int, penalty: int, difficulty: str, realtime: bool,
) -> CheckOutcome:
    """The INT check chained onto a >=5 SAN loss.

    A success here means rolling a real madness table, not a plain good outcome,
    and Luck is never offered: spending it to pass would push toward the worse
    outcome for the character.
    """
    char = roll.char
    skill_result = roll.dice_port.skill_check(
        value, bonus_dice=bonus, penalty_dice=penalty, required_tier=difficulty,
    )
    tier_zh = narration.tier_zh_for_result(skill_result)
    if skill_result.success:
        madness = roll.dice_port.roll_madness(realtime=realtime)
        roll_line = (
            f"🎲 {char.name} 的 INT 檢定：{value}%，擲出 {skill_result.roll} → {tier_zh}\n"
            f"💥 觸發短暫瘋狂（Bout of Madness）！症狀擲骰 {madness['roll']} → 「{madness['symptom']}」"
            f"（持續約{madness['duration']}）"
        )
        keeper_message = (
            f"（{char.name} 的 INT 檢定{tier_zh}，觸發了短暫瘋狂：症狀是「{madness['symptom']}」"
            f"——{madness['guidance']}，持續約{madness['duration']}。這是已經確定的結果，"
            f"請照這個症狀具體描述角色接下來的失常行為，不要自己另外編一個症狀，也不要忽略這個結果。）"
        )
    else:
        roll_line = (
            f"🎲 {char.name} 的 INT 檢定：{value}%，擲出 {skill_result.roll} → {tier_zh}\n"
            "（INT 檢定失敗，勉強壓下這股衝擊，沒有當場失常）"
        )
        keeper_message = (
            f"（{char.name} 的 INT 檢定{tier_zh}，沒有觸發短暫瘋狂——角色勉強壓下了這股衝擊，"
            f"不需要描述任何失常行為，可以正常繼續劇情，但可以帶一點事後的心理陰影或後怕細節。）"
        )
    roll_feedback_text, keeper_header = narration.build_split_check_feedback(
        char.name, "INT", str(value), skill_result.roll, tier_zh
    )
    roll.reconcile()
    return roll.settled(
        roll_line=roll_line, keeper_message=keeper_message,
        roll_feedback_text=roll_feedback_text, keeper_header=keeper_header,
        resolved_event=events.event_seed(
            check_id=roll.check_id, timeline_id=roll.timeline_id, owner_id=roll.user_id,
            character_id=char.character_id, investigator=char.name, skill="INT", skill_value=value,
            roll=skill_result.roll, difficulty=difficulty,
            outcome=_outcome_label(skill_result), before=roll.attributes_before, tracked_roll_fields=(),
            caused_by_check_id=str((roll.pending_entry or {}).get("caused_by_check_id") or ""),
        ),
    )


# --------------------------------------------------------------------------
# A player's Luck decision
# --------------------------------------------------------------------------

def resolve_luck_decision(
    state: GroupState, user_id: str, choice: str, *,
    dice_port: DicePort = DEFAULT_DICE, managed: ManagedChecks | None = None,
) -> CheckOutcome:
    """Settle an open Luck decision: keep the roll (``skip``) or buy a better tier.

    The balance is read from the latest state here, so Luck spent by another
    action since the decision opened refuses the spend with nothing deducted.
    """
    audience_entry = dict(state.pending_luck_decisions.get(user_id) or {})
    owned = state.pending_luck_decisions.get(user_id) or {}
    if is_managed_entry(owned):
        if managed is None:
            return outcome_for(user_id, owned, reply_text=MANAGED_UNAVAILABLE_TEXT)
        return managed.resolve_luck(state, user_id, choice, owned)
    pending = state.pending_luck_decisions.pop(user_id, None)
    if not pending:
        return outcome_for(user_id, audience_entry, reply_text="目前沒有待決定的 Luck 花費。")
    char = state.get_active_character(user_id)
    if not char:
        return outcome_for(user_id, audience_entry, reply_text="找不到你的角色。")
    char = resource_bridge.effective(state, char)
    attributes_before = events.character_attribute_snapshot(char)

    timeline_id = _timeline(state)
    pending_timeline_id = str(pending.get("timeline_id") or "").strip()
    if pending_timeline_id and pending_timeline_id != timeline_id:
        observability.event(
            "luck.result.stale", level=logging.WARNING, reason="timeline_mismatch",
            requested_timeline_id=pending_timeline_id, current_timeline_id=timeline_id,
            owner_id_hash=observability.safe_identifier(user_id),
        )
        outcome = outcome_for(user_id, audience_entry, reply_text=STALE_LUCK_TEXT)
        outcome.changed = True  # the stale decision is dropped
        return outcome
    decision_id = effective_decision_id(user_id, pending, timeline_id)
    # Keep the narration/result identity aligned with the persisted check. Older
    # Luck entries may not have a check_id, so use the same deterministic legacy
    # derivation as the button path instead of generating a random id here.
    check_id = effective_check_id(user_id, pending, timeline_id)
    action_context = str(pending.get("action_context", "")).strip()[:240]
    if not action_context:
        action_context = _recent_player_action(state)

    spend = luck_policy.choose(pending["options"], choice, char.luck, pending["original_tier"])
    if spend == "invalid_option":
        state.pending_luck_decisions[user_id] = pending  # not a valid option — put it back
        options_text = "、".join(f"{o['tier']}（{o['cost']} 點）" for o in pending["options"])
        return outcome_for(user_id, audience_entry, reply_text=f"這不是有效的選項，可選：{options_text}、skip")
    if spend == "insufficient_luck":
        cost = next(o["cost"] for o in pending["options"] if o["tier"] == choice)
        state.pending_luck_decisions[user_id] = pending
        return outcome_for(
            user_id, audience_entry, reply_text=f"目前 Luck 只有 {char.luck} 點，不足以花費 {cost} 點。",
        )
    luck_spent, tier = spend.cost, spend.tier
    char.luck -= luck_spent

    required_tier = pending.get("difficulty", "regular")
    r = rules.settle_with_luck(pending, tier)
    # Roll the ranged attacker's shot exactly once here, using the FINAL
    # (post-Luck) success — see rules.resolve_ranged_defense_outcome.
    ranged_attacker = pending.get("ranged_attacker")
    ranged_opposed_text = (
        rules.resolve_ranged_defense_outcome(char.name, r.success, ranged_attacker, dice_port)
        if ranged_attacker is not None else None
    )
    pending_is_counter = bool(pending.get("is_counter", False))
    major_wound_trigger = bool(pending.get("major_wound_trigger", False))
    if major_wound_trigger and not r.success:
        rules.apply_major_wound_failure(char)
    roll_line, keeper_message = narration.build_check_narration(
        char, pending["skill_name"], pending["display_label"], pending["value"], r,
        pending["bonus_dice"], pending["penalty_dice"],
        luck_spent=luck_spent, original_tier=pending["original_tier"],
        attacker_tier=pending.get("attacker_tier"),
        major_wound_trigger=major_wound_trigger,
        ranged_opposed_text=ranged_opposed_text, is_counter=pending_is_counter,
    )
    resource_bridge.reconcile(state, char, event_id=f"{decision_id}:resources", reason="Authoritative Luck decision")
    outcome_text = narration.tier_zh_for_tier(tier, required_tier)
    if luck_spent:
        result_line = f"花費 {luck_spent} 點幸運：{pending['roll']} → {outcome_text}"
    else:
        result_line = f"維持原結果：{pending['roll']} → {outcome_text}"
    # The split feedback must carry the opposed text too (melee attacker tier or
    # the ranged shot): the player-facing line never shows roll_line.
    opposed_text = ""
    pending_attacker_tier = pending.get("attacker_tier")
    if pending_attacker_tier is not None:
        opposed_text = narration.describe_opposed_outcome(char.name, pending_is_counter, tier, pending_attacker_tier)
    elif ranged_opposed_text:
        opposed_text = ranged_opposed_text
    scenario_opposed = opposed_checks.resolve(pending.get("opposed"), tier)
    if scenario_opposed:
        opposed_text = opposed_checks.public_text(scenario_opposed)
        roll_line += "\n" + opposed_text
        keeper_message += "\n" + opposed_text
    roll_feedback_text, keeper_header = narration.build_split_check_feedback(
        char.name, pending["display_label"] or pending["skill_name"], str(pending["value"]),
        pending["roll"], outcome_text, opposed_text, result_line=result_line,
    )
    return outcome_for(
        user_id, audience_entry,
        roll_line=roll_line, keeper_message=keeper_message,
        roll_feedback_text=roll_feedback_text, keeper_header=keeper_header, should_finalize=True,
        check_id=check_id, decision_id=decision_id, timeline_id=timeline_id, action_context=action_context,
        changed=True,
        resolved_event=events.event_seed(
            check_id=check_id, timeline_id=timeline_id, owner_id=user_id,
            character_id=char.character_id, investigator=char.name, skill=pending["skill_name"],
            skill_value=pending["value"], roll=pending["roll"], difficulty=required_tier,
            outcome=_outcome_label(r, scenario_opposed, luck_spent=luck_spent),
            before=attributes_before, tracked_roll_fields=(("luck",) if luck_spent else ()),
            check_context=pending, opposed_outcome=scenario_opposed,
            success=(scenario_opposed["winner"] == "player" if scenario_opposed else r.success),
        ),
    )


# --------------------------------------------------------------------------
# The Keeper's autoroll
# --------------------------------------------------------------------------

@dataclass
class AutorollOutcome:
    """The tool payload for a check the engine rolled itself, and the event to record."""

    result: dict[str, Any]
    event_seed: dict[str, Any] | None = None


def autoroll_skill(
    state: GroupState, char: Character, *, tool_input: dict[str, Any], value: int, bonus: int,
    penalty: int, difficulty: str, pushed: bool, consequences: list[Any],
    opposed_request: dict | None, dice_port: DicePort = DEFAULT_DICE,
) -> AutorollOutcome:
    """Roll a Keeper-requested skill check now (autoroll on), exactly as a player's roll would settle.

    The opponent's roll is drawn before the investigator's, as it always has been.
    """
    state_before = events.character_attribute_snapshot(char)
    opposed_receipt = opposed_checks.roll_opponent(opposed_request, dice_port)
    rolled = rules.roll_skill(
        dice_port, skill_value=value, bonus_dice=bonus, penalty_dice=penalty, difficulty=difficulty,
        luck_balance=char.luck, luck_allowed=luck_policy.luck_allowed("skill", pushed=pushed),
    )
    roll = rolled.result
    opposed_outcome = opposed_checks.resolve(opposed_receipt, roll.tier)
    metadata = check_lifecycle.metadata(state, char.owner_id, tool_input)
    result: dict[str, Any] = {
        "ok": True,
        "resolved": True,
        "investigator": char.name,
        "skill": tool_input["skill"],
        "skill_value": value,
        "bonus_dice": bonus,
        "penalty_dice": penalty,
        "difficulty": difficulty,
        "roll": roll.roll,
        "tier": roll.tier,
        "required_tier": roll.required_tier,
        "success": (opposed_outcome["winner"] == "player") if opposed_outcome else roll.success,
        "player_check_success": roll.success,
        "check_id": metadata["check_id"],
        "timeline_id": metadata["timeline_id"],
        "action_context": metadata["action_context"],
        "player_declaration": metadata["player_declaration"],
        "action_basis": metadata["action_basis"],
        "opposed_outcome": opposed_checks.public_outcome(opposed_outcome),
        "consequences": consequences,
        "note": (
            "Keeper 已由 deterministic dice engine 擲完這次檢定；請直接依照結果敘事，不要再要求玩家擲攻擊骰或技能骰。"
            if state.autoroll_checks
            else "已建立待處理檢定；請讓玩家用 /coc check 或按鈕擲骰，收到結果後再敘事，不要自行判定。"
        ),
    }
    if rolled.luck_options:
        decision = {
            "decision_id": new_decision_id(),
            "check_id": metadata["check_id"],
            "timeline_id": metadata["timeline_id"],
            "origin_revision": state.state_revision + 1,
            "origin_turn_id": metadata["origin_turn_id"],
            "origin_request_id": metadata["origin_request_id"],
            "created_at": metadata["created_at"],
            "action_context": metadata["action_context"],
            "skill_name": tool_input["skill"],
            "display_label": None,
            "value": value,
            "roll": roll.roll,
            "bonus_dice": bonus,
            "penalty_dice": penalty,
            "original_tier": roll.tier,
            "attacker_tier": None,
            "difficulty": difficulty,
            "options": [{"tier": item.tier, "cost": item.cost} for item in rolled.luck_options],
            "major_wound_trigger": False,
            "opposed": opposed_receipt,
            "player_declaration": metadata["player_declaration"],
            "action_basis": metadata["action_basis"],
            "consequences": consequences,
        }
        state.pending_luck_decisions[char.owner_id] = decision
        result.update({
            "pending_luck": True,
            "opposed_outcome": None,
            "success": None if opposed_receipt else result["success"],
            "decision_id": decision["decision_id"],
            "luck_options": decision["options"],
            "note": (
                "Keeper 已擲完檢定，有花 Luck 買到更好結果的選項可用。玩家現在只可選擇是否"
                "花 Luck 修正；玩家不需要、也不可以自行重骰。先不要把最終成敗敘事成不可逆的結果。"
            ),
        })
        return AutorollOutcome(result)
    seed = events.event_seed(
        check_id=metadata["check_id"], timeline_id=metadata["timeline_id"], owner_id=char.owner_id,
        character_id=char.character_id, investigator=char.name, skill=tool_input["skill"],
        skill_value=value, roll=roll.roll, difficulty=difficulty,
        outcome=_outcome_label(roll, opposed_outcome), before=state_before,
        check_context={
            "consequences": consequences,
            "player_declaration": metadata["player_declaration"],
            "action_basis": metadata["action_basis"],
        },
        opposed_outcome=opposed_outcome, success=result["success"], include_medical_context=False,
    ) if state.autoroll_checks else None
    return AutorollOutcome(result, seed)


def autoroll_sanity(
    state: GroupState, char: Character, *, tool_input: dict[str, Any], loss_success: str,
    loss_failure: str, dice_port: DicePort = DEFAULT_DICE,
) -> AutorollOutcome:
    """Roll a Keeper-requested SAN check now, including the INT check a 5-point loss owes."""
    metadata = check_lifecycle.metadata(state, char.owner_id, tool_input)
    state_before = events.character_attribute_snapshot(char)
    sanity_result = dice_port.sanity_check(char.san, loss_success, loss_failure)
    char.san = sanity_result.san_after
    result: dict[str, Any] = {
        "ok": True,
        "resolved": True,
        "investigator": char.name,
        "current_san": sanity_result.san_before,
        "san_after": sanity_result.san_after,
        "loss": sanity_result.loss,
        "loss_expression": sanity_result.loss_expression,
        "roll": sanity_result.check.roll,
        "tier": sanity_result.check.tier,
        "success": sanity_result.check.success,
        "check_id": metadata["check_id"],
        "timeline_id": metadata["timeline_id"],
        "action_context": metadata["action_context"],
        "note": (
            "Keeper 已由 deterministic dice engine 擲完 SAN 檢定並更新 SAN；不要要求玩家再輸入 /coc check。"
            if state.autoroll_checks
            else "已建立待處理 SAN 檢定；請讓玩家用 /coc check 或按鈕擲骰，結果回來前不要扣 SAN。"
        ),
    }
    if sanity_result.risk_of_madness:
        int_value = resolve_skill_value(char, "INT")
        int_result = dice_port.skill_check(int_value)
        result["madness_int_check"] = {
            "skill_value": int_value,
            "roll": int_result.roll,
            "tier": int_result.tier,
            "success": int_result.success,
        }
        if int_result.success:
            result["madness"] = dice_port.roll_madness(realtime=True)
            result["note"] = (
                "Keeper 已完成 SAN 與後續 INT 檢定；損失達 5 點並觸發短暫瘋狂，"
                "請照 madness 結果敘事，不要再要求玩家擲 INT。"
            )
        else:
            result["note"] = (
                "Keeper 已完成 SAN 與後續 INT 檢定；INT 未觸發短暫瘋狂，請照結果敘事。"
            )
    resource_bridge.reconcile(
        state, char, event_id=f"{metadata['check_id']}:san", reason="Authoritative SAN check",
    )
    seed = events.event_seed(
        check_id=metadata["check_id"], timeline_id=metadata["timeline_id"], owner_id=char.owner_id,
        character_id=char.character_id, investigator=char.name, skill="SAN",
        skill_value=sanity_result.san_before, roll=sanity_result.check.roll, difficulty="regular",
        outcome=f"{sanity_result.check.tier} {'成功' if sanity_result.check.success else '失敗'}",
        before=state_before, include_medical_context=False, detail=False,
    )
    return AutorollOutcome(result, seed)
