# Phase 4 結果：移除 legacy_commands

分支 `refactor/phase4-retire-legacy-commands`，基底 `refactor/phase3-combat-engine`（疊在第 3 階段之上）。規格：[`legacy_commands_retirement_design_spec`](../specs/refactor/legacy_commands_retirement_design_spec_zh.md)；需求：[架構重構規格](../specs/refactor/architecture_refactor_phases_1_4_design_spec_zh.md)；盤點：[migration-inventory](migration-inventory.md)；四個階段的總覽：[summary](summary.md)。

## 1. 版本

| 項目 | 值 |
| --- | --- |
| Base SHA | `a3bebe11f259796ef97fc120e19a0e645c4f68f4`（第 3 階段 head；其下 `2affd06c9b90105d454d19c6eb4f7e7c1fb232f8` 為 `main_v2`） |
| 程式碼 commit | `89ac20bec9d7154c579e572d367671afed9958f4`（`Retire legacy_commands`） |
| Result SHA | 本 PR 的 head commit；文件 commit 在 `89ac20b` 之後 |
| 實際環境 | Python 3.13.14、pytest 9.1.1、ruff 0.16.8、mypy 2.3.1、SQLite 3.45.1、4 核 Linux |

## 2. 變更檔案與責任

| 檔案 | 責任 |
| --- | --- |
| `app/legacy_commands.py` | **刪除**（`2affd06` 為 2590 行；本 PR 開始時剩 1184 行） |
| `app/services/scenario_ingestion.py`（新，610 行） | PDF 上傳流程、新劇本／更正的套用、劇本比較、角色卡上傳、預製角色合併 |
| `app/services/map_service.py`（新，261 行） | 地圖上傳、把玩家的話解析成地圖移動、RAG 房間查找 |
| `app/services/character_service.py`（新，243 行） | 認領預製角色、暫離／回歸、治療、就緒名冊、「已有角色」防護 |
| `app/commands/handlers/messages.py`（新） | `/roll`、不支援的訊息類型 |
| `app/commands/handlers/{character,uploads}.py` | 分別接收 `handle_pregen_luck_roll` 與 `resolve_pdf_upload_choice`（權限＋鎖＋回覆屬於 handler） |
| `app/commands/{__init__,router}.py`、`handlers/system.py` | 改匯入各擁有者；`app.commands` 不再有延遲解析的 `__getattr__` |
| `tests/test_architecture_legacy.py`、`test_ingestion_trace.py`、`ingestion_trace.py`、`fixtures/ingestion_trace.json`（新） | L1／L7 守門；L5 的前後對照 |

## 3. Inventory 前後

見 [migration-inventory 第 4 節](migration-inventory.md)。`legacy_commands` 的 runtime 匯入者：`2affd06` 時是 10 個模組（router、`commands/__init__`、七個 handler、`discord_bot`），本 PR 開始時剩 5 個（router、`commands/__init__`、`character`／`system`／`uploads` handler），現在是 0；提到它的測試檔有 18 個，其中匯入或 patch 它的測試改接後變成 0，其餘只在說明文字（docstring、註解）與守門測試中提到它的名稱。「函式內延遲匯入」殘留：`legacy_commands` 內唯一一處（`from app.commands import permissions`，只因 package 會匯入該模組才需要）隨著函式搬到 handler 而變成一般匯入。

## 4. 驗證

指令（乾淨 checkout、Python 3.13 venv）：

```text
ruff check .          # All checks passed
mypy app              # Success: no issues found in 149 source files
pytest                # 2228 passed, 1 skipped, 223 subtests passed（第 3 階段 2202 項 → +26；其中 5 項是 code review 後新增，其餘來自併入的 `main_v2`）
```

唯一的 skip 同前（`tests/test_codex_analysis_smoke.py`，需已登入的 Codex CLI），**未執行**。

驗收情境與對應測試：

