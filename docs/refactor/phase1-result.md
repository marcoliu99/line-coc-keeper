# Phase 1 結果：State Transaction

分支 `refactor/phase1-state-transaction`，基底 `main_v2`。規格：[`state_transaction_design_spec`](../specs/refactor/state_transaction_design_spec_zh.md)；需求：[架構重構規格](../specs/refactor/architecture_refactor_phases_1_4_design_spec_zh.md)；盤點：[migration-inventory](migration-inventory.md)。

## 1. 版本

| 項目 | 值 |
| --- | --- |
| Base SHA | `2affd06c9b90105d454d19c6eb4f7e7c1fb232f8`（`main_v2`，含 PR155／156） |
| Result SHA | 本 PR 的 head commit；程式碼在 `651455a` 之上的後續 commit，文件 commit 最後 |
| 實際環境 | Python 3.13.14、pytest 9.1.1、ruff 0.16.8、mypy 2.3.1、SQLite 3.45.1、4 核 Linux |

## 2. 變更檔案與責任

| 檔案 | 責任 |
| --- | --- |
| `app/repositories/state_transaction.py`（新） | 唯一的遊戲狀態寫入邊界：`mutate`／`amutate`／`mutate_value`、`commit_for_snapshot`／`run_snapshot`、`commit_snapshot`、`ambient`、`sync_snapshot`／`refresh_snapshot`、action ledger、局部不變量 |
| `app/repositories/group_state.py` | `_save_state_unlocked` 改名為 `write_state_tx`（儲存 primitive）；`StateCommit.apply` 同步 `loaded_timeline_id` |
| `app/db.py`、`app/storage_errors.py`（新） | 新增 `state_actions` 資料表；在狀態交易內嘗試另開寫入時立即丟出 `NestedTransactionError`（類別放在獨立小模組，避免測試 `importlib.reload(db)` 後類別身分分裂） |
| `app/models.py` | `GroupState.loaded_timeline_id`（只存在記憶體，不序列化） |
| `app/keeper.py` | `_mutate_and_save_state` 變成轉接；回合／OOC／維護提交、已結算檢定事件、`_ensure_turn_timeline` 改走交易；移除 `_save_state_checked`、`_sync_state_snapshot` |
| `app/checkpoints.py` | `rollback` 單一交易；`create_checkpoint` 在 mutation 內加入既有交易 |
| `app/agents/supervisor.py`、`assistant.py` | 由 `run_turn` 擁有 turn id，傳入最終提交 |
| `app/commands/handlers/{character,combat,map_handler,uploads,system}.py`、新 `transact.py` | handler 在最新 state 的交易內驗證並修改；`newgame` 為 `replace_state` |
| `app/services/{pending_buttons,narrative_corrections,correction_summary}.py`、`app/legacy_commands.py` | 認領／釋放為 delta；其餘 `commit_snapshot` |
| `tests/test_state_transaction.py`、`test_state_transaction_adapters.py`、`test_architecture_state_writes.py`、`tests/state_store.py` | 新測試與共用測試輔助 |
| `scripts/benchmark_state_transaction.py` | 寫入延遲對照 |

## 3. Inventory 前後

寫入旁路從 6 條（工具、回合／維護提交、handler、舊檢定／上傳、待處理按鈕、回溯）收斂為 1 條。細節與每列的前後狀態見 [migration-inventory](migration-inventory.md) 第 1 節。重複碼：`keeper` 與 `legacy_commands` 各一份的已結算檢定事件保存，**仍是兩份**（改道到同一個 action id，合併留給第 2 階段）。

## 4. 驗證

指令（皆在乾淨 checkout、Python 3.13 venv 上執行）：

```text
ruff check .          # All checks passed
mypy app              # Success: no issues found in 130 source files
pytest                # 全數通過；基線 2048 項 → 現在 2107 項（+59）
```

驗收情境與對應測試：

