"""Server-owned turn delivery contract; no model call or tool replay.

Observations are allowlisted tool results. Raw RAG, internal errors, private
messages, enemy statistics and reasoning are never generic fallback material.
"""
from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app import observability, presentation, spoiler_policy
from app.domain.models import MechanicResult, ObservedOutcome
from app.models import GroupState
from app.services import canonical_facts

PROVISIONAL_MARK = "【戰鬥暫定；尚未結算】"
BLOCKED_NOTICE = "回覆需要核對後才能安全顯示。已結算的結果與待處理選擇仍保留；請查看目前狀態，勿重做這次行動。"
_COUNT = {"一": 1, "兩": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def distinct_lines(lines: list[str]) -> list[str]:
    """The same line said once. A line with and without the provisional mark is one line, kept with the mark."""
    kept: dict[str, str] = {}
    for line in lines:
        base = line.replace(PROVISIONAL_MARK, "")
        if base not in kept or PROVISIONAL_MARK in line:
            kept[base] = line
    return list(kept.values())


def is_private(entry: dict) -> bool:
    return entry.get("visibility", entry.get("audience", "public")) != "public"


def observe_tool(name: str, result: dict, number: int, arguments: dict | None = None) -> ObservedOutcome:
    text = ""
    args = arguments or {}
    if result.get("ok"):
        if name in {"roll_dice", "roll_weapon_damage", "roll_impaling_damage"} and "total" in result:
            text = f"骰子已結算：{result.get('expression', '傷害骰')}，總值 {result.get('total')}。"
        elif name in {"add_carried_item", "remove_carried_item"} and (result.get("item") or args.get("item")):
            # The entry as the pack spells it (the receipt's ``item``: "Knife" for a "knife" already held, the stored
            # text a partial name removed), judged the way the tool matched it, so this never contradicts the receipt.
            item = str(result.get("item") or args["item"]).strip()
            present = any(str(entry).strip().casefold() == item.casefold() for entry in result.get("carried_items", []))
            text = f"{result.get('investigator', '調查員')} 的背包已確認{'包含' if present else '不含'}「{item}」。"
        elif name == "transfer_item" and result.get("item") and not result.get("replayed"):
            quantity = int(result.get("quantity") or 1)
            amount = f" {quantity} 份" if quantity > 1 else ""
            text = f"{result.get('from', '調查員')} 已把{amount}「{result['item']}」交給 {result.get('to', '調查員')}。"
        elif name in {"skill_check", "sanity_check"} and result.get("resolved"):
            text = (f"{result.get('investigator', '調查員')} 的檢定已結算："
                    f"骰值 {result.get('roll')}，等級 {presentation.tier_label(str(result.get('tier')))}。")
        elif name == "apply_resolved_check_damage":
            text = (f"{result.get('investigator', '調查員')} 已結算傷害 {result.get('damage')}，"
                    f"HP {result.get('hp_before')} → {result.get('hp_after')}。")
        elif name == "create_triggered_check" and result.get("pending"):
            text = f"{result.get('investigator', '調查員')} 還要擲{result.get('skill', '後續')}檢定。"
        elif name == "adjust_ammo":
            text = f"{result.get('investigator')} 的 {result.get('weapon')} 彈藥已更新為 {result.get('ammo')}。"
        elif name == "adjust_character":
            text = f"{result.get('investigator')} 的 {result.get('field')} 已更新為 {result.get('value', result.get('new_value'))}。"
        elif name in {"record_clue", "record_established_fact"}:
            record = result.get("record") or {}
            if record.get("visibility", "public") == "public":
                text = str(record.get("text", ""))
        elif name == "set_skill":
            text = f"{result.get('investigator')} 的 {result.get('skill')} 已設為 {result.get('value')}。"
        elif name in {"add_status_tag", "remove_status_tag"}:
            text = f"{result.get('investigator')} 的狀態標記已更新：{'、'.join(result.get('status_tags', [])) or '無'}。"
        elif name == "clear_pending_check" and result.get("cleared"):
            text = f"{result.get('investigator', '調查員')} 尚未擲骰的檢定已取消。"
        elif name in {"start_combat", "initialize_combat", "end_combat", "advance_combat_turn", "add_npc_to_combat", "declare_combat_action", "run_combat_action", "run_enemy_combat_plan", "submit_combat_choice", "preview_combat_settlement", "confirm_combat_settlement", "rollback_combat", "correct_combat_event", "reconcile_combat_baseline", "change_combat_initiative", "declare_combat_effect", "run_combat_effect", "stop_combat_effect", "resolve_combat_ruling", "reconcile_combat_correction", "process_postcombat_obligations", "stabilize_investigator", "request_stabilization_check"}:
            # Nothing public: the narration tells the fight, and a fixed 「戰鬥機制操作已記錄」 line read as engine noise
            # after every combat turn (the Haunting runs, 2026-10-09). Enemy sheets stay out of a generic dump too.
            text = ""
    if result.get('provisional') and text:
        text += PROVISIONAL_MARK
    record = result.get("record") if name in {"record_clue", "record_established_fact"} else None
    fact_ref = (str(record.get("fact_id", "")) if isinstance(record, dict)
                and record.get("verification_status") == "verified" else "")
    return ObservedOutcome(f"tool:{number}", name, bool(result.get("ok")), text,
                           "public" if text else "internal", fact_ref=fact_ref)


@dataclass(frozen=True)
class InteractionRef:
    kind: str
    owner_id: str
    identity: str
    timeline_id: str
    audience: str = "public"
    recipient_id: str = ""
    roll: int | None = None

    @property
    def instruction(self) -> str:
        if self.kind == "luck":
            value = f" {self.roll} " if self.roll is not None else ""
            return f"既有骰值{value}仍等待 Luck 決定，請使用 Luck 按鈕或 /coc luck skip；不要重擲。"
        return "已有待處理檢定／選擇，請使用檢定按鈕或 /coc check。"


@dataclass
class DeliveryEnvelope:
    output_id: str
    audience: str
    recipient_id: str
    narrative: str
    authorized_facts: list[ObservedOutcome] = field(default_factory=list)
    verified_fact_refs: list[canonical_facts.CanonicalFactRef] = field(default_factory=list)
    interactions: list[InteractionRef] = field(default_factory=list)
    canonical_policy: str = "verified_turn_output"
    status: str = "candidate"

    def projected_text(self) -> str:
        lines = [fact.public_text for fact in self.authorized_facts if fact.public_text]
        lines.extend(fact.text for fact in self.verified_fact_refs)
        lines.extend(dict.fromkeys(ref.instruction for ref in self.interactions))
        return "\n".join(distinct_lines(lines))

    def render(self) -> str:
        additions = [line for line in self.projected_text().splitlines() if line not in self.narrative]
        return "\n\n".join(part for part in (self.narrative.strip(), "\n".join(additions)) if part)


def interaction_refs(state: GroupState) -> list[InteractionRef]:
    # Use the same legacy identity helpers as Discord; never recreate checks.
    from app.check_identity import effective_check_id, effective_decision_id
    refs = []
    timeline = state.timeline_id or f"legacy-{state.group_id}"
    for kind, collection in (("check", state.pending_checks), ("luck", state.pending_luck_decisions)):
        for owner, entry in collection.items():
            entry_timeline = entry.get("timeline_id") or timeline
            if entry_timeline != timeline:
                continue
            private = is_private(entry)
            identity = (effective_check_id(owner, entry, timeline) if kind == "check"
                        else effective_decision_id(owner, entry, timeline))
            refs.append(InteractionRef(kind, owner, identity, timeline,
                                       "player_private" if private else "public", owner if private else "",
                                       entry.get("roll") if isinstance(entry.get("roll"), int) else None))
    return refs


def public_mechanic(result: MechanicResult | None, state: GroupState) -> MechanicResult | None:
    if result is None:
        return None
    projected = deepcopy(result)
    status = projected.check_status
    if _is_private_wait(status.get("pending"), state.pending_checks):
        status["pending"] = None
    if _is_private_wait(status.get("pending_luck"), state.pending_luck_decisions):
        status["pending_luck"] = None
    return projected


def _is_private_wait(pending: dict[str, Any] | None, entries: dict[str, dict]) -> bool:
    """Whether a pending check or Luck decision, or the live entry it mirrors, is private."""
    return bool(pending and (is_private(pending) or any(
        is_private(entry) and entry.get("check_id") == pending.get("check_id")
        for entry in entries.values()
    )))


def _proven_hard_fact_conflict(narrative: str, facts: list[canonical_facts.CanonicalFactRef]) -> bool:
    """Check only typed, explicit entity/quantity/location/identity conflicts.

    This deliberately does not attempt general Chinese prose interpretation.
    """
    for fact in facts:
        entity = fact.constraints.get("entity")
        forbidden_names = fact.constraints.get("forbidden_names", [])
        if isinstance(forbidden_names, list) and any(
            isinstance(name, str) and name and name in narrative for name in forbidden_names
        ):
            return True
        location = fact.constraints.get("location")
        if isinstance(entity, str) and entity and isinstance(location, str) and location:
            # Only known mutually exclusive positions are safe to compare.
            opposites = {"櫥櫃內": ("櫥櫃下", "櫥櫃外"), "櫥櫃下": ("櫥櫃內",)}
            if any(entity in sentence and any(opposite in sentence for opposite in opposites.get(location, ()))
                   for sentence in re.split(r"[，。；\n]", narrative)):
                return True
        quantity = fact.constraints.get("quantity")
        unit = fact.constraints.get("unit")
        if not isinstance(entity, str) or not entity or not isinstance(quantity, int) or not isinstance(unit, str) or not unit:
            continue
        pattern = rf"(?P<count>\d+|[一二兩三四五六七八九]){re.escape(unit)}(?:[\w\u4e00-\u9fff]{{0,3}})?{re.escape(entity)}"
        for match in re.finditer(pattern, narrative):
            raw = match.group("count")
            observed = int(raw) if raw.isdigit() else _COUNT.get(raw)
            if observed is not None and observed != quantity:
                return True
    return False


def validate_delivery_contract(envelope: DeliveryEnvelope, text: str, state: GroupState) -> bool:
    """Validation only. Exact server projection and live identities, no rewrite."""
    live = interaction_refs(state)
    if any(ref not in live for ref in envelope.interactions):
        return False
    if any(fact.audience != envelope.audience or fact.recipient_id != envelope.recipient_id
           for fact in envelope.authorized_facts):
        return False
    live_facts = {fact.fact_id: fact for fact in canonical_facts.project(
        state, recipient_id=envelope.recipient_id,
    )}
    if any(live_facts.get(fact.fact_id) != fact for fact in envelope.verified_fact_refs):
        return False
    if any(ref.audience != envelope.audience or ref.recipient_id != envelope.recipient_id
           for ref in envelope.interactions):
        return False
    return all(line in text for line in envelope.projected_text().splitlines())


def finalize(message, narrative: str) -> tuple[str, list[tuple[str, str]]]:
    state = message.payload["state"]
    outcomes = list(message.payload.get("observed_outcomes", []))
    resolved = message.payload.get("resolved_check_context")
    if resolved:
        private_result = is_private(resolved)
        outcomes.append(ObservedOutcome(
            f"resolved-check:{resolved.get('check_id', 'current')}", "resolved_check", True,
            f"已結算檢定：骰值 {resolved.get('roll', '未知')}，結果為「{presentation.outcome_label(str(resolved.get('outcome', '未知')))}」。",
            "player_private" if private_result else "public",
            message.payload.get("user_id", "") if private_result else "",
        ))
    if resolved and is_private(resolved):
        # The model saw a private result. Its free-form prose cannot become a
        # public artifact merely because the pending decision is now cleared.
        narrative = "請查看你的私訊。"
    refs = interaction_refs(state)
    due_ids = {outcome.fact_ref for outcome in outcomes if outcome.success and outcome.fact_ref}
    live_facts = canonical_facts.project(state)
    due_public = [fact for fact in live_facts if fact.fact_id in due_ids and fact.visibility == "public"]
    due_valid_ids = {fact.fact_id for fact in due_public}
    public_outcomes = [outcome for outcome in outcomes if outcome.audience == "public"
                       and (not outcome.fact_ref or outcome.fact_ref in due_valid_ids)]
    envelope = DeliveryEnvelope(uuid4().hex, "public", "", narrative,
                                public_outcomes,
                                due_public,
                                [ref for ref in refs if ref.audience == "public"])
    protected = spoiler_policy.collect_protected_terms(state)
    for entries in (state.pending_checks, state.pending_luck_decisions):
        for entry in entries.values():
            if is_private(entry):
                protected.extend(str(entry[key]) for key in ("skill", "skill_name", "action_context") if entry.get(key))
    result = envelope.render()
    conflict = _proven_hard_fact_conflict(narrative, due_public)
    safe = spoiler_policy.sanitize_public_text(result, protected).is_safe and not conflict
    valid = validate_delivery_contract(envelope, result, state)
    if not (safe and valid):
        # Drop unsafe narrative, retaining the same required projected facts
        # and controls. An unsafe projection blocks delivery instead of silently
        # shrinking the contract.
        envelope.narrative = "敘事暫時無法安全顯示，以下為已確認的結果。"
        result = envelope.render()
        if (spoiler_policy.sanitize_public_text(result, protected).is_safe
                and validate_delivery_contract(envelope, result, state)):
            envelope.status = "projected_fallback"
        else:
            envelope.status = "blocked"
            result = BLOCKED_NOTICE
    else:
        envelope.status = "projected_fallback" if message.payload.get("narration_failed") else "passed"
    message.payload["delivery_envelope"] = envelope
    observability.event("turn.delivery_contract", status=envelope.status,
                        fact_count=len(envelope.authorized_facts) + len(envelope.verified_fact_refs), control_count=len(refs),
                        hard_fact_conflict=conflict,
                        narrative_complete=not message.payload.get("narration_failed", False))
    private = []
    private_owners = [ref.recipient_id for ref in refs if ref.audience == "player_private"]
    private_owners.extend(fact.recipient_id for fact in outcomes if fact.audience == "player_private")
    for owner in dict.fromkeys(private_owners):
        private_envelope = DeliveryEnvelope(uuid4().hex, "player_private", owner, "",
                                            authorized_facts=[fact for fact in outcomes if fact.audience == "player_private" and fact.recipient_id == owner],
                                            interactions=[ref for ref in refs if ref.recipient_id == owner])
        text = private_envelope.render()
        if validate_delivery_contract(private_envelope, text, state):
            private.append((owner, text))
    return result, private
