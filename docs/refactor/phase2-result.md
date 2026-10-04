# Phase 2 結果：Check Engine

分支 `refactor/phase2-check-engine`，基底 `refactor/phase1-state-transaction`（疊在第 1 階段之上）。規格：[`check_engine_design_spec`](../specs/refactor/check_engine_design_spec_zh.md)；需求：[架構重構規格](../specs/refactor/architecture_refactor_phases_1_4_design_spec_zh.md)；盤點：[migration-inventory](migration-inventory.md)。

## 1. 版本

| 項目 | 值 |
| --- | --- |
| Base SHA | `ae0a614496f579faa71f71161a74a44b552a405c`（第 1 階段 head；其下 `2affd06c9b90105d454d19c6eb4f7e7c1fb232f8` 為 `main_v2`） |
| 程式碼 commit | `d2f3edb04c21634abc47af2bdbadcadc14300af7`（`Route every check through one check engine`） |
| Result SHA | 本 PR 的 head commit；文件 commit 在 `d2f3edb` 之後 |
| 實際環境 | Python 3.13.14、pytest 9.1.1、ruff 0.16.8、mypy 2.3.1、SQLite 3.45.1、4 核 Linux |

## 2. 變更檔案與責任

| 檔案 | 責任 |
| --- | --- |
| `app/checks/service.py`（新） | 有狀態的檢定規則：`resolve_player_check`、`resolve_luck_decision`（玩家指令／按鈕）、`autoroll_skill`、`autoroll_sanity`（Keeper 自動擲骰）。取得交易給的最新 state，就地修改，回傳 `CheckOutcome`（`changed` 決定要不要寫） |
| `app/checks/rules.py`、`dice_port.py`、`luck.py`、`events.py`、`narration.py`、`skills.py`、`models.py`（新） | 骰子與算術（`DicePort`）、Luck 政策與花費、已結算事件與後果來源、結果文字、技能值解析、結果型別 |
| `app/commands/handlers/checks.py`（新） | 唯一的指令轉接：一次 `state_transaction.mutate` 結算擲骰 → Keeper 敘事 → 記錄事件。`handle_check_command`、`handle_luck_decision`、`finalize_check_result` |
| `app/services/managed_checks.py`（新） | `ManagedChecks` port 的實作（戰鬥管轄的檢定與 Luck），位於 check 與 combat 之上，兩者互不匯入；第 3 階段併入戰鬥引擎 |
| `app/services/post_turn.py`、`app/commands/types.py`（新） | 從 `legacy_commands` 先移出：送出後處理（傳送、背景 maintenance）與回呼型別別名 |
| `app/keeper_tools/checks.py` | 自動擲骰改呼叫 `checks.service`；NPC 擲骰走 `DicePort`；607 → 463 行 |
| `app/keeper.py`、`app/dice.py`、`app/services/opposed_checks.py`、`app/repositories/state_transaction.py` | 刪除重複的事件保存與屬性快照；新增 `dice.evaluate_roll`（`skill_check` 的純函式部分）；`roll_opponent` 可注入 port；`TxContext.reason` 讓 mutation 在執行中決定提交原因 |
| `app/legacy_commands.py` | 刪除玩家檢定／Luck 解析、結果文字與事件保存；2611 → 1184 行（PR4 繼續） |
| `app/commands/{__init__,router}.py`、`handlers/{buttons,system,character,combat,correct,map_handler,uploads}.py`、`app/discord_bot.py` | 改匯入新擁有者；`app.commands` 對仍在 `legacy_commands` 的名稱改為延遲載入，避免循環匯入 |
| `tests/test_check_engine.py`、`test_architecture_checks.py`、`check_dice.py` | 新測試與腳本化骰子 |

## 3. Inventory 前後

檢定的規則由三份各自維護的副本（Keeper 工具、`legacy_commands` 玩家解析器、戰鬥管轄版本）收斂為 `app/checks` 一份，戰鬥版本經 `ManagedChecks` port 接入。已結算事件保存原有兩份（`keeper.py`、`legacy_commands.py`），現在只有 `checks.events.persist_resolved_event`。細節見 [migration-inventory](migration-inventory.md) 第 2 節。