| ID | 測試 | 結果 |
| --- | --- | --- |
| S1 不同資源併發無 lost update | `test_concurrent_deltas_to_different_resources_are_all_kept`；工具層 `test_concurrent_tools_on_different_resources_lose_no_update`；handler 與工具併發 | 通過 |
| S2 多 process 序列化 | `test_separate_processes_are_serialised_by_storage`（3 個真實 process × 12 次，最終 36、revision 連續） | 通過 |
| S3 同 action 同時送兩次 | `test_same_action_submitted_twice_applies_once`（一次 applied、一次 duplicate；扣值、事件、ledger 各一份） | 通過 |
| S4 同 id 不同 payload | `test_same_action_id_with_different_payload_conflicts_and_changes_nothing` | 通過 |
| S5 reset 後舊按鈕 | `test_button_from_before_a_reset_is_stale_and_runs_nothing`、`test_a_snapshot_from_before_a_new_game_cannot_overwrite_it_even_at_the_same_revision`、回溯後同理 | 通過 |
| S6 注入失敗全 rollback | 寫 ledger 失敗、寫 state 後失敗、`os._exit` 強制結束 process（新連線讀回），以及 rollback 失敗不留回溯前 checkpoint | 通過 |
| S7 提交後傳送失敗再重試 | `test_retry_after_delivery_failure_reuses_the_committed_result`；回合提交重試只記一次 log | 通過 |
| S8 較舊 refresh 晚到 | `test_late_older_refresh_does_not_roll_a_snapshot_back`；新 timeline 不受單調限制 | 通過 |
| S9 例外／取消不污染 | mutation 拋例外與 `BaseException`、取消中的 `amutate`（worker 完成後同 id 重試為 duplicate）、lock 可再用 | 通過 |
| S10 跨對話 | 不同對話使用不同 lock 物件；持有 A 的 process lock 不阻擋 B；各自計數無污染 | 通過 |

另外：局部不變量（只判斷被改動的欄位）、巢狀交易立即報錯、`ctx.conn` 寫入與 state 同進退、ledger 每對話有上限、AST 守門（含別名、`db.set_json*` 指向遊戲狀態表、原生 SQL；並以真實違規驗證守門會失敗）、六個進入點在新 process 的 import smoke。

既有 26 個測試檔原本以 in-memory fake 或 mock 取代 `load_state`／`save_state`。這些 fake 碰不到新邊界，已改用 `tests/state_store.py`：多數改成真實 SQLite（因此現在也涵蓋真實交易）；以共享的單一記憶體 state 驅動按鈕流程的少數測試改用 `MemoryTransactions`（只替代「有寫入發生」，不用來證明原子性）。**沒有修改任何行為預期**，修改的是測試的接縫與 fixture（例如真實儲存會在第一次存檔指派 timeline，fixture 需先存檔再取 timeline）。

寫入延遲（`scripts/benchmark_state_transaction.py`；4 核 Linux、本機檔案系統、5 角色＋60 筆 log 的 state、300 次）：

| 路徑 | median | p95 |
| --- | --- | --- |
| 舊路徑（鎖、load、mutate、`save_state`） | 2.85 ms | 3.83 ms |
| `state_transaction.mutate` | 2.30 ms | 2.63 ms |
| `mutate` + action id | 2.44 ms | 3.00 ms |

只代表本機檔案系統上的單寫入者；不是玩家端延遲數據。

## 5. 缺陷紀錄

| 類別 | 項目 |
| --- | --- |
| baseline 既有（本 PR 修復） | `newgame` 把 revision 重設為 1，上一局在 revision 1 載入的快照能通過 revision 檢查並覆蓋新局。現在 `commit_snapshot` 同時要求載入時的 timeline 相符。 |
| baseline 既有（本 PR 修復） | handler 在鎖外 load，與背景寫入競爭時玩家會看到 `StateRevisionConflict`；改為在最新 state 的交易內驗證與修改。 |
| 本 PR 引入後修復 | 開戰前 checkpoint 在 mutation 內自開 `BEGIN IMMEDIATE`，與外層交易互等到逾時（約 5 秒）失敗；由全套測試發現，已改為 `ambient` 加入同一交易，並新增「在狀態交易內另開寫入立即報錯」的防護。 |
| baseline 既有（未修，已記錄） | `keeper` 與 `legacy_commands` 各有一份已結算檢定事件保存、`apply_character_delta` 的 CON 註冊與 legacy 解析器各有一份 tier／Luck 邏輯 → 第 2 階段。 |
| 限制 | 工具呼叫本身沒有 action id（回傳值是 Python 物件，無法從 ledger 重播）；仍靠領域收據。`LOG_ENABLED` 與 `LOG_TEXT_ENABLED` 都關閉時沒有 request turn id，`run_turn` 自行產生；擋不住同一則 Discord 事件被重新投遞。 |

## 6. 相容性與回退

- 舊存檔：不需遷移。新資料表 `state_actions` 以 `CREATE TABLE IF NOT EXISTS` 建立；`loaded_timeline_id` 不序列化；`write_state_tx` 輸出與舊 `_save_state_unlocked` 相同。
- 回退：還原本 PR 的程式碼後，舊程式可直接讀取新寫入的存檔與 pending；`state_actions` 的列只是不再被讀取。回退期間失去 action 去重。
- 部署：未實際部署，也未連真實 Discord 或付費 provider。

## 7. 未執行項目

- 真實 Discord 群組與付費 provider 的連線測試（規格禁止作為預設環境）。
- 多 process 部署下的長時間浸泡測試：僅以 3 個 process 的短測試驗證儲存層序列化。
- 離線五人中文情境重播：留待第 2、3 階段，因為需要統一後的檢定與戰鬥流程才有意義。
