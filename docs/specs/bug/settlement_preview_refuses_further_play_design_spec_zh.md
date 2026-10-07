# 結算預覽等待確認時，拒絕繼續戰鬥操作

## 問題

在 Haunting 的一次測試中，鼠群已經倒下，守密人也呼叫了 `preview_combat_settlement`，但之後每個玩家回合守密人又去推進戰鬥。結算預覽還在等待時，推進仍然被允許；每次推進都讓輪數往前、戰鬥的 revision 改變，預覽因此過期，`confirm_combat_settlement` 被拒絕。輪數從 15 一路爬到 36，約 23 個回合裡幾乎每個玩家行動都以「這次行動無法繼續」收尾，戰鬥到第 55 輪才結束。

## 修改

戰鬥處於 `SETTLEMENT` 階段時，`advance_combat`、`declare_action`、NPC 規劃（`plan_enemy_turn`）與執行 NPC 計畫（`run_enemy_plan`）會在改動任何東西之前拒絕。拒絕訊息說明戰鬥已結束，並指出下一步：用待確認的 `settlement_id` 呼叫 `confirm_combat_settlement`（這個 id 也會放在回傳欄位裡），或呼叫 `rollback_combat`。已經記錄過的推進重試，仍然重播原本的結果。

結算預覽拿得太早時，原本沒有退路：只能確認（戰鬥就結束了）或 `rollback_combat`（整場丟掉）。現在守密人有 `cancel_combat_preview`（`combat_id`、`event_id`、`reason`）：預覽等待確認時，它撤回預覽並把階段改回 `READY`，戰鬥繼續；資源、骰值與收據都不動，用同一個 `event_id` 重試會重播。舊的結算 id 之後視為過期，下一次 `preview_combat_settlement` 會產生新的。沒有等待中的預覽時會被拒絕。

## 不做

要不要結算仍由守密人決定，不會自動確認或取消預覽；也不會因為還有活著的敵人就拒絕預覽（戰鬥可能在敵人還站著時結束：逃跑、投降，或調查員全倒）。其他戰鬥工具不變。

## 測試

`tests/test_combat_flow.py`：預覽等待時，推進、宣告、規劃 NPC 回合與執行其計畫都被拒絕並附上結算 id，輪數與 revision 不變，同一份預覽之後可以正常提交；取消預覽會讓戰鬥繼續、重試會重播、舊的結算 id 變成過期、沒有預覽時被拒絕（`tests/test_combat_wiring.py` 涵蓋守密人工具）。
