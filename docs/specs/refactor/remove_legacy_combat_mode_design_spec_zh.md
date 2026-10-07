# 移除舊版戰鬥模式

## 問題

戰鬥有兩套實作：managed 流程（現在開始的所有戰鬥），以及給 managed 流程出現前存下的戰鬥用的「legacy」模式。舊版工具從 Keeper 的工具表移除後，已經沒有任何入口能開始、關閉或完成舊版戰鬥，但它的程式碼還留在 `combat.py`、`combat_engine.py`、`combat_resources.py`，部分測試也靠它建戰鬥。每次改規則都得想兩遍。

## 修改

* 正式程式：刪除 `LEGACY_OPS`／`LegacyOps`、`apply_legacy_damage`、`process_legacy_timing`、舊版敵人結算、`close_legacy_combat`、`CloseLegacy` action、`Mode.LEGACY` 與 `LEGACY_NEEDS_ADMISSION`。共用的回合與傷害規則保留，改接收 `combat_flow.MANAGED_OPS`。
* 進行中但不是 managed 的戰鬥絕不轉換或推算。`mode_of` 丟出既有的 `CombatAdmissionError`，訊息為「This combat was created by an unsupported legacy combat format. Start a new combat.」，所以引擎的每個 action 與 `end_combat` 都會拒絕且不寫入。Keeper 的提示會顯示這段訊息，而不是戰鬥狀態區塊。
* 沒有戰鬥的狀態照常運作：managed ops 只拒絕「進行中但不是 managed」的戰鬥。
* 測試：`tests/combat_calls.py::ops_for` 對 managed 狀態回傳 managed ops，其他狀態一律丟錯，測試不會再默默跑在錯的實作上。原本手工建出進行中戰鬥的測試，現在改用 `combat.begin_combat` 開始；只測舊版保存或關閉的測試刪除。

## 不做

不遷移舊存檔、不新增例外型別、不改 managed 狀態機、結算、回滾或任何玩家看得到的流程。

## 測試

`tests/test_combat_engine.py`（不支援的戰鬥被每個 action 拒絕且不被改動；經引擎開始的戰鬥是 managed；未 managed 的狀態拿不到任何實作）、`tests/test_combat_resources.py` 與 `tests/test_combat_state_machine_integration.py`（資源層與 Keeper 工具的同樣拒絕）、`tests/test_architecture_combat.py`（`combat.py` 不讀 managed 旗標）。