## 4. 驗證

指令（乾淨 checkout、Python 3.13 venv）：

```text
ruff check .          # All checks passed
mypy app              # Success: no issues found in 144 source files
pytest                # 2154 passed, 1 skipped, 222 subtests passed（第 1 階段 2107 項 → +47）
```

唯一的 skip 是 `tests/test_codex_analysis_smoke.py`（需設定 `RUN_CODEX_ANALYSIS_SMOKE=1` 並使用已登入的 Codex CLI），第 1 階段之前就存在，本次**未執行**。

驗收情境與對應測試：

| ID | 測試 | 結果 |
| --- | --- | --- |
| C1 同一 intent 經指令／按鈕／工具 | `test_c1_command_button_and_tool_settle_the_same_intent_identically`（事件欄位、HP/SAN/Luck、pending 完全相同，各門只擲 1 次）；`test_c1_luck_offer_is_the_same_for_every_door` | 通過 |
| C2 等級邊界與 bonus/penalty | `test_c2_tier_boundaries`（14 組：critical／extreme／hard／regular／fail／fumble，含技能值 40 的 96 與 50 的 96）；`test_c2_bonus_penalty_and_required_tier_reach_the_dice_unchanged`（困難門檻下 regular 擲出顯示為失敗） | 通過 |
| C3 autoroll 關閉 | `test_c3_autoroll_off_registers_a_pending_check_without_rolling_or_applying_anything`（未消耗任何骰、HP 不變、無事件與後果來源） | 通過 |
| C4 重送 | 連續重送、並行雙擊（2 執行緒＋Barrier）、Luck 按鈕並行重送：只擲一次、只扣一次 Luck | 通過 |
| C5 SAN 要求 Luck | `test_c5_sanity_check_never_offers_luck_and_a_luck_request_is_refused`、`test_c5_luck_policy_table`（7 組） | 通過；**戰鬥部分見第 5 節的衝突** |
| C6 Luck 成功／餘額被消耗 | 合法花費（扣點一次、事件記錄 Luck 變化）、餘額被其他 action 改成 2 後拒絕（無部分扣值、decision 仍開啟、revision 不變）、無效等級 | 通過 |
| C7 已結算後的新檢定與非戰鬥傷害 | `test_c7_settled_check_leads_to_a_new_check_and_non_combat_damage_with_the_original_event_kept`（新 check id、`caused_by_check_id` 連回原事件、原事件保留、傷害只扣一次不重擲） | 通過 |
| C8 SAN → INT 瘋狂鏈 | 手動模式：SAN 只扣一次、建立單一 INT pending（連回原檢定）、INT 只擲一次、瘋狂表只擲一次、重送不再擲；自動模式：INT 在同一請求內只擲一次 | 通過 |
| C9 五位玩家同時 pending | 5 執行緒同時結算各自的 pending，另有旁觀玩家的 pending 完全不變；另一玩家登記新檢定不會清掉他人 pending | 通過 |
| C10 對抗／強推 | 強推不提供 Luck；對抗檢定仍拒絕強推與固定難度（既有限制）。對抗結果與單次對手擲骰沿用既有測試（`test_scenario_action_check_handoff` 等） | 通過 |
| C11 重試 | 三次重試 continuation 只記錄一次事件與後果來源（重試不寫入、revision 不變）；工具自動擲骰同 turn 重試回傳快取、不重擲；敘事失敗後重試不重擲 | 通過 |

架構守門 `tests/test_architecture_checks.py`：`app/checks` 不匯入 Discord、`app.keeper`、`app.agents`、`app.providers`、`app.commands`、`legacy_commands`、combat 或 `managed_checks`（並以真實違規驗證守門會失敗）；`persist_resolved_event`、`build_check_narration`、`resolve_player_check` 等各只定義一次且在擁有者模組；舊的 12 個重複函式名不得再出現（刪除而非改名）。

