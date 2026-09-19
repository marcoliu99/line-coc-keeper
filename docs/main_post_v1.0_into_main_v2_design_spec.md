# `main` `v1.0` 後功能移植至 `main_v2` 規格書

## 1. 背景與結論

本次目標不是把兩條 branch 做機械式 merge，而是把 `main` 上 `v1.0` tag 後的功能，依照 `main_v2` 的現行架構重新移植。

已讀取並逐一檢查 `v1.0..origin/main` 的 11 個 commit、每個 commit 的 patch、測試與文件變更。檢查結果：

- `v1.0`：`7e78fba`
- `origin/main` 與 `origin/main_v2` 的 merge-base：`e114583`
- `origin/main_v2` 相對於 `origin/main` 多 108 個架構／功能 commit。
- `origin/main` 相對於 `origin/main_v2` 有 11 個 commit，但兩邊已經分叉成不同架構。
- `main_v2` 已採用 command router、handler、repository、Discord-only help 與 combat state；`main` 仍以較舊的單檔 command／state 結構為主。

因此不能執行以下任一種未經選擇的整合：

- `git merge origin/main`：可能把 `main_v2` 的新架構刪除或改回舊檔案。
- 將 `v1.0..main` 全部 cherry-pick：會把舊架構的檔案路徑與實作假設一起帶入，仍會造成大量錯誤或隱性回退。
- 直接以 `main` 版本覆蓋同名檔案：同名檔案的責任與資料模型也已不同。

本規格定義「逐功能移植」；Git merge 只保留在 branch／PR 的最後整合方式，不是功能移植方式。

## 2. 已審閱的完整 commit 清單

| 順序 | Commit | 類型 | 審閱結論 |
| --- | --- | --- | --- |
| 1 | `09ae7b3` | KP Assistant Explicit Canon runtime | 將 `!`／`！` 解析、KP-only canonical persistence 與測試加入 `main_v2` 的 `app/keeper.py`；不能直接覆蓋檔案。 |
| 2 | `05848e9` | PR merge commit | 只有把 `09ae7b3` 合入 `main` 的拓撲；不另外移植。 |
| 3 | `75a8e24` | LUCK 設計文件 | 定義 claim-time LUCK reroll、預覽標示與測試驗收；內容納入本次實作規格。 |
| 4 | `d42da7e` | LUCK + `/coc alloc` 修正 | 移植到 `app/pregen_extractor.py`、`app/legacy_commands.py` 的預覽函式、`app/creation.py`，並新增對應測試。 |
| 5 | `c9a2790` | 預製角色技能 canonicalization | 移植 `_translate_skill_names`、別名補充與同名技能取最大值規則。 |
| 6 | `b3ae73a` | 已儲存資料 migration | 以 `main_v2` 的 SQLite／repository schema 驗證後移植 `scripts/migrate_skill_names.py`，不可直接假定 source branch 的 mirror key。 |
| 7 | `faa8cf9` | 預製角色預覽上限 | 移植到 `app/legacy_commands.py` 的 `_pregen_full_sheet_text`；與 `main_v2` 現有 `Character.sheet_text()` 的 top-12 契約一致。 |
| 8 | `446964a` | PR merge commit | 只有把 pregen 功能分支合入 `main` 的拓撲；不另外移植。 |
| 9 | `082311a` | PDF 提示文字 | 移植到 `main_v2` 實際負責 PDF upload 的 handler／legacy compatibility path，不直接依 source `app/commands.py` 路徑套用。 |
| 10 | `1c18c87` | KP Assistant unified creates-canon | 將 manual `!`／`！` 與 deterministic tool canon 合併為單一路徑，取代 `main_v2` 目前舊 Explicit Canon 行為。 |
| 11 | `d7ca3e9` | PR merge commit | 只有把 KP unified 功能合入 `main` 的拓撲；不另外移植。 |

Merge commit (`05848e9`、`446964a`、`d7ca3e9`) 的 patch 已檢查；它們沒有第三套獨立功能，避免重複套用。

## 3. 目標架構對應

