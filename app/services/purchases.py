"""Atomic purchase receipts. Travel is narrative adjudication, never map inference."""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models import GroupState

TOOL: dict[str, Any] = {
    "name": "purchase_items",
    "description": "一次提交到店裁定與購買。必須先依劇情確認已抵達、店家及商品來源；不是移動捷徑。一般花費依信用評級結算；精確現金先報價等玩家確認。不准用 add_carried_item 代替購買。",
    "input_schema": {
        "type": "object", "properties": {
            "investigator": {"type": "string"},
            "shop": {"type": "string"},
            "arrived": {"type": "boolean", "description": "行程已完成且確實可交易；僅想前往不算抵達"},
            "arrival_basis": {"type": "string", "description": "依目前劇情裁定抵達的過程；尚待檢定時不可標示抵達"},
            "source": {"type": "string", "description": "劇本／已確立事件支持店家存在及商品可取得的依據；檢索未找到就是未知"},
            "mode": {"type": "string", "enum": ["lifestyle", "cash"]},
            "affordability": {"type": "string", "description": "根據角色實際信用評級、年代、物品性質裁定為一般可負擔花費的理由；昂貴、稀有、武器不可草率放行"},
            "currency": {"type": "string", "description": "cash 模式的幣別，例如 USD；不做匯率換算"},
            "items": {"type": "array", "minItems": 1, "maxItems": 20, "items": {
                "type": "object", "properties": {
                    "name": {"type": "string"}, "quantity": {"type": "integer", "minimum": 1, "maximum": 99},
                    "unit_price": {"type": "string", "description": "cash 必填，每件單價，最多兩位小數；lifestyle 可留空"},
                }, "required": ["name", "quantity"],
            }},
        }, "required": ["investigator", "shop", "arrived", "arrival_basis", "source", "mode", "items"],
    },
}


def amount(value: Any) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,10}(?:\.\d{1,2})?", value):
        raise ValueError("金額須為非負數，最多兩位小數。")
    try:
        return int(Decimal(value) * 100)
    except (InvalidOperation, ValueError):
        raise ValueError("無效金額。") from None


