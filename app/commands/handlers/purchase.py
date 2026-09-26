"""Deterministic cash confirmation; no model call or player-supplied balance."""
from __future__ import annotations

import asyncio

from app import keeper
from app.repositories.group_state import load_state
from app.services import purchases


async def handle(conversation_id, user_id, reply, parts, is_keeper=False):
    state = load_state(conversation_id)
    try:
        if parts[1] == "funds":
            if len(parts) < 5:
                raise ValueError("用法：/coc funds 角色完整名稱 幣別 餘額（KP 專用）")
            name = " ".join(parts[2:-2])
            currency = purchases.currency_code(parts[-2])
            value = purchases.amount(parts[-1])
            def set_balance(latest):
                if not (is_keeper or latest.kp_assistant_user_id == user_id):
                    raise ValueError("只有 KP 可以登記已確認的現金餘額。")
                char = purchases._actor(latest, name)
                previous = char.cash_balances.get(currency)
                char.cash_balances[currency] = value
                latest.commerce.setdefault("balance_adjustments", []).append({
                    "character_id": char.character_id, "timeline_id": latest.timeline_id,
                    "currency": currency, "before": previous, "after": value, "confirmed_by": user_id,
                })
            await asyncio.to_thread(keeper._mutate_and_save_state, state, set_balance)
            await reply(f"已登記 {name} 的現金：{currency} {value // 100}.{value % 100:02d}。")
        elif parts[1] == "purchase":
            if len(parts) != 3:
                raise ValueError("用法：/coc purchase 報價ID；可先用 /coc purchases 查看。")
            def settle(latest):
                result = purchases.confirm(latest, user_id, parts[2])
                return keeper._StateMutation(result, should_save=not result.get("duplicate", False))
            result = await asyncio.to_thread(keeper._mutate_and_save_state, state, settle)
            prefix = "這筆交易已結算，沒有再次扣款或入袋。\n" if result.get("duplicate") else ""
            await reply(prefix + purchases.describe(result["purchase"]))
        else:
            char = state.get_active_character(user_id)
            if char is None:
                raise ValueError("請先選擇目前角色。")
            records = [r for r in state.commerce.get("transactions", {}).values()
                       if r["character_id"] == char.character_id and r["timeline_id"] == state.timeline_id
                       and r["status"] in {"quoted", "purchased"}]
            balances = "、".join(f"{c} {n // 100}.{n % 100:02d}" for c, n in char.cash_balances.items()) or "未登記"
            await reply("現金：" + balances + "\n" + ("\n".join(purchases.describe(r) for r in records[-5:]) or "目前沒有報價或購買紀錄。"))
    except ValueError as exc:
        await reply(str(exc))