既有測試的改動：只改接縫與目標名稱（指向新擁有者），沒有改任何行為預期。唯一改變斷言的是 `test_button_routing` 的邊界測試：`discord_bot` 原本「只從 `legacy_commands` 匯入型別」，現在型別已在 `app.commands.types`，因此斷言改為「不從 `legacy_commands` 匯入任何東西，且只從 `app.commands.types` 匯入那三個型別」（比原本更嚴）。`test_unified_keeper_turn_flow` 的檢定後處理測試因 `finalize_check_result` 簽章改變而重寫，驗證的內容（以 `resolved_check_followup` 呼叫 supervisor、帶入權威結果、`resolved_location` 為 None、maintenance 執行一次）相同。

## 5. 缺陷與衝突紀錄

| 類別 | 項目 |
| --- | --- |
| **需要你決定的衝突**（未更動） | 需求文件規定「SAN／combat 要求 Luck 必須明確 rejected」。SAN 已符合。但受管理的戰鬥流程對**攻擊與防禦擲骰**一直提供 Luck（`combat_flow._request_check` 設 `allow_luck: not injury`），`tests/test_combat_wiring.py::test_managed_manual_roll_luck_retains_context_and_never_uses_legacy_ranged_rng` 等測試釘住這個行為；重傷、瀕死、穩定傷勢檢定已是 `allow_luck: False`。改成「戰鬥一律不可用 Luck」會改變遊戲玩法，所以我保留現況、把政策集中到 `checks/luck.py` 並記錄。最小選項：把 `combat_flow._request_check` 的 `'allow_luck': not injury` 改成 `False`，並更新被釘住的測試（連同對應的戰鬥 Luck 按鈕行為） |
| 本 PR 引入後修復 | 測試發現先匯入 `app.legacy_commands` 時會與 `app.commands.__init__` 循環匯入失敗；已改成 `app.commands` 對仍在 legacy 的名稱延遲載入（PR4 刪除 legacy 後這段也會消失） |
| 行為差異（有意） | ① 無效 Luck 選項與餘額不足的拒絕，舊程式會寫入一次沒有變化的 state（revision +1），現在不寫（與規格「拒絕什麼都不寫」一致）。② 檢定後處理改用 `load_state` 取得最新快照，取代傳入物件的 refresh，內容相同。③ 已結算事件保存合併後，Keeper 工具路徑也要求調查員名稱一致（原本只比對 character_id）。④ 被另一檢定引出的檢定，事件與 pending 新增選用欄位 `caused_by_check_id`（需求「新 check 連回原 event」） |
| baseline 既有（未修，已記錄） | ① `combat_flow.roll_pending_check` 仍直接用 `app.dice`，戰鬥管轄的檢定尚無法注入腳本化骰子（第 3 階段）。② 重傷 CON 檢定（`keeper.py`、`combat.py` 建立的 pending）沒有 `caused_by_check_id`；它不是由已結算的檢定引出，而是由傷害引出，連結對象需要先定義 |
| 限制 | 工具呼叫仍沒有 action id（見第 1 階段）；autoroll 的同 turn 重試保護依賴 `turn_id` 快取，沒有 turn id 的直接呼叫不去重 |

## 6. 相容性與回退

* 舊存檔：不需遷移。pending／Luck 決定／事件結構不變；新增欄位只在新產生的資料出現，讀取時皆選用。
* 工具名稱、schema、輸出、Discord custom id、指令文字不變。`app.commands.handle_check_command`／`handle_luck_decision` 仍可匯入（現在由 `commands/handlers/checks.py` 提供）。`app.legacy_commands` 不再提供這兩個名稱；repo 內的腳本已改接。
* 回退：還原本 PR 後，舊程式讀得懂新寫入的資料；`caused_by_check_id` 會被忽略。
* 部署：未實際部署，也未連真實 Discord 或付費 provider。

## 7. 未執行項目

* 真實 Discord 群組與付費 provider 的連線測試（規格禁止作為預設環境）；離線結果不代表線上驗證。
* 離線五人中文情境重播：留待第 3、4 階段（需要統一後的戰鬥流程才完整）。
* `tests/test_codex_analysis_smoke.py`（需已登入的 Codex CLI）。
