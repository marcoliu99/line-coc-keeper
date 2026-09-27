"""Server-owned turn delivery contract; no model call or tool replay.

Observations are allowlisted tool results. Raw RAG, internal errors, private
messages, enemy statistics and reasoning are never generic fallback material.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from uuid import uuid4

from app import observability, spoiler_policy
from app.domain.models import MechanicResult, ObservedOutcome
from app.models import GroupState
from app.services import purchases

BLOCKED_NOTICE = "回覆需要核對後才能安全顯示。已結算的結果與待處理選擇仍保留；請查看目前狀態，勿重做這次行動。"


def is_private(entry: dict) -> bool:
    return entry.get("visibility", entry.get("audience", "public")) != "public"


def observe_tool(name: str, result: dict, number: int, arguments: dict | None = None) -> ObservedOutcome:
    text = ""
    args = arguments or {}
    if result.get("ok"):
        if name in {"roll_dice", "roll_weapon_damage", "roll_impaling_damage"} and "total" in result:
            text = f"骰子已結算：{result.get('expression', '傷害骰')}，總值 {result.get('total')}。"
        elif name in {"add_carried_item", "remove_carried_item"} and args.get("item"):
            item = args["item"].strip()
            present = item in result.get("carried_items", [])
            text = f"{result.get('investigator', '調查員')} 的背包已確認{'包含' if present else '不含'}「{item}」。"
        elif name == "purchase_items" and result.get("purchase"):
            text = purchases.describe(result["purchase"])
        elif name in {"skill_check", "sanity_check"} and result.get("resolved"):
            text = (f"{result.get('investigator', '調查員')} 的檢定已結算："
                    f"骰值 {result.get('roll')}，等級 {result.get('tier')}。")
        elif name in {"apply_combat_damage", "apply_final_combat_damage", "damage_combatant"} and "final_damage" in result:
            text = f"{result.get('name', result.get('target', '目標'))} 已結算傷害 {result['final_damage']}。"
        elif name == "damage_combatant":
            # Enemy HP may already have been removed by the public tool projection.
            if "hp" in result and "hp_before" in result:
                text = f"{result.get('name', '目標')} 已結算治療，HP {result['hp_before']} → {result['hp']}。"
            else:
                text = f"{result.get('name', '目標')} 的治療已結算。"
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
        elif name in {"start_combat", "end_combat", "advance_combat_turn", "resolve_enemy_action", "add_npc_to_combat", "add_combat_effect"}:
            # Do not expose enemy sheets/ability names through a generic dump.
            text = "戰鬥機制操作已記錄；後續以目前戰鬥狀態為準。"
    return ObservedOutcome(f"tool:{number}", name, bool(result.get("ok")), text,
                           "public" if text else "internal")


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
    interactions: list[InteractionRef] = field(default_factory=list)
    canonical_policy: str = "verified_turn_output"
    status: str = "candidate"

    def projected_text(self) -> str:
        lines = [fact.public_text for fact in self.authorized_facts if fact.public_text]
        lines.extend(dict.fromkeys(ref.instruction for ref in self.interactions))
        return "\n".join(lines)

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
    for key, entries in (("pending", state.pending_checks), ("pending_luck", state.pending_luck_decisions)):
        pending = projected.check_status.get(key)
        if pending and (is_private(pending) or any(
            is_private(entry) and entry.get("check_id") == pending.get("check_id")
            for entry in entries.values()
        )):
            projected.check_status[key] = None
    return projected


def validate_delivery_contract(envelope: DeliveryEnvelope, text: str, state: GroupState) -> bool:
    """Validation only. Exact server projection and live identities, no rewrite."""
    live = interaction_refs(state)
    if any(ref not in live for ref in envelope.interactions):
        return False
    if any(fact.audience != envelope.audience or fact.recipient_id != envelope.recipient_id
           for fact in envelope.authorized_facts):
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
            f"已結算檢定：骰值 {resolved.get('roll', '未知')}，結果為「{resolved.get('outcome', '未知')}」。",
            "player_private" if private_result else "public",
            message.payload.get("user_id", "") if private_result else "",
        ))
    if resolved and is_private(resolved):
        # The model saw a private result. Its free-form prose cannot become a
        # public artifact merely because the pending decision is now cleared.
        narrative = "請查看你的私訊。"
    refs = interaction_refs(state)
    envelope = DeliveryEnvelope(uuid4().hex, "public", "", narrative,
                                [o for o in outcomes if o.audience == "public"],
                                [ref for ref in refs if ref.audience == "public"])
    protected = spoiler_policy.collect_protected_terms(state)
    for entries in (state.pending_checks, state.pending_luck_decisions):
        for entry in entries.values():
            if is_private(entry):
                protected.extend(str(entry[key]) for key in ("skill", "skill_name", "action_context") if entry.get(key))
    result = envelope.render()
    safe = spoiler_policy.sanitize_public_text(result, protected).is_safe
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
                        fact_count=len(envelope.authorized_facts), control_count=len(refs),
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
