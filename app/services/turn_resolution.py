"""Validate Executor decisions against observed tools and the final state.

This layer never applies a model-requested mutation or retries a tool. Evidence
references establish provenance, not semantic proof of a scenario inference.
"""
from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from typing import Any

from app import observability
from app.domain.models import TURN_BLOCKER_CODES, TurnResolution
from app.models import GroupState
from app.services.turn_context import character_id

INFORMATION_QUERY_TOOLS = frozenset({"search_scenario", "search_memory", "get_character_sheet",
                                      "get_combat_status", "search_scenario_images"})

_DISPOSITIONS = {
    "no_mechanics", "await_check", "await_luck", "deferred", "resolved_without_check",
    "resolved", "cancelled", "blocked", "incomplete",
}



def _mutation_evidence(state: GroupState, events: list[dict[str, Any]], refs: list[str], actor_name: str) -> tuple[bool, bool]:
    """Return (verified mutation, independent exact-item transfer).

    Inventory receipts must agree with final state; lookup/no-op success is not
    completion. Only a matched transfer may coexist with an unchanged old check.
    """
    inventory = []
    latest = {}
    ended = False
    purchase_effect = False
    for i, event in enumerate(events, 1):
        name, result = event['name'], event['result']
        if (name in {'add_carried_item', 'remove_carried_item', 'end_combat'}
                and (not result.get('ok') or f'tool:{i}' not in refs)):
            return False, False
        if name in {'add_carried_item', 'remove_carried_item'}:
            owner = result.get('investigator')
            before = event.get('inventory_before', {}).get(owner)
            after = result.get('carried_items')
            if before is None or not isinstance(after, list) or before == after:
                return False, False
            inventory.append(event)
            latest[owner] = after
        if name == "purchase_items":
            receipt = result.get("purchase", {})
            stored = state.commerce.get("transactions", {}).get(receipt.get("id"))
            if not result.get("ok") or f"tool:{i}" not in refs or not stored or stored != receipt or receipt.get("investigator") != actor_name:
                return False, False
            purchase_effect = True
            if receipt.get("status") == "purchased" and not result.get("duplicate"):
                owner = receipt["investigator"]
                before = event.get("inventory_before", {}).get(owner)
                after = result.get("carried_items")
                added: Counter[str] = Counter()
                for item in receipt["items"]:
                    added[item["name"]] += item["quantity"]
                if before is None or not isinstance(after, list) or Counter(after) != Counter(before) + added:
                    return False, False
                latest[owner] = after
        if name == 'end_combat':
            ended = bool(event.get('combat_active_before') and not state.combat.active)
    chars = {c.name: c for c in state.active_characters()}
    if any(owner not in chars or chars[owner].carried_items != items for owner, items in latest.items()):
        return False, False
    actor_involved = any(e['result'].get('investigator') == actor_name for e in inventory)
    transfer = False
    if len(inventory) == 2 and {e['name'] for e in inventory} == {'remove_carried_item', 'add_carried_item'}:
        # Match by operation, preserving chronological receipts and tool:N references.
        remove = next(e for e in inventory if e['name'] == 'remove_carried_item')
        add = next(e for e in inventory if e['name'] == 'add_carried_item')
        giver, receiver = remove['result'].get('investigator'), add['result'].get('investigator')
        item = remove.get('arguments', {}).get('item')
        if (remove['name'] == 'remove_carried_item' and add['name'] == 'add_carried_item'
                and giver == actor_name and receiver != giver and isinstance(item, str)
                and add.get('arguments', {}).get('item') == item):
            transfer = (
                Counter(remove['inventory_before'][giver]) - Counter(remove['result']['carried_items']) == Counter([item])
                and Counter(add['result']['carried_items']) - Counter(add['inventory_before'][receiver]) == Counter([item])
                and Counter(remove['result']['carried_items']) - Counter(remove['inventory_before'][giver]) == Counter()
                and Counter(add['inventory_before'][receiver]) - Counter(add['result']['carried_items']) == Counter()
            )
    transfer = transfer and all(e['name'] in {
        'add_carried_item', 'remove_carried_item', 'search_scenario', 'get_character_sheet',
    } for e in events)
    return bool(ended or purchase_effect or (inventory and actor_involved)), transfer

