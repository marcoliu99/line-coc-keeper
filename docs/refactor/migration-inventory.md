# Migration inventory（遷移盤點）

本表隨每個階段的 PR 更新。欄位：入口、state 讀取、state 寫入、原有 lock、事件持久化、外部副作用、目標 owner、測試、遷移狀態。行數與呼叫次數是 2026-10-04 在 `main_v2` `2affd06` 實際追蹤的結果，不是 review 報告的轉述。

## 0. 基線事實

| 項目 | 實際值 |
| --- | --- |
| Repository | `marcoliu99/line-coc-keeper` |
| 基線 | `main_v2` @ `2affd06c9b90105d454d19c6eb4f7e7c1fb232f8`（工作樹乾淨，無使用者未提交修改） |
| PR155 / PR156 | 皆已合併：`85f986d`（#155 多欄 PDF 匯入）、`6024adf`（#156 戰鬥回合狀態機）。未再 cherry-pick |
| 適用的指引 | `AGENTS.md`、`CODING_STANDARDS.md`、`CONTEXT.md`、`docs/adr/0001–0003`、`docs/agents/*` 已讀；`docs/specs/` 為 issue tracker |
| 工具 | Python 3.13.14（CI 使用 3.13）、pytest 9.1.1、ruff 0.16.8、mypy 2.3.1、SQLite 3.45.1、discord.py 2.7.1 |
| 基線測試 | `ruff check .` 通過、`mypy app` 通過、`pytest` 2048 項全數通過（約 30 秒） |
| 部署形態 | 未能從 repo 證實是否多 process／worker（`scripts/start_bot.sh` 啟動單一 bot）。因此寫入安全不假設單 process，而依賴 SQLite `BEGIN IMMEDIATE` 與 revision |

## 1. State 寫入

