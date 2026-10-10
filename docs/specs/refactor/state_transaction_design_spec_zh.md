# 遊戲狀態交易

[English](state_transaction_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與目標

分類：`refactor`。狀態：**已實作**（[四階段架構重構](architecture_refactor_phases_1_4_design_spec_zh.md)的第 1 階段）。基準為 `main_v2` 的 `2affd06`（2026-10-04）。

改動前，遊戲狀態寫入 SQLite 有六條路：Keeper 工具（`_mutate_and_save_state`）、`keeper.py` 的回合與維護提交、指令 handler（`load_state` → 修改 → `save_state`）、舊的檢定與上傳路徑、待處理按鈕的認領，以及回溯。只有第一條會在鎖內重新讀取最新狀態；其餘寫入整份快照，靠 revision 檢查失敗來擋。沒有任何一條能辨認「同一個操作被重送」。目標是一個寫入邊界：重新讀取、驗證、原子提交，讓重試、連點與背景寫入不會遺失或重複任何變更。不新增模型呼叫。

## 現況證據

- `repositories/group_state.save_state` 原本就用每對話 `RLock` 加 `BEGIN IMMEDIATE`，並拒絕過期的 `state_revision`；但在鎖外 load 的 handler 會把無害的競態變成玩家看到的 `StateRevisionConflict`。
- `newgame` 會在新 timeline 上把 revision 重設為 1。上一局在 revision 1 載入的快照因此能通過 revision 檢查、覆蓋新的一局。
- 回溯、維護裁剪與回合提交各自帶著一份「重讀、比對 timeline、寫入」的流程。
- `combat._checkpoint_before_combat` 在 Keeper 工具的 mutation 內自己開 `BEGIN IMMEDIATE`；共用邊界下會在同一資料庫上巢狀兩個寫入交易。

## 契約

`app/repositories/state_transaction.py` 是唯一會寫 `GroupState` 列的模組。

```text
mutate(conversation_id, mutation, expected_timeline, action_id, request_fingerprint, expected_revision)
  鎖住該對話  ->  BEGIN IMMEDIATE  ->  讀最新列
  -> timeline  -> action ledger  -> revision  -> mutation(ctx)  -> 局部不變量
  -> 寫入 state + 角色鏡像 + 暫存事件 + action 結果  -> COMMIT
```

1. **結果是值。** `applied`、`duplicate`、`awaiting_input`（已提交，等待玩家）、`stale_timeline`、`conflict`、`rejected`。`awaiting_input` 是 mutation 呼叫 `ctx.awaiting_input()` 才會得到的結果；檢定與戰鬥 service 目前沒有呼叫它，等待玩家只是以一般狀態（待處理檢定或決定、戰鬥階段）提交，結果為 `applied`，所以呼叫端不能只靠結果推論「接下來要玩家行動」。mutation 本身拋出的領域錯誤，在 rollback 後照常往外傳。
2. **檢查順序。** 先擋過期 timeline，所以 reset／restore 之前的按鈕不會被重播。再查 action ledger：同 timeline、同 action id、同 payload 指紋，在比對 revision *之前* 就回傳已存的結果（`duplicate`，附原結果、事件與結果 payload）；同 id 不同指紋為 `conflict`。最後才是依快照計算的操作所用的 `expected_revision`。
3. **兩種寫法。** `mutate` 對最新狀態套用 delta，是預設。`commit_snapshot` 是無法改寫成 delta 的「已驗證快照」操作的嚴格路徑：只有在存放的 revision 與快照*載入時*的 timeline（`GroupState.loaded_timeline_id`，只存在記憶體）都仍吻合才會寫入，否則丟出 `StateRevisionConflict`／`StaleTimelineError`。刻意開新 timeline 的流程（劇本上傳、`/coc scenario use`）以它讀取時的 timeline 為準。
4. **與狀態同一個交易。** mutation 可透過 `ctx.conn` 寫其他表（手動角色卡資產、更正封存列、記憶片段）。這些寫入、state 列、角色鏡像、暫存事件與 ledger 列一起提交或一起失敗。`state_transaction.ambient(conversation_id)` 把已開啟的交易交給輔助函式；開戰前 checkpoint 就用它。mutation 內再呼叫 `mutate`、`db.transaction()` 或 `db.set_json()` 會立即報錯，而不是等 SQLite 自己的鎖逾時。
5. **Action ledger。** 資料表 `state_actions`，以對話、timeline、action id 為鍵，與狀態在同一交易寫入。選擇不存檔（`ctx.skip_save()`）的 mutation 不留 ledger 列，已經透過 `ctx.conn` 寫入的列也會一併 rollback（與拒絕相同），之後的重試會重新評估。保留最近 1000 筆／每對話與 30 天，每 25 個 revision 清理一次；列被清掉後，終態領域狀態（已消耗的待處理檢定、既有 event id）仍會阻止重複結算。
6. **局部不變量。** 只檢查 mutation 實際改動的欄位：HP/MP/SAN/Luck 為整數且在 `0..max`（有 max 時），彈藥在 `0..ammo_max`，pending 項目必須是 mapping。舊存檔中本來就怪的值，不會因為這次操作沒碰它而被拒絕。違反時回 `rejected`，不寫任何東西。
7. **快照。** 提交後，呼叫端的快照會以已提交的狀態同步。`sync_snapshot` 不會把快照移到同一 timeline 的較舊 revision，所以晚到的重新整理不會讓共享快照倒退；不同 timeline 一律勝出。
8. **失敗。** 儲存 state、鏡像、事件或 ledger 時失敗，全部 rollback 並發出 `state.save.failed`。提交後的失敗（快取更新、Discord 傳送、敘事）不會重跑 mutation；用同一個 action id 重試會取回已存的結果。

Action id 來自操作的可信擁有者，而不是文字相似度：`run_turn` 擁有的 turn id 加上所提交內容的摘要（`turn:<id>:<digest>`），以及已結算檢定的穩定 event id（`check-event:<event_id>`）。第 2、3 階段會在同一個 ledger 上加入檢定與戰鬥的 action id。

取值用的輔助函式 `mutate_value` 與 `run_snapshot` 回傳 `TxResult.value`，而重播的動作不會填這個欄位（保存的收據在 `TxResult.result`）。因此兩者收到 `action_id` 時直接以 `ValueError` 拒絕；可重播的操作改呼叫 `mutate` 或 `commit_for_snapshot` 並讀取 `TxResult`，和 `mutate_tool_state_once` 的做法一樣。

## 已搬上邊界的寫入者

| 原路徑 | 現在 |
| --- | --- |
| `keeper._mutate_and_save_state`／`mutate_tool_state`（所有 Keeper 工具） | `state_transaction.run_snapshot` 的薄轉接 |
| `keeper._commit_turn_result`、`_commit_kp_ooc_turn_result`、`_persist_memory_maintenance_state`、`_ensure_turn_timeline`、已結算檢定事件保存 | 使用 `commit_for_snapshot`／`mutate`，帶 id 與 `ctx.conn` |
| `checkpoints.rollback` | 單一 `mutate`：回溯前 checkpoint、還原狀態與鏡像一起提交 |
| `/coc` 角色、戰鬥、地圖 handler | 在 `transact` 內對最新狀態驗證並修改 |
| `/coc newgame` | `mutate` 加 `ctx.replace_state`，在最新狀態上重新檢查「戰鬥未結算」防護 |
| 待處理按鈕的認領與釋放 | 對最新狀態的 delta |
| 舊的檢定／上傳／地圖解析、system handler、更正服務 | `commit_snapshot`（嚴格），待第 2–4 階段再搬家 |

`tests/test_architecture_state_writes.py` 會解析 `app/` 與 `scripts/` 下每個模組，追蹤 import 別名，拒絕任何對 `save_state`、`write_state_tx` 或其私有變體的引用、任何指向 `group_states`／`characters` 的 `db.set_json*`／`db.delete_json*` 呼叫（或無法解析資料表者）、以及對它們的原生 `INSERT/UPDATE/DELETE` SQL。僅有兩個儲存模組與一份附理由的營運腳本清單可例外。

## 相容性

既有存檔不需遷移。新資料表以 `CREATE TABLE IF NOT EXISTS` 建立，舊程式會忽略；`loaded_timeline_id` 不會被序列化。工具名稱、輸出、Discord custom id 與指令文字均不變。還原程式碼是安全的：ledger 列只是不再被讀取。

## 部署說明

行程內的鎖只排序同一行程內的呼叫者。跨行程安全依靠 `BEGIN IMMEDIATE` 與儲存的 revision。SQLite 整個資料庫同一時間只有一個寫入者，所以不同對話的寫入會在交易的短暫期間內序列化；行程層級的鎖是每對話一把，沒有全域鎖。

## 限制

- 工具呼叫本身沒有 action id：工具 mutation 的回傳值是 Python 物件，無法從 ledger 重播。工具層級的重複保護仍由領域收據（戰鬥擲骰收據、後果收據）負責，並在第 2、3 階段重新檢視。
- 當 `LOG_ENABLED` 與 `LOG_TEXT_ENABLED` 都關閉時，request context 不帶 turn id；`run_turn` 會為每次執行自行產生一個，能保護它自己的重試，但擋不住同一則 Discord 事件被重新投遞。

## 驗證

`tests/test_state_transaction.py`（情境 S1–S10，加上不變量、巢狀、rollback 與清理，含真實的多行程併發與交易中途強制結束行程）、`tests/test_state_transaction_adapters.py`（回合提交、工具、handler、待處理按鈕、回溯）與 `tests/test_architecture_state_writes.py`。結果報告：[第 1 階段](../../refactor/phase1-result.md)。