def currency_code(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{1,11}", value):
        raise ValueError("請提供明確幣別代碼，例如 USD。")
    return value.upper()


def _text(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        raise ValueError(f"缺少有效的 {key}。")
    return value.strip()


def _actor(state: GroupState, name: str):
    matches = [c for c in state.active_characters() if c.name == name]
    if len(matches) != 1:
        raise ValueError("購買角色須以唯一完整名稱指定。")
    return matches[0]


def _ready(state: GroupState, owner: str) -> None:
    if state.combat.active or owner in state.pending_checks or owner in state.pending_luck_decisions:
        raise ValueError("先完成戰鬥／待處理檢定與 Luck，再裁定抵達及購買。")


def prepare(state: GroupState, data: dict, turn_key: str) -> dict:
    """Called only under keeper's reload/mutate/save lock; validates before mutation."""
    if not turn_key:
        raise ValueError("購買需要 Executor 的回合識別。")
    char = _actor(state, _text(data, "investigator"))
    normalized = {key: deepcopy(data.get(key)) for key in TOOL["input_schema"]["properties"]}
    identity = json.dumps([state.timeline_id, char.character_id, turn_key], sort_keys=True, ensure_ascii=False)
    request_hash = hashlib.sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    quote_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
    existing = state.commerce.get("transactions", {}).get(quote_id)
    if existing:
        if existing.get("request_hash") != request_hash:
            raise ValueError("本回合已有購買／報價紀錄，請勿重複結算不同內容。")
        return {"ok": True, "purchase": deepcopy(existing), "duplicate": True}
    _ready(state, char.owner_id)
    if data.get("arrived") is not True:
        raise ValueError("尚未裁定抵達店家，不能購買或入袋。")
    shop, arrival, source = (_text(data, key) for key in ("shop", "arrival_basis", "source"))
    mode = data.get("mode")
    if mode not in {"cash", "lifestyle"}:
        raise ValueError("請選擇 lifestyle 或 cash。")
    currency = currency_code(data.get("currency")) if mode == "cash" else ""
    rationale = _text(data, "affordability") if mode == "lifestyle" else ""
    items = data.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 20:
        raise ValueError("一次購買須有 1 至 20 個品項。")
    normalized_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("商品格式不正確。")  # noqa: TRY004 - boundary returns player-facing validation errors
        name = _text(item, "name")
        quantity = item.get("quantity")
        if type(quantity) is not int or not 1 <= quantity <= 99:
            raise ValueError("數量須為 1 至 99 的整數。")
        price = amount(item.get("unit_price")) if mode == "cash" else None
        normalized_items.append({"name": name, "quantity": quantity, "unit_price_minor": price})
    credit = char.skills.get("信用評級", char.skills.get("Credit Rating"))
    if mode == "lifestyle" and (type(credit) is not int or not 0 <= credit <= 99):
        raise ValueError("角色缺少有效信用評級，不能推定可負擔。")
    receipt = {
        "id": quote_id, "request_hash": request_hash, "timeline_id": state.timeline_id, "character_id": char.character_id,
        "owner_id": char.owner_id, "investigator": char.name, "shop": shop,
        "arrival_basis": arrival, "source": source, "mode": mode, "currency": currency,
        "items": normalized_items, "credit_rating": credit, "affordability": rationale,
        "total_minor": sum(i["quantity"] * (i["unit_price_minor"] or 0) for i in normalized_items) if mode == "cash" else None,
        "status": "quoted", "map_position": [state.current_map_page.get(char.owner_id), state.current_room_id.get(char.owner_id)],
    }
    state.commerce.setdefault("transactions", {})[quote_id] = receipt
    if mode == "lifestyle":
        _settle(state, receipt)
    return {"ok": True, "purchase": deepcopy(receipt), "investigator": char.name,
            "carried_items": list(char.carried_items)}


def _settle(state: GroupState, receipt: dict) -> None:
    char = _actor(state, receipt["investigator"])
    if char.character_id != receipt["character_id"] or state.timeline_id != receipt["timeline_id"]:
        raise ValueError("角色或時間線已改變，請重新裁定購買。")
    _ready(state, char.owner_id)
    if receipt["mode"] == "cash":
        currency, total = receipt["currency"], receipt["total_minor"]
        balance = char.cash_balances.get(currency)
        if type(balance) is not int:
            raise ValueError("尚未確認此幣別的現金餘額，請 KP 先登記。")
        if balance < total:
            raise ValueError("現金不足；尚未扣款或取得商品。")
        receipt["balance_before"] = balance
        receipt["balance_after"] = balance - total
        char.cash_balances[currency] = balance - total
    for item in receipt["items"]:
        char.carried_items.extend([item["name"]] * item["quantity"])
    receipt["status"] = "purchased"


def confirm(state: GroupState, owner: str, quote_id: str) -> dict:
    receipt = state.commerce.get("transactions", {}).get(quote_id)
    char = state.get_active_character(owner)
    if not receipt or not char or receipt["owner_id"] != owner or receipt["character_id"] != char.character_id or receipt["timeline_id"] != state.timeline_id:
        raise ValueError("沒有屬於目前角色與時間線的報價。")
    if receipt["status"] == "purchased":
        return {"ok": True, "duplicate": True, "purchase": deepcopy(receipt)}
    if receipt["status"] != "quoted" or receipt["map_position"] != [state.current_map_page.get(owner), state.current_room_id.get(owner)]:
        raise ValueError("報價情境已過期，請重新確認到店與交易。")
    _settle(state, receipt)
    return {"ok": True, "purchase": deepcopy(receipt)}


def expire_quotes(state: GroupState, owner: str) -> bool:
    changed = False
    for receipt in state.commerce.get("transactions", {}).values():
        if receipt["owner_id"] == owner and receipt["status"] == "quoted":
            receipt["status"] = "expired"
            changed = True
    return changed


def describe(receipt: dict) -> str:
    goods = "、".join(f"{i['name']} ×{i['quantity']}" for i in receipt["items"])
    if receipt["mode"] == "cash":
        total = receipt["total_minor"]
        price = f"{receipt['currency']} {total // 100}.{total % 100:02d}"
        if receipt["status"] == "quoted":
            return f"已抵達{receipt['shop']}。報價：{goods}，合計 {price}；尚未扣款或入袋。確認購買請輸入 /coc purchase {receipt['id']}。"
        return f"已在{receipt['shop']}買入{goods}，支付 {price}。"
    return f"已抵達{receipt['shop']}並買入{goods}；費用依信用評級 {receipt['credit_rating']} 納入日常花費，未扣現金帳本。"