| `main` source | `main_v2` target | 整合原則 |
| --- | --- | --- |
| `app/commands.py` | `app/legacy_commands.py`、`app/commands/handlers/*` | 先找實際 command owner；預製角色預覽與 PDF upload 目前仍在 legacy compatibility path。 |
| `app/keeper.py` | `app/keeper.py` | 只移植相關 parser、format、canonical branch 與測試，不覆蓋整個 Keeper。 |
| `app/pregen_extractor.py` | `app/pregen_extractor.py` | 保留 `main_v2` 的 role-sheet parser、weapon、state reconciliation 與既有 Character mapping，只移植 LUCK 和 skill normalization。 |
| `app/creation.py` | `app/creation.py` | 在現有 `allocate()` 入口先 canonicalize skill，再讀寫 session。 |
| `app/skill_aliases.py` | `app/skill_aliases.py` | 保留 `main_v2` 既有 aliases，補入 source commit 新增且確認不歧義的 aliases。 |
| `scripts/migrate_skill_names.py` | 同一路徑 | 依 target 的 `db.list_keys/get_json/set_json` 與 character mirror 格式實作、測試。 |
| source tests | `tests/test_kp_assistant_v2.py`、新增 `tests/test_pregen_and_creation.py` | 測試行為契約，不複製與舊架構耦合的測試 fixture。 |

## 4. 功能規格

### 4.1 KP Assistant manual canon

#### 行為

- 只有 `speaker_role == "kp_assistant"` 時，訊息開頭的 ASCII `!` 或全形 `！` 才是 manual canon trigger。
- 只 normalize 第一個 marker 字元；正文不可整段 NFKC normalize。
- 移除 marker 後 `lstrip()`；若只剩空白，視為普通訊息，不觸發 canon。
- 玩家輸入 `!文字` 必須保持普通 player message，不可觸發 KP canon。
- provider 看到的 KP message 應為 `[KP Assistant] <effective message>`，不可再使用舊的 `[KP ASSISTANT / OOC HOST INSTRUCTION]` wrapper。

#### Persistence

- manual `!` 與成功的 deterministic game-resolution tool 共用 `kp_turn_creates_canon`。
- canonical branch 只寫一組 `state.log` user + assistant pair。
- pure manual canon 的 user entry 為 `[KP Assistant] <message>`，不附加假的 workflow 區塊。
- 有 deterministic tool event 時才附加 `[DETERMINISTIC GAME WORKFLOW]`、tool name、sorted JSON input/result。
- 只要該 turn creates canon，就不寫 `kp_ooc_log`。
- `openai_previous_response_id` 依一般 canonical commit 規則推進，不再被 manual `!` 清空。
- 移除／不保留獨立的 `_commit_kp_explicit_canon_turn_result()` compatibility path。

#### Target implementation

- `app/keeper.py`：將既有 `_parse_kp_explicit_canon` 改為 unified manual trigger parser，調整 `_format_turn_message`、canonical history formatter、`run_turn()` flag 初始化與 persistence branch。
- `tests/test_kp_assistant_v2.py`：補 parser、ASCII／全形 marker、玩家邊界、pure manual、automatic tool、manual + tool、舊 marker 不存在等 regression tests。

### 4.2 預製角色 LUCK claim-time reroll

- `/coc usepregen` 呼叫 `pregen_to_character()` 時，LUCK 一律以 `3d6 × 5` 新骰。
- `/coc pregens` 顯示的 PDF 原始 LUCK 不得被呈現成 claim 後的固定值；若有原始值，標示「卡面 LUCK X，取用時將重新骰定」，沒有原始值則標示將於取用時骰定。
- 其餘 STR、CON、SIZ、DEX、APP、INT、POW、EDU 維持 extraction/default 行為。
- shared `state.pregens` 不得因 claim 被寫回新 LUCK；不同 owner claim 同一 pregen 時要各自獨立骰值。
- 使用 target `app.models._roll`，不在 `pregen_extractor.py` 複製第三份骰子實作。

### 4.3 技能 canonicalization 與 aliases

- `pregen_extractor._translate_skill_names()` 必須透過 `canonical_skill_name()`，不只查 dynamic dictionary。
- 兩個 source skill name canonicalize 成同一 key 時：若數值都是數字，取較大值；其他型別維持可保存值，不任意丟棄 homebrew skill。
- 補入 source commit 實際驗證的 aliases：
  - `求生 → 生存`
  - `鎖匠 → 開鎖`
  - `自然世界 → 自然學`
  - `喬裝 → 偽裝`
  - `威嚇 → 恐嚇`
  - `駕駛／飛行`、`駕駛/飛行 → 駕駛`
  - `步槍／霰彈槍`、`步槍/霰彈槍 → 射擊（步槍/霰彈槍）`
- 不把 `藝術/工藝`、`科學`、`語言` 等劇本特化技能強行加入靜態 alias table；它們沒有單一正確 canonical key。
- `creation.allocate()` 在任何 session.skills read/write 前 canonicalize，確保「手槍」等 shorthand 不會建立第二個不可被實際檢定讀到的 entry。

### 4.4 已存在資料 migration