| 入口 | 讀取 | 寫入 | 原 lock | 事件持久化 | 外部副作用 | 目標 owner | 測試 | 狀態 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Keeper 工具（`keeper_tools/*` → `keeper.mutate_tool_state`） | 鎖內 reload | `_mutate_and_save_state` → `save_state` | state `RLock` | 隨 state（`resolved_check_events`、combat receipts） | 私訊／圖片排入佇列 | `state_transaction.run_snapshot` | `test_keeper_tool_registry`、`test_state_transaction_adapters` | **PR1 完成** |
| 回合提交 `keeper._commit_turn_result`（supervisor、assistant） | 鎖內 reload | `save_state` | state `RLock` | 隨 state（`log`） | 無（Discord 傳送在提交之後） | `commit_for_snapshot` + action id `turn:<id>:<digest>` | `test_state_transaction_adapters::TurnCommitTests` | **PR1 完成** |
| KP OOC 提交 `_commit_kp_ooc_turn_result` | 鎖內 reload | `save_state` | state `RLock` | 隨 state | 無 | `commit_for_snapshot` | `test_kp_assistant_v2` | **PR1 完成** |
| 已結算檢定事件 `keeper._persist_resolved_check_event`、`legacy_commands._persist_resolved_check_event`（兩份重複） | 鎖內 reload | `save_state` | state `RLock` | `resolved_check_events` | 無 | `mutate` + action id `check-event:<event_id>`（PR2 合併為 `checks.events`） | `test_resolved_check_events` | **PR1 完成；PR2 合併重複碼** |
| 檢定後果來源 `_persist_check_consequence_origin` | 鎖內 reload | `save_state` | state `RLock` | `check_consequence_origins` | 無 | `mutate` | `test_resolved_check_consequences` | **PR1 完成** |
| 記憶維護 `keeper._persist_memory_maintenance_state` | 自開 `BEGIN IMMEDIATE` | `_save_state_unlocked` + `memory_chunks` | state `RLock` | `memory_chunks` 同交易 | 無 | `mutate`（`ctx.conn`） | `test_state_loss_amnesia`、`test_state_persistence` | **PR1 完成** |
| `_ensure_turn_timeline`、工具恢復標記 | reload／快照 | `save_state` | state `RLock` | `tool_recovery_markers` | 無 | `run_snapshot` | `test_state_loss_amnesia` | **PR1 完成** |
| `checkpoints.rollback` | 自開 `BEGIN IMMEDIATE` | `_save_state_unlocked` + `state_checkpoints` | state `RLock` | 回溯前 checkpoint | 圖片快取（提交後） | `mutate_value`（單一交易） | `test_scenario_activation`、`test_state_transaction_adapters` | **PR1 完成** |
| `checkpoints.create_checkpoint`（含開戰前自動 checkpoint） | 自開 `BEGIN IMMEDIATE` | 只寫 `state_checkpoints`（非遊戲狀態） | state `RLock` | — | 無 | 在 mutation 內改用 `state_transaction.ambient` 加入同一交易 | `test_state_transaction::JoinedTransactionTests` | **PR1 完成**（在 mutation 內自開交易會讓寫鎖互等 5 秒；已修） |
| `/coc` 角色 handler（11 個寫入點） | 無鎖 `load_state` | `save_state` | 對話 `asyncio.Lock`（router） | 隨 state | DM（秘密目標） | `transact`：驗證與變更都在最新 state 的交易內 | `test_pregen_and_creation`、`test_kp_sudo`、`test_state_transaction_adapters` | **PR1 完成** |
| `/coc` 戰鬥、地圖 handler | 無鎖 `load_state` | `save_state` | 對話鎖 | 隨 state | 無 | `transact` | `test_combat_start_in_combat_module`、`test_defeated_enemy_readd` | **PR1 完成** |
| `/coc newgame` | 無鎖 `load_state` | `save_state(reason="newgame")` 略過 revision 檢查 | 對話鎖 | 手動角色卡保留（`ctx.conn`） | 無 | `mutate` + `replace_state`，在最新 state 重驗「戰鬥未結算」 | `test_state_transaction::S5` | **PR1 完成** |
| `handlers/system.py` 其他寫入（約 20 處：scenario、kp、era、autoroll、start…） | 無鎖 `load_state` | `save_state` | 對話鎖 | 隨 state | 圖片快取、模板 | `commit_snapshot`（嚴格 revision + timeline） | `test_kp_authority`、`test_scenario_activation`、`test_unified_keeper_turn_flow` | **PR1 完成**（PR4 再搬到各 owner） |
| 上傳：`handle_pdf_upload`、`resolve_pdf_upload_choice`、`handle_map_upload`、`handle_role_sheet_upload`、`handlers/uploads._stage_pdf_parts` | 鎖內 reload | `save_state(mutate_tx=…)` | 對話鎖 | 劇本庫／手動角色卡同交易 | 圖片快取（提交後） | `commit_snapshot`；PDF staging 為 `amutate` delta | `test_upload_routing`、`test_manual_pregen_persistence` | **PR1 完成**（PR4 搬移） |
| 待處理按鈕認領／釋放 `services/pending_buttons` | 無鎖 `load_state` | `save_state` | 對話鎖 | `_buttons_posted` 標記 | Discord 傳送（鎖外） | `amutate` delta | `test_pending_button_latency`、`test_pending_control_completion` | **PR1 完成** |
| 更正服務 `narrative_corrections.save`、`correction_summary` | 鎖內 reload | `save_state(mutate_tx=archive)` | 對話鎖 | `narrative_correction_archive` 同交易 | 記憶標記（交易外、可失敗） | `commit_snapshot` | `test_narrative_correction_*` | **PR1 完成** |
| 舊檢定／Luck 解析（`_resolve_check_deterministically` 等 12 處） | 鎖內 reload | `save_state` | state `RLock` | `resolved_check_events` 另行保存 | 無 | `state_transaction.mutate` 內執行 `checks.service` | `test_check_engine`、`test_combat_wiring`、`test_scenario_action_check_handoff` | PR1 改道；**PR2 完成** |
| 營運腳本 | — | `db.set_json*`、`save_state` | — | — | — | allowlist（附理由）：`migrate_json_to_sqlite`、`migrate_skill_names`、`benchmark_*`、`codex_*` 煙霧腳本 | `test_architecture_state_writes` | 明列例外 |

低階符號 `save_state` 仍留在 `repositories/group_state.py` 作為 storage primitive（測試 fixture 使用）；`_save_state_unlocked` 改名為 `write_state_tx`。AST gate：`tests/test_architecture_state_writes.py`。

## 2. 檢定（PR2 完成；「遷移前」是 2026-10-04 在 `main_v2` `2affd06` 的實測，「PR2 之後」是目前狀態）