| ID | 做法與測試 | 結果 |
| --- | --- | --- |
| L1 刪除後啟動／import | `test_the_retired_module_cannot_be_imported`（全新 process 中 `import app.legacy_commands` 失敗）；`test_every_entry_point_loads_without_it`（router、`discord_bot`、三個新 service、`messages` 各自在全新 process 載入）；`test_no_package_resolves_names_lazily_on_first_use`（沒有任何 package `__init__` 定義 `__getattr__`）；`test_the_new_services_do_not_depend_on_the_command_layer_at_runtime` | 通過 |
| L2 指令別名／說明／錯誤格式 | router 的 diff 只有匯入與改名後的呼叫（逐行檢視）；既有 router、help、別名測試全數通過（`test_help_text`、`test_help_actions`、`test_kp_sudo`、`test_natural_corrections` 等） | 通過 |
| L3 角色 claim／heal／correction、地圖指令 | 搬走的 29 個定義中 27 個與原本的語法樹完全相同（忽略改名）；其餘兩個的差異見第 5 節；既有 `test_pregen_and_creation`、`test_reverted_movement_pipeline`、`test_rag_query_logging`、`test_turn_safety` 改接後通過；狀態寫入沿用第 1 階段的 `commit_snapshot` | 通過 |
| L4 舊按鈕 payload | 按鈕 handler 與 custom id 沒有被動到；`test_button_routing`（過期點擊被確認、釋放且不還原）、`test_state_transaction` 的 reset 後舊按鈕、`test_pending_button_latency` 均通過 | 通過 |
| L5 上傳流程（已保存 fixture／假 provider） | `test_upload_flows_reproduce_the_trace_recorded_before_the_module_was_retired`：首次上傳、相似劇本重傳、角色卡上傳、地圖上傳，比對**在仍有 `legacy_commands` 的第 3 階段 head 上錄下**的 trace（每個協作者呼叫的名稱與參數、全部訊息與警告、最終狀態）。在兩份 checkout 上交替執行並 diff：完全相同、且重跑不變 | 通過 |
| L6 舊存檔＋待處理檢定／戰鬥 | 本階段沒有改動任何儲存結構；第 3 階段的 `test_b7_*`（legacy 戰鬥載入、操作、明確關閉、存檔後續玩）與第 2 階段的待處理檢定測試在本分支上全數通過 | 通過 |
| L7 架構測試 | `test_architecture_legacy.py`（16 項）：檔案不存在且無名稱相近的 shim；`app/`、`scripts/`、`tests/` 內沒有任何方式匯入（import 敘述、`from app import ...`、`import_module`、`__import__`），並以五種「回來的方式」驗證守門會失敗；第 1 階段的狀態寫入守門（沒有直接寫入旁路）仍通過 | 通過 |

既有測試的改動：只改接縫與目標名稱（改為 `scenario_ingestion`、`map_service`、`character_service`、`character_handler`，以及去掉底線的公開名稱），沒有改任何行為預期。`test_button_routing` 的 `test_discord_bot_imports_nothing_from_legacy_commands` 因模組已不存在而變成永遠成立，已刪除，由 `test_architecture_legacy.py` 取代；第 2、3 階段守門清單裡的 `"app.legacy_commands"` 字串也一併移除。

## 5. 缺陷與差異紀錄

