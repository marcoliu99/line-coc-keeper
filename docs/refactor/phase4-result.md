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
pytest                # 2219 passed, 1 skipped, 222 subtests passed（第 3 階段 2202 項 → +17）
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
| 未解決（沿用） | 第 2 階段的 Luck 政策衝突（戰鬥攻擊／防禦擲骰是否可用 Luck）仍待產品決定；戰鬥骰仍直接用 `app.dice` 而非檢定引擎的 `DicePort` |
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