| 路徑 | 遷移前 | PR2 之後 |
| --- | --- | --- |
| Keeper 工具建立／自動擲骰 | `keeper_tools/checks.py`（607 行）自帶 tier／Luck／SAN／INT 邏輯與事件種子 | `keeper_tools/checks.py` 只剩登記 pending、快取與 `ToolSpec` 轉接；自動擲骰呼叫 `checks.service.autoroll_skill`／`autoroll_sanity` |
| 玩家擲骰（指令／按鈕） | `legacy_commands._resolve_check_deterministically`（約 390 行）、`_resolve_luck_decision_deterministically`、`handlers/buttons.py` | `checks.service.resolve_player_check`／`resolve_luck_decision`；`commands/handlers/checks.py` 為唯一轉接，按鈕沿用同一組 `handle_check_command`／`handle_luck_decision` |
| 戰鬥管轄的檢定 | `legacy_commands._resolve_managed_check`／`_resolve_managed_luck` | `services/managed_checks.ManagedCombatChecks`（`ManagedChecks` port 的實作）；PR3 併入戰鬥引擎 |
| 生命週期 | `check_lifecycle.py`、`check_identity.py`、`luck.py`、`resolved_check_consequences.py`、`services/opposed_checks.py` | 保留並由引擎呼叫，不平行維護第二套；Luck 政策集中在 `checks/luck.py` |
| 重傷 CON | `keeper.apply_character_delta_in_state`、`combat.major_wound_blocked` | 不變（PR3 與戰鬥一起處理）；失敗標記改由 `checks.rules.apply_major_wound_failure` 一處套用 |
| 結算後敘事 | `legacy_commands._finalize_check_result` | `commands/handlers/checks.finalize_check_result`（行為相同，狀態改由 `load_state` 取得最新快照） |
| 已結算事件保存 | `keeper._persist_resolved_check_event` 與 `legacy_commands._persist_resolved_check_event` 各一份 | `checks.events.persist_resolved_event`（`check-event:<event_id>`），兩份皆刪除 |
| 後處理（delivery／maintenance） | `legacy_commands._deliver_side_effects`、`_spawn_post_turn_maintenance`、`_run_post_turn_maintenance_*` | `services/post_turn.py`（PR4 前先移出，供 check 與 router 共用） |
| 型別別名（`Reply`、`SendDM`…） | `legacy_commands` | `commands/types.py` |

## 3. 戰鬥（PR3 的盤點）

| 模組 | 行數 | 備註 |
| --- | --- | --- |
| `combat.py` | 1888 | 先於 `combat_flow` 載入；四處函式內 `from app import combat_flow`（102、1395、1617、1714 行）形成循環 |
| `combat_flow.py` | 1387 | 頂層 `from app import combat`；managed pending／choice／settlement |
| `combat_resources.py` | 677 | working resource ledger |
| `combat_rules.py` | 304 | 純規則（維持純淨） |
| `keeper_tools/combat.py`、`managed_combat.py`、`resource_bridge.py` | 374／352／124 | tool 與 transport 橋接 |
| 開戰前 checkpoint | `combat._checkpoint_before_combat` | PR1 已改為加入同一個交易 |

## 4. legacy_commands（PR4 的盤點）

`app/legacy_commands.py` 2590 行，runtime 匯入者：`commands/router.py`、`commands/__init__.py`、`handlers/{buttons,character,combat,correct,map_handler,system,uploads}.py`、`discord_bot.py`；另有 25 個測試檔與 3 個腳本。符號與預定 owner（PR4 實作）：

| 符號群 | 預定 owner |
| --- | --- |
| `Reply`／`SendDM`／`SendImage`／`FormatMention`／`PdfChoice` 型別 | **PR2 已完成**：`app/commands/types.py` |
| `handle_pdf_upload`、`resolve_pdf_upload_choice`、`_apply_new_scenario`、`_apply_scenario_correction`、`_install_library_context`、`_pdf_upload_confirmation_text`、`_merge_extracted_pregens`、`handle_scenario_compare_upload`、`handle_role_sheet_upload` | ingestion application service |
| `handle_map_upload`、`_resolve_map_action_*`、`_find_room_via_rag`、`_find_scene_map_by_location` | map service |
| `_claim_pregen`、`handle_pregen_luck_roll`、`_blocked_by_*`、`_heal_character`、`_build_readiness_roster`、`_pregen_full_sheet_text`、`_set_character_away_state` | character service |
| `_check_*`、`_resolve_*`、`_finalize_check_result`、`handle_check_command`、`handle_luck_decision` | **PR2 已完成**：`app/checks` 與 `commands/handlers/checks.py` |
| `_deliver_side_effects`、`_spawn_post_turn_maintenance`、`_run_post_turn_maintenance_*` | **PR2 已完成**：`services/post_turn.py` |
| `handle_roll_command`、`handle_unsupported_message` | handlers |