| 類別 | 項目 |
| --- | --- |
| 差異（有意） | `resolve_pdf_upload_choice` 的函式內 `from app.commands import permissions` 是為了避開「package 在載入時匯入本模組」的循環；函式搬到 `handlers/uploads.py` 後改為一般匯入，行為不變 |
| 差異（有意） | `_resolve_pdf_upload_choice_locked` 改名 `apply_pdf_upload_choice`，`_resolve_map_action_transaction` 改名 `resolve_map_action` 等，共 9 個函式去掉底線成為公開名稱（它們現在跨模組被呼叫）。未保留舊名稱的別名 |
| 差異（有意） | log 來源的 logger 名稱由 `app.legacy_commands` 改為 `app.services.map_service`（`_find_room_via_rag` 的 INFO log）；其餘搬走的模組原本沒有使用該 logger。若有以 logger 名稱過濾的外部設定，需要更新 |
| baseline 既有（未修，已記錄） | `scenario_ingestion` 與 `map_service` 仍用 `state_transaction.commit_snapshot`（嚴格快照路徑）提交，而不是 delta `mutate`；改成 delta 會改變函式的行為，不屬於「搬移」 |
| baseline 既有（未修，已記錄） | `commands/handlers/system.py` 仍然很大，且保留自己的 `commit_snapshot` 呼叫；規格只要求搬走能讓 `legacy_commands` 被刪除的部分 |
| 未解決（沿用） | 第 2 階段的 Luck 政策衝突已由產品決定：維持戰鬥攻擊／防禦擲骰可用 Luck，並修改規格（程式未更動）；戰鬥骰仍直接用 `app.dice` 而非檢定引擎的 `DicePort` |
| baseline／#165 引入後修復（code review） | 沒有進行中劇本時上傳角色卡，若與未被認領的預製角色以不同名字判定為同一人（指紋或職業＋技能），比對會把別名寫進 dictionary 表，而 #165 的「交易內不准另開寫入」防護會丟出 `NestedTransactionError`，上傳失敗。現在比對（含別名學習）在開啟交易之前完成（`test_upload_without_a_scenario_learns_the_alias_after_the_pool_is_committed`） |
| #165 引入後修復（code review） | `handle_pregen_luck_roll` 在交易外載入快照、抽 Luck，再直接在 event loop 上用阻塞的 `commit_snapshot` 提交；背景寫入讓 revision 變動時玩家會看到沒被接住的 `StateRevisionConflict`，抽到的值也作廢。改為 `transact` 的 delta：骰值在最新 state 的交易內抽取與套用（`test_pregen_luck_roll_applies_to_the_latest_state_and_a_refusal_writes_nothing`） |
| #165 引入後修復（code review） | `claim_pending_buttons_locked` 每回合都開 `BEGIN IMMEDIATE`，即使沒有任何新按鈕；基線是先讀、有新項目才寫。恢復為先用唯讀載入判斷，只有確實有東西要認領時才開寫入交易（`test_a_turn_with_no_new_button_never_opens_a_write_transaction`） |
| #165 引入後修復（code review） | `ctx.skip_save()` 只略過 state 與 ledger，已透過 `ctx.conn` 寫入的列（例如為沒有存檔的戰鬥所建的開戰前 checkpoint）仍會提交，與「全部成功才提交」不符。現在與拒絕相同，一併 rollback（`test_skip_save_also_drops_rows_already_written_through_the_connection`；所有現有的 `skip_save` 呼叫都在寫入之前，不受影響） |
| 併入 `main_v2` 時處理（上游新功能） | `main_v2` 在本階段進行期間合併了 #169（以 `scenario` 開頭的 Markdown 劇本上傳），其 `handle_scenario_markdown_upload` 加在 `legacy_commands.py`。本 PR 將它移到 `scenario_ingestion`（`_MARKDOWN_PAGE_MARKER_RE`、`_markdown_scenario_title` 一併），`handlers/uploads.py` 的路由、`handlers/messages.py` 的不支援訊息文字與三處「請重新上傳劇本檔案」訊息、`處理劇本檔案` 的 KP 提示照上游原樣帶過來。trace 新增 `markdown_upload` 流程；`tests/fixtures/ingestion_trace.json` 在 `main_v2` `f183e9f`（仍有 `legacy_commands`）上重新錄製，與本分支的輸出逐項相同。舊的 golden 與新錄製只差 `first_upload` 的 manifest 多了 #169 加的 `source_format`／`source_file` 兩個欄位，不是本 PR 的行為改動 |
| 本 PR 修復（我的失誤） | #166／#167 在併入 `main_v2` 時，`README.md` 帶著未解決的衝突標記（`<<<<<<<`／`>>>>>>>`）進了 `main_v2`：phase 2 分支合併 phase 1 時我只處理了 `legacy_commands.py` 的衝突而漏看 README。本 PR 的合併已把 README 解成正確內容 |
| 文件與死碼整理（code review） | `TxContext.revision_before` 與 `TxContext.replaced` 沒有任何讀取者，已刪除。`awaiting_input` 是需求規定要能表達的 outcome，予以保留，但規格與模組說明改為如實說明：目前檢定與戰鬥 service 不呼叫它，等待玩家以一般狀態提交、結果為 `applied`。`retryable`（revision 衝突時設定）與 `original_outcome`（重播時設定）有 production 設定者與測試，保留 |
| 文件修正（code review） | 傷勢檢定不可用 Luck 的範圍限於戰鬥引擎登記的傷勢檢定；戰鬥以外由 `adjust_character` 串在重傷後的 CON 檢定從基線起就提供 Luck，現況不變，新增 `test_c5_a_major_wound_con_check_outside_combat_keeps_offering_luck` 釘住。另清掉 `pyproject.toml` 裡已刪除檔案的 SLF001 例外、`catalog.json` 中 18 筆指向 `app/legacy_commands.py` 的證據連結，以及兩處舊函式名稱的註解 |
| 限制 | typed extraction result 與 provider 抽象依規格留待後續階段；PDF／預製角色流程只做原樣搬移 |

## 6. 相容性與回退

* 舊存檔：不需遷移。未改動任何儲存結構、custom id 或指令文字。
* `app.commands` 的公開名稱（`handle_pdf_upload`、`handle_map_upload`、`handle_check_command`…）保留，現在直接指向擁有者模組；`app.legacy_commands` 不再存在，repo 內（含 `scripts/`）已無人匯入。
* 回退：還原本 PR 後，舊程式讀得懂所有現有資料。
* 部署：未實際部署，也未連真實 Discord 或付費 provider。

## 7. 未執行項目

* 真實 Discord 群組與付費 provider 的連線測試（規格禁止作為預設環境）；離線結果不代表線上驗證。
* 離線五人中文情境重播（需要真實模型或錄製的回應；本環境沒有，所以沒有以此宣稱端到端驗證）。
* `tests/test_codex_analysis_smoke.py`（需已登入的 Codex CLI）。