- migration 來源必須直接 import `app.skill_aliases.SKILL_ALIASES`，禁止維護第二份 rename table。
- 處理 target 實際存在的 `group_states` 中 `characters` 與 `pregens`，以及 `characters` mirror table 的 `sheet.skills`。
- alias 與 canonical key 同時存在且都是數字時取最大值；migration 必須 idempotent。
- 不在本次自動執行 production database migration；只提供明確的 `.venv/bin/python -m scripts.migrate_skill_names` 操作與 dry-run／測試保障。實際 production 執行另需明確部署決策。
- 必須確認 target mirror key（目前由 repository 以 conversation/owner 或 character id 組成）不被 script 假設成 source branch 的 key。

### 4.5 預製角色預覽與 PDF 提示

- `_pregen_full_sheet_text()` 只顯示技能值最高的 12 項，排序規則與 `Character.sheet_text()` 一致；少於 12 項全部顯示。
- LUCK 使用 4.2 的 reroll 標籤；secret goal 仍不可出現在公開 preview。
- 圖片較多 PDF 的提示移除固定「一分鐘左右」估算，改成「需要較長時間」的非承諾式提示。
- 這兩項修改應落在 `main_v2` 真正執行 upload／character command 的模組，不可只修改 source branch 的已改名檔案。

## 5. 實作順序與衝突處理

1. 先保留本規格書並等待確認；不得先 merge source branch。
2. 以最新 `origin/main_v2` 為基線，建立 integration branch 並 push。
3. 先移植低耦合資料規則：aliases、`allocate()`、pregen skill canonicalization、LUCK。
4. 再移植 preview／PDF 文案與 migration script。
5. 最後移植 KP Assistant unified canon，因為 `app/keeper.py` 是最高衝突與最高風險區域。
6. 每一個功能以獨立 commit 提交，commit message 要標示 source commit 與 target module；不可把整個 `main` merge commit 混入。
7. 若遇到 `main_v2` 已經有更晚的同類修正，以 `main_v2` 現況為準，只補缺少的行為與測試。
8. 所有 conflict 必須以 target 架構重新實作，不使用「選 ours/theirs 整檔」解決。

## 6. 測試與驗收

### 6.1 必要測試

- KP Assistant：完整既有 `tests.test_kp_assistant_v2`，加上 manual `!`／`！`、純 manual persistence、tool canon、玩家 `!` 邊界與舊 marker regression。
- Pregen／creation：新增 `tests/test_pregen_and_creation.py`，涵蓋 LUCK mock dice、無 luck 欄位、不同 claim 獨立骰值、shared pregen 不變、preview label、8 個其他屬性不變、alias collapse、max merge、unknown homebrew、`allocate()` stacking。
- Migration：isolated temporary SQLite，涵蓋 group state characters、pregens、standalone character mirror、max merge、idempotency。
- Preview：20 項技能只顯示 top 12，少於 12 項完整顯示。
- PDF handler：確認新的 processing message，並確認既有 PDF flow、pending upload 與 extraction 不受影響。

### 6.2 整合驗收

- `python -m unittest discover -s tests` 通過。
- `python -m py_compile` 覆蓋所有修改的 production modules。
- `git diff --check` 通過。
- `git ls-files -u` 無輸出。
- 不存在對舊架構 `app/commands.py`、舊 state repository 或 LINE adapter 的新依賴。
- `git diff --stat origin/main_v2...HEAD` 只包含本規格定義的功能、測試與文件，不得出現大規模架構刪除。

## 7. 風險與回復

- **資料風險**：LUCK 是 claim-time 行為改變；不改寫 shared pregen，但已 claim 的角色不應被自動重骰。測試與 migration 不得觸碰 live DB。
- **canonical history 風險**：KP manual canon 會改變 `state.log` 與 OpenAI response chain 行為，需以完整 regression tests 鎖定舊 marker 移除與 OOC/canon 互斥。
- **技能資料風險**：max merge 可能捨棄較低 duplicate；規格只對數字 alias collision 使用 max，並保留可識別的 homebrew key。
- **部署風險**：migration script 與程式部署分開，未經明確決策不自動執行。
- **回復方式**：透過 integration branch 的 PR review；若已合入，使用該 PR merge commit 的 revert PR，不改寫 `main_v2` 歷史。

## 8. 完成定義

只有在上述功能逐項移植、測試通過、`main_v2` 最新變更已重新對齊，且 PR review 確認沒有 source branch 舊架構被帶入時，才可視為完成。完成方式是建立供審核的 PR，不能直接 push 或 force-push `main_v2`。
