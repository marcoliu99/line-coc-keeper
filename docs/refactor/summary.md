# 四階段重構總覽

四個 PR 疊成一條線，每一個的 base 是前一個的分支。需求：[架構重構規格](../specs/refactor/architecture_refactor_phases_1_4_design_spec_zh.md)；盤點：[migration-inventory](migration-inventory.md)。所有結果都是離線驗證（SQLite、腳本化骰子、假 provider），沒有連真實 Discord、付費 provider 或真實模型。

## 1. 階段與 commit 對照

| 階段 | 分支（base → head） | PR | 程式碼 commit | 文件 commit | 結果報告 |
| --- | --- | --- | --- | --- | --- |
| 1 State Transaction | `main_v2` → `refactor/phase1-state-transaction` | [#165](https://github.com/marcoliu99/line-coc-keeper/pull/165) | `651455a727ea5fa8e3152e48eda11a7eadb6475c` | `ae0a614496f579faa71f71161a74a44b552a405c` | [phase1-result](phase1-result.md) |
| 2 Check Engine | phase1 → `refactor/phase2-check-engine` | [#166](https://github.com/marcoliu99/line-coc-keeper/pull/166) | `d2f3edb04c21634abc47af2bdbadcadc14300af7` | `c57122f58e0d43c6ff6c810d9a48383b695b23d5` | [phase2-result](phase2-result.md) |
| 3 Combat Engine | phase2 → `refactor/phase3-combat-engine` | [#167](https://github.com/marcoliu99/line-coc-keeper/pull/167) | `0eab400246b6dfe46a74eed94ceeacf4a500f00f` | `a3bebe11f259796ef97fc120e19a0e645c4f68f4` | [phase3-result](phase3-result.md) |
| 4 退役 `legacy_commands` | phase3 → `refactor/phase4-retire-legacy-commands` | [#168](https://github.com/marcoliu99/line-coc-keeper/pull/168) | `89ac20bec9d7154c579e572d367671afed9958f4` | 本 PR 的 head commit | [phase4-result](phase4-result.md) |

`main_v2` 基線為 `2affd06c9b90105d454d19c6eb4f7e7c1fb232f8`。因為是疊式分支，合併順序必須是 #165 → #166 → #167 → 本 PR；每個 PR 合併後，下一個 PR 的 base 需改為 `main_v2`（或由合併工具自動 retarget）。

## 2. 測試證據

指令：`ruff check .`、`mypy app`、`pytest`。環境：Python 3.13.14、pytest 9.1.1、ruff 0.16.8、mypy 2.3.1、SQLite 3.45.1。

| 時點 | ruff | mypy（檔案數） | pytest |
| --- | --- | --- | --- |
| 基線 `main_v2` | 通過 | 通過 | 2048 項 |
| 第 1 階段 | 通過 | 130 | 2107 項（+59） |
| 第 2 階段 | 通過 | 144 | 2154 項（+47），1 skipped |
| 第 3 階段 | 通過 | 146 | 2202 項（+48），1 skipped |
| 第 4 階段 | 通過 | 149 | 2228 項（+26），1 skipped，223 subtests |

唯一的 skip 是 `tests/test_codex_analysis_smoke.py`，它需要已登入的 Codex CLI，**這個環境沒有，未執行**。

既有測試只改了接縫與目標名稱（fixture 改用真實 SQLite、patch 目標改到新的擁有者模組），沒有改任何行為預期；golden 預期值沒有被靜默更改。

## 3. 各階段的結果

| 階段 | 做了什麼 | 可量測的結果 |
| --- | --- | --- |
| 1 | 所有遊戲狀態寫入收斂到 `state_transaction`（`BEGIN IMMEDIATE`＋每對話鎖＋action ledger＋timeline 驗證） | 寫入旁路 6 條 → 1 條；單寫入 median 2.85 ms → 2.30 ms、p95 3.83 → 2.63 ms（本機單寫入者，不是玩家延遲） |
| 2 | 檢定規則（純函式）與有狀態的 service 分開；已結算事件保存、tier／Luck 邏輯各只剩一份；戰鬥檢定經 `ManagedChecks` port | 12 個重複／平行函式刪除，由 `test_architecture_checks` 守門 |
| 3 | 戰鬥所有動作經 `CombatEngine.handle`；模式（IDLE／LEGACY／MANAGED）只在入口判定一次；`combat` ↔ `combat_flow` 循環消除 | 工具呼叫數 2 → 2（沒有減少，原本就是一次 domain action）；引擎分派成本 median 約 +0.15 µs；`combat.py` 的旗標讀取 15 處 → 2 處 |
| 4 | 刪除 `app/legacy_commands.py`（原 2590 行），其職責移到 `scenario_ingestion`、`map_service`、`character_service`、`handlers/messages` 與前兩階段的引擎 | 所有匯入者 10 個 → 0；上傳流程的 trace 與退役前逐項相同 |

## 4. 已修復的 baseline 缺陷

* `newgame` 後舊局的快照能通過 revision 檢查並覆蓋新局（第 1 階段；現在同時要求 timeline 相符）。
* handler 在鎖外 load，與背景寫入競爭時玩家看到 `StateRevisionConflict`（第 1 階段）。
* 對沒有進行中戰鬥的對話呼叫只屬於 managed 的工具，會憑空建立一場空的 managed 戰鬥（第 3 階段；現在回 `{"ok": false}` 且不寫入）。

## 5. 已知限制與已做的決定

1. **Luck 政策衝突（已決定：維持現況並修改規格，程式未更動）。** 原需求規定 SAN／戰鬥檢定不可使用 Luck；現況 `combat_flow._request_check`（`app/combat_flow.py:173`）對戰鬥攻擊／防禦擲骰設 `allow_luck: not injury`，也就是提供 Luck 選項，並有測試釘住這個行為（例如 `tests/test_combat_wiring.py::test_managed_manual_roll_luck_retains_context_and_never_uses_legacy_ranged_rng`）。SAN 檢定已符合規格。產品決定採用選項 (a)：戰鬥攻擊／防禦擲骰維持可用 Luck，規格改為「SAN、瘋狂 INT 與戰鬥引擎登記的傷勢檢定（重傷、瀕死、穩定傷勢）不可用 Luck」；戰鬥以外串在重傷後的 CON 檢定從基線起就提供 Luck，維持不變。政策判斷集中在 `checks/luck.py`。不採用的選項 (b) 是把該處 `'allow_luck': not injury` 改成 `False` 並更新被釘住的測試。
2. 戰鬥傷害骰與反擊骰仍直接使用 `app.dice`，不是檢定引擎的 `DicePort`（測試以 patch 腳本化）。
3. `combat_flow` 內仍有 18 處 `is_managed`：它們區分「進行中戰鬥的工作副本」與「已結束戰鬥的已提交義務」，不是 legacy／managed 分支；消除它們需要一個明確的「義務範圍」概念。
4. `scenario_ingestion` 與 `map_service` 仍用嚴格快照路徑 `commit_snapshot` 而非 delta `mutate`；改變它會改變函式行為，不屬於搬移。`handlers/system.py` 仍然很大。
5. 工具呼叫本身沒有 action id；`LOG_ENABLED` 與 `LOG_TEXT_ENABLED` 都關閉時，擋不住同一則 Discord 事件被重新投遞。
6. logger 名稱由 `app.legacy_commands` 改為 `app.services.map_service`（一處 INFO log）；若有外部以 logger 名稱過濾的設定，需要更新。
7. typed extraction result 與 provider 抽象依規格留待後續階段。

## 6. 未執行項目（離線結果不代表線上驗證）

* 真實 Discord 群組與付費 provider 的連線測試。
* LLM round trip 數與玩家端延遲的實測（只量了工具呼叫數與引擎自身的成本）。
* 離線五人中文情境重播（需要真實模型或錄製的回應）。
* `tests/test_codex_analysis_smoke.py`（需已登入的 Codex CLI）。
* 沒有重新匯入任何真實劇本；OCR、版面、數值驗證、fallback 順序、匯入門檻、地圖抽取與 provider 選擇都沒有動。

## 7. 回退

四個階段都沒有改儲存結構、custom id 或指令文字，舊存檔不需遷移；還原任一 PR 後，舊程式讀得懂所有現有資料。疊式分支的回退順序與合併順序相反。