def validate_resolution(
    text: str, *, state: GroupState, user_id: str, before_pending: dict,
    before_luck: dict, tool_events: list[dict[str, Any]], has_scenario: bool,
    before_actor: dict[str, Any], before_gameplay: dict[str, Any] | None = None,
) -> TurnResolution:
    actor_id = character_id(state, user_id)
    def incomplete(reason: str, code: str) -> TurnResolution:
        return TurnResolution(actor_character_id=actor_id, reason=reason, validation_code=code)
    if not isinstance(text, str) or len(text) > 8192:
        return incomplete("裁決資料過長", "completion_too_long")
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return incomplete("未收到有效的回合裁決；已執行工具不會重做", "invalid_json")
    if not isinstance(data, dict):
        return incomplete("裁決格式不正確", "invalid_object")
    disposition = data.get("disposition")
    if not isinstance(disposition, str) or disposition not in _DISPOSITIONS or data.get("actor_character_id") != actor_id or not actor_id:
        return incomplete("裁決角色或狀態不正確", "invalid_actor_or_disposition")
    for key in ("waiting_for", "check_id", "reason"):
        if not isinstance(data.get(key, ""), str) or len(data.get(key, "")) > 600:
            return incomplete("裁決欄位不正確", "invalid_fields")
    blocker = data.get("blocker_code", "")
    if not isinstance(blocker, str) or (blocker and blocker not in TURN_BLOCKER_CODES):
        return incomplete("裁決阻擋分類不正確", "invalid_blocker_code")
    refs = data.get("evidence_refs", [])
    if not isinstance(refs, list) or len(refs) > 20 or not all(isinstance(x, str) for x in refs):
        return incomplete("裁決依據格式不正確", "invalid_evidence_format")
    valid_refs = {"state"}
    if has_scenario:
        valid_refs.add("scenario_context")
    valid_refs.update(f"tool:{i}" for i, e in enumerate(tool_events, 1) if e['result'].get('ok'))
    if not refs or not set(refs) <= valid_refs:
        observability.event("executor.resolution.invalid_evidence", reference_count=len(refs),
                            invalid_reference_count=len(set(refs) - valid_refs),
                            available_tool_count=len(tool_events),
                            has_scenario_context=has_scenario)
        return incomplete("裁決引用了不存在或失敗的依據", "invalid_evidence_reference")
    waiting = data.get("waiting_for", "")
    check_id = data.get("check_id", "")
    actor = state.get_active_character(user_id)
    if actor is None:
        return incomplete("沒有可核對的行動角色", "missing_actor")
    pending = state.pending_checks.get(user_id)
    luck = state.pending_luck_decisions.get(user_id)
    if disposition in {"await_check", "await_luck"}:
        # A turn may legitimately establish a defense/check for someone else.
        owner = next((c.owner_id for c in state.active_characters()
                      if character_id(state, c.owner_id) == (waiting or actor_id)), None)
        collection = state.pending_checks if disposition == "await_check" else state.pending_luck_decisions
        record = collection.get(owner or "", {})
        expected_id = record.get("check_id") if disposition == "await_check" else record.get("decision_id")
        if not expected_id or check_id != expected_id or record.get("timeline_id", state.timeline_id) != state.timeline_id:
            return incomplete("要求處理的檢定／Luck 決定不存在或已過期", "pending_identity_mismatch")
        if disposition == "await_check" and owner in state.pending_luck_decisions:
            return incomplete("骰已擲出，必須先處理 Luck", "luck_takes_precedence")
    elif disposition == "cancelled":
        old = before_pending.get(user_id)
        cleared = any(e['name'] == 'clear_pending_check' and e['result'].get('cleared')
                      and e['result'].get('investigator') == actor.name for e in tool_events)
        if (not old or not check_id or old.get('check_id') != check_id or pending or luck
                or before_luck.get(user_id) or not cleared
                or old.get('timeline_id', state.timeline_id) != state.timeline_id
                or not _isolated_changes(state, before_gameplay, tool_events, user_id, actor.name, 'cancelled')):
            return incomplete("尚未確認原本的未擲檢定已取消", "cancellation_not_verified")
    elif disposition == "deferred":
        current = None
        if state.combat.active and 0 <= state.combat.current_index < len(state.combat.order):
            current = state.combat.order[state.combat.current_index]
        waiting_owner = next((c.owner_id for c in state.active_characters()
                              if character_id(state, c.owner_id) == waiting), None)
        actual_wait = bool(waiting and waiting != actor_id and (
            (current and waiting in {current.character_id, current.combatant_id})
            or (waiting_owner and (waiting_owner in state.pending_checks or waiting_owner in state.pending_luck_decisions))
        ))
        # Never present a partially spent shot/action as merely waiting.
        now = actor_snapshot(state, user_id)
        if (not actual_wait or pending or luck or now != before_actor
                or any(e.get("actor_changed") for e in tool_events)
                or not _isolated_changes(state, before_gameplay, tool_events, user_id, actor.name, "deferred")):
            return incomplete("暫緩裁決與目前順位或已提交變更不一致", "deferral_not_verified")
    elif disposition in {"resolved", "resolved_without_check", "no_mechanics", "blocked"}:
        mutation, transfer = _mutation_evidence(state, tool_events, refs, actor.name)
        if disposition in {"resolved", "resolved_without_check"}:
            if any(e['name'] in {'add_carried_item', 'remove_carried_item', 'end_combat', 'purchase_items'} for e in tool_events) and not mutation:
                return incomplete("物品或戰鬥變更缺少完整且可核對的工具證據", "inventory_or_combat_not_verified")
            # A newly created/replaced check for any participant is still work.
            changed_wait = any(before_pending.get(owner) != record for owner, record in state.pending_checks.items())
            changed_luck = any(before_luck.get(owner) != record for owner, record in state.pending_luck_decisions.items())
            if changed_wait or changed_luck or ((pending or luck) and not (transfer and not luck)):
                return incomplete("本次仍有待處理檢定或 Luck；既有檢定只允許獨立且已驗證的物品交接", "unfinished_check_or_luck")
        rolled = any(
            f"tool:{i}" in refs and e["result"].get("ok") and e["result"].get("resolved")
            and e["result"].get("investigator") == actor.name
            and e["result"].get("timeline_id") == state.timeline_id
            for i, e in enumerate(tool_events, 1)
        )
        if disposition == "resolved" and not (rolled or mutation):
            return incomplete("沒有可核對的結算或狀態變更結果", "missing_resolved_effect")
        if disposition == "resolved" and mutation and not rolled:
            disposition = "resolved_without_check"
        scenario_evidence = "scenario_context" in refs or any(
            f"tool:{i}" in refs and e['name'] == 'search_scenario' and e['result'].get('results')
            for i, e in enumerate(tool_events, 1)
        )
        if disposition == "resolved_without_check" and not (mutation or scenario_evidence):
            return incomplete("免檢定完成缺少劇本或可核對的工具變更依據", "missing_scenario_or_mutation_evidence")
        if disposition == "no_mechanics" and tool_events and (
            any(e["name"] not in INFORMATION_QUERY_TOOLS or not e["result"].get("ok") for e in tool_events)
            or not _isolated_changes(state, before_gameplay, tool_events, user_id, actor.name, "no_mechanics")
        ):
            return incomplete("已有工具操作，不能當作沒有機制", "no_mechanics_has_effects")
    return TurnResolution(
        disposition=disposition, actor_character_id=actor_id, waiting_for=waiting,
        check_id=check_id, reason=data.get("reason", "")[:600], evidence_refs=list(refs),
        validation_code="model_incomplete" if disposition == "incomplete" else "validated",
        blocker_code=blocker if disposition in {"blocked", "incomplete"} else "",
    )


