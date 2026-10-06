# 戰鬥開始後漏登記已在場敵人

[English](bug-active-enemy-registration-after-combat-start.md) | [文件索引](../../README_zh.md)

分類：`bug`。狀態：**backlog**。

## 證據

未修改 prompt 的 `main_v2` 在 16 輪《陰宅》戰鬥模擬中呼叫了 `start_combat`，卻完全沒有呼叫 `add_npc_to_combat`。同一聚焦 harness 在戰鬥路由整併分支起初也出現相同漏呼叫，因此對照結果本身無法證明路由表造成退化。更早一次使用原始戰鬥文字的模擬曾正確呼叫三次 `add_npc_to_combat`，顯示行為不穩定，需重複受控量測。

之後的 16 輪整合模擬中，兩個工具都成功，但分散在連續兩個玩家回合。`start_combat` 那回合的四次工具額度被三次 `search_scenario` 與一次 `start_combat` 用完；下一回合才呼叫 `add_npc_to_combat`。這直接顯示：檢索消耗額度時，prompt 要求「同一工具序列登記」可能超過 `MAX_TOOLS_PER_TURN=4`。

## 預期行為

正式戰鬥開始時，已依劇本觸發且在場的敵人都應在交出首輪前登記，包括劇本護甲、攻擊、能力、使用限制；同種多隻敵人要有不同顯示名稱。尚未符合劇本觸發條件的沉睡敵人不可提前視為在場戰鬥員。

## 後續

用相同劇本序列重現並檢查 Executor 工具呼叫、工具回執及最終戰鬥狀態，分辨原因是檢索額度、來源證據、工具範圍、模型路由判斷，還是 Python 交接。修法須保留工具數限制對 AI 自行推進劇情的防護，同時允許已觸發、有劇本依據的敵人完成原子登記；不可提前登記未觸發的敵人。本次整合增加的路由提醒是防護文字，不能單憑它宣稱此間歇缺陷已解決。

[PR #148](https://github.com/marcoliu99/line-coc-keeper/pull/148) 已合併：`initialize_combat(enemies)` 一次呼叫就開戰並登記全部敵人，守密人提示在兩隻以上有劇本依據的敵人已在場時會導向它。工具已經存在，但守密人並不一定使用。在 `6de8b31` 上的四人《陰宅》實跑中，第 143 回合先呼叫三次 `search_scenario`，再單獨呼叫 `start_combat`；鼠群到第 148 回合才登記（先 `close_legacy_combat`，再 `add_npc_to_combat`），在那之前這場沒有敵人的戰鬥一直扣住整桌人（見 [combat_turn_always_endable_design_spec](combat_turn_always_endable_design_spec_zh.md)）。用該序列重跑、敵人在開場回合就完成登記之前，本缺陷維持開啟。