def actor_snapshot(state: GroupState, user_id: str) -> dict[str, Any]:
    from copy import deepcopy

    char = state.get_active_character(user_id)
    if char is None:
        return {}
    return deepcopy({key: getattr(char, key) for key in (
        "hp", "mp", "san", "luck", "carried_items", "cash_balances", "weapons", "status_tags",
    )})


# Diagnostic/provider bookkeeping does not constitute a game action. All other
# persisted fields (including every character and enemy card) are compared.
_NON_GAMEPLAY_FIELDS = {
    "state_revision", "log", "kp_ooc_log", "campaign_summary",
    "openai_previous_response_id", "openai_previous_response_timeline_id",
}


def gameplay_snapshot(state: GroupState) -> dict[str, Any]:
    return deepcopy({key: value for key, value in state.to_dict().items()
                     if key not in _NON_GAMEPLAY_FIELDS})


def _setup_only(before: dict, after: dict, event: dict) -> bool:
    """Allow encounter initialization, never damage or advancing an existing turn."""
    if event["name"] not in {"start_combat", "add_npc_to_combat"} or not event["result"].get("ok"):
        return False
    if {k: v for k, v in before.items() if k != "combat"} != {k: v for k, v in after.items() if k != "combat"}:
        return False
    old, new = before["combat"], after["combat"]
    if not new["active"] or new["round_number"] != 1 or new["effects"] or new["plans"]:
        return False
    if old["active"]:
        old_order = {c["combatant_id"]: c for c in old["order"]}
        new_order = {c["combatant_id"]: c for c in new["order"]}
        if any(new_order.get(cid) != c for cid, c in old_order.items()):
            return False
        if any(new["enemy_cards"].get(cid) != card for cid, card in old["enemy_cards"].items()):
            return False
        if old["order"] and (not new["order"] or
                old["order"][old["current_index"]]["combatant_id"] !=
                new["order"][new["current_index"]]["combatant_id"]):
            return False
    return True


def _isolated_changes(state: GroupState, before: dict | None, events: list[dict],
                      owner: str, actor_name: str, disposition: str) -> bool:
    # Production always supplies snapshots. Without evidence, fail closed.
    if before is None:
        return False
    expected = before
    for event in events:
        previous, current = event.get("gameplay_before"), event.get("gameplay_after")
        if previous != expected or current is None:
            return False
        allowed = previous == current
        if disposition == "cancelled" and event["name"] == "clear_pending_check":
            result = event["result"]
            if result.get("ok") and result.get("cleared") and result.get("investigator") == actor_name:
                cancelled = deepcopy(previous)
                cancelled["pending_checks"].pop(owner, None)
                allowed = current == cancelled
        if disposition == "deferred" and not before["combat"]["active"]:
            allowed = allowed or _setup_only(previous, current, event)
        if not allowed:
            return False
        expected = current
    return gameplay_snapshot(state) == expected
