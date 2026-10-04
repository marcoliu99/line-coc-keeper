# Game Opening Deep Module

[English](game_opening_module_design_spec.md)

## Status / Baseline

類別：`refactor`。狀態：**已實作**；實際邊界與驗證見 [架構紀錄](../../architecture/game-opening-refactor.md)。2026-10-05 `git fetch origin` 後的設計基準：`origin/main_v2` `b0e875ccb268966599a19f3823d377a7b4c7630f`，已包含 Scenario Lifecycle PR #173；實作驗證前已整合 `7ab07965eeebfa8e7ed638c67cc8e471f4c4626e`。本文從該版執行程式及測試重建行為，不沿用 #173 合併前的架構推測。`app/state_transaction.py` 在目前樹中的實際路徑是 `app/repositories/state_transaction.py`。

這是零意圖性 UX／遊戲規則變更的設計。實作前先加特徵測試；沒有新增 runtime schema。

## Existing Flow

```text
Discord /coc start
  -> router 的 conversation lock（含 post-turn hook）
  -> system handler 的戰鬥替換防護、早期就緒檢查
  -> state lock：重讀、heal_character、必要時 commit_snapshot [交易 H]
  -> 公開回覆就緒名冊
  -> to_thread(extract_opening_narration -> provider.analyze_text)
       ├─ found=True：state lock、重讀、可選全隊檢定准入／登記、
       │             opening_instruction + assistant history、game_started、
       │             commit_snapshot [交易 S]；回覆開場、可選檢定說明
       └─ found=False：keeper turn lock + narration lock、refresh state、
                     supervisor.run_turn(opening_fallback)；Narrator 可用受限工具、
                     Guard、delivery、_commit_turn_result(start_game=True) [交易 F]；
                     釋放兩把敘事鎖；公開回覆／私訊／圖片、安排背景維護
  -> router post-turn hook 在 conversation lock 內 claim 新檢定按鈕 [可能另一次交易 B]
  -> 釋放 conversation lock；Discord adapter 在 finally 發布已 claim 的按鈕
```

這不是「一個從就緒到開場的 SQLite 交易」。它是兩個可能的權威提交（H + S 或 H + F），後接可選按鈕 claim B 與可見輸出。若沒有治癒，H 不存在。若 scripted check 被擋或 fallback 敘事失敗，H 可已成功而 S/F 不存在。

## Existing Lock Model

`router.py` 將 `start` 列於 `_SYSTEM_COMMANDS`，但只把 PDF／部分 `scenario` 子命令列為長操作例外。因此 `/coc start` 的整個 `handle_system_command()` 位於 `_conversation_lock_with_notice()` 下；此路徑未呼叫 `TurnHandoff.to_narration()`。同一 conversation 的正常命令不能在這段期間改動狀態，包含就緒名冊的 Discord I/O、`asyncio.to_thread(scenario_intro.extract_opening_narration)`、同步 provider `analyze_text`、fallback `supervisor.run_turn`、Narrator 的受限搜尋工具、Guard、公開輸出／私訊／圖片交付。開場擷取和 fallback LLM 的耗時即為同 conversation 排隊時間；超過約十秒時 router 可送排隊提示。

H 與 S 分別短暫持有同步 `get_state_lock()`；`commit_snapshot()` 本身再持 state lock、`BEGIN IMMEDIATE`，在同一 SQLite transaction 檢查已讀 revision／timeline 後寫入。fallback 另在 `locks.narrating_turn()` 同時持 keeper turn lock 及 narration lock（依序取得）；它們涵蓋 supervisor、Narrator、Guard、F，**不**涵蓋隨後的公開 delivery。外層 conversation lock 仍涵蓋 delivery。Router 的 post-turn hook 在外層鎖釋放前 claim buttons；發布在釋放後。不同 process 的 conversation lock 並不共享；SQLite revision／timeline CAS 保護已讀快照提交，fallback 的 start guard 防重，但**不**保證擷取期間另一 process 換劇本後舊開場不套到新來源，詳見風險。

架構問題是開場規則的所有權；延遲問題是慢速擷取／fallback 期間持鎖。本規格只解決前者。

## Current Transaction Boundaries

| 階段 | 讀／寫及權威性 | 慢速工作／可見輸出 |
| --- | --- | --- |
| 入場 | 在 conversation lock 內讀狀態；戰鬥替換防護及五項早期檢查，不寫入 | 拒絕時直接 reply |
| H：治癒 | state lock 內重讀，每位 `state.characters` 執行 `heal_character`；有 notes 才 `commit_snapshot`，revision 增加。無 notes 不提交 | H 後 reply 名冊，使用 H 讀取且可能被提交的 snapshot 與 notes |
| 擷取 | 使用 H 後的 `state.scenario_text`；沒有 state lock，仍持 conversation lock | `to_thread` + provider `analyze_text`；沒有 SQLite transaction |
| S：scripted | state lock 內再次重讀；檢定全隊准入、未知技能登記、兩筆 history、`game_started=True` 在單一 `commit_snapshot`；失敗全數不提交 | 成功提交後 reply 開場，再回可選 reason；被擋則只回 blocker |
| F：fallback | refresh snapshot，supervisor/Narrator/Guard；`keeper._commit_turn_result(start_game=True)` 在最新 state 上 append log、start flag、response chain 清除並提交 | 成功或失敗訊息於敘事鎖釋放後交付；背景維護在 reply/side effects 後排程 |
| B：控制項 | router hook 比較 pending diff，交易中寫 `_buttons_posted` claim；與 S 是**不同**交易 | Discord buttons 於 conversation lock 釋放後發送；失敗走既有 recovery |

`commit_snapshot()` 的 CAS 失敗不留下 S/H 的部分資料。H 已成功後的 S/F 失敗不會撤銷 H。fallback 的工具路徑若已提交自己的獨立效果，不能把 F 誤稱為「所有 fallback side effects 的單一交易」；此規格沿用 supervisor 的既有安全與 retry 語義，不重寫工具交易。

## Readiness Admission

在 `system.py` 的 start 分支**之前**，`resource_bridge.guard_replacement(guard_state)` 對 `start` 也會拒絕未妥善結束的戰鬥工作狀態。start 分支按順序拒絕：`not state.active or not state.scenario_text`、沒有 `state.characters`、`pending_pregen_luck`、`game_started`。Router 另在進 handler 前檢查 Help 提供的 `expected_revision`（若有）。`start` 沒有 KP 專用檢查，也沒有要求發命令者擁有調查員；任何能送命令的玩家可開始。

**沒有一般性的** `pending_checks`、`pending_luck_decisions`、待選劇本、角色 away/存活狀態、或「時間線必須已存在」入場門檻。`pending_pregen_luck` 與檢定後的 `pending_luck_decisions` 是兩個不同欄位。若先前已有 pending check：沒有 opening check 的 scripted 開場仍可開始；有 opening check 者由 `register_many()` 拒絕，不開始；fallback 不經這個登記 gate，仍可開始。pending Luck decision 也只在 scripted opening **有 opening check** 時由 check admission 擋下；fallback 不以此為一般門檻。不可在重構時順手統一三條路的准入。

## Investigator Healing Semantics

H 是 **pre-opening normalization**，不是 S/F 的一部分。它對目前 `state.characters` 的每位調查員補 BASE_SKILLS 缺項、修復異常 HP／MP／SAN 上限等；全九屬性皆為 50 時只發警語，不猜改值。`heal_character()` 回傳 notes 即觸發 H；notes 可能只有警語，所以「notes 非空」與「欄位必然被修正」不可混為一談。`commit_snapshot` 成功後 revision 增加；無 notes 時不提交。新開場是否找到、檢定被擋、fallback 生成失敗或後來的 transaction 失敗，皆不回滾已提交的 H。若 cancellation 發生在 H 之前，沒有 H；H 之後才取消則 H 留存。特徵測試須鎖定此行為。

## Scripted Opening Path

`scenario_intro.extract_opening_narration()` 是來源擷取 collaborator，不應讓 Game Opening 重寫 provider prompt/parser。found 且 text 非空時，S 使用**再次重讀**的 state，而不是 H 的舊 snapshot。若 `game_started` 已變真便直接返回；目前沒有另一個 scenario identity 比對，但外層 conversation lock 對同 process 的一般切換提供序列化。

有 opening check 時，對每個 `state.characters` 的 owner 建 skill 或 sanity candidate。skill：先以 `resolve_skill_value(..., register_unknown=False)` 算 target，這次不寫角色卡；全隊 `register_many()` 成功後，才用預設 `register_unknown=True` 再呼叫一次，將未知技能的 base rate 寫入角色卡。sanity 使用 `loss_success` 預設 `0`、`loss_failure` 預設 `1d4`。兩次解析不是多餘重複，而是避免 blocker 時漏寫角色卡。

## Opening Check Semantics

`register_many()` 先對所有 candidate `admit()`；任一 blocked 就回 blocked map，**不替任何玩家 register**。`admit()` 對同一玩家先看 `pending_luck_decisions`，再看 `pending_checks`，所以 Luck blocker 優先於該玩家的 check blocker；handler 取 blocked map 的第一個 owner 回覆相應中文文案。被擋時 S 不提交、`game_started=False`、不留新增 check/history/未知技能。成功時每名玩家有獨立 `check_id`，`timeline_id` 與 `origin_revision` 隨 S 一起保存。若沒有 opening check，現有 pending check 不會阻止 S。

## History Authority Semantics

S 的第一筆 `role=user`、`record_kind=opening_instruction`、`authority=claim`，內容為既有守密人開場指令；第二筆 `role=assistant` 預設 `record_kind=narrative`、`authority=presentation`，內容為開場文字。兩筆使用同一 `turn_id` 與當次 `timeline_id`，與可選 pending checks、未知技能與 `game_started=True` 在 S **同一**權威提交。F 透過 `_commit_turn_result` 寫 user claim 與 assistant presentation，同一 turn/timeline；不得將敘事提升成 canonical fact（ADR-0002）。

## Fallback Opening Path

沒有 scripted opening 時，handler 組既有 Keeper prompt，持 `narrating_turn`，refresh state 後若已開始就返回，否則以 `turn_kind="opening_fallback"` 呼叫 `supervisor.run_turn`。此 turn 不走一般意圖分類與 Executor；Narrator 只提供 registry 標記可在 opening 使用的受限工具，入口及 gateway 都會擋未授權工具。Narrator 之後走 consistency、Guard、`turn_delivery.finalize`，再由 `_commit_turn_result(start_game=True)` 提交 F；它在交易內讀最新 state、檢查 timeline、用 turn id + request fingerprint 的 action ledger 防重、避免重複 start、把 log 與 started 同時寫入並清除 provider response chain。`STALE_TIMELINE` 回 false，`CONFLICT` raise；不能自製較弱的 fallback commit。

Narrator 設 `narration_failed`（例如 provider 失敗）時 supervisor 提前回覆失敗文案，不呼叫 F；`game_started` 和 opening log 不新增，可再次 `/coc start`，但 H 與已送名冊留存。舊資料若尚無 timeline，supervisor 的 `_ensure_turn_timeline()` 可在 Narrator 前另做 timeline 初始化提交，即使 F 後來失敗也保留。若最後 F 拒絕舊 timeline／重複開始，supervisor 返回既有 stale 回覆。`run_post_turn_maintenance_after_output` 負責公開回覆、私訊／圖片 side effects 與背景維護；它不是 Game Opening 的交易。

## Extraction Failure Semantics

無 provider、空來源、provider 回 `None`／`found=False`、`found=True` 但空文字都變 `found=False` 而走 fallback。provider adapter 的常見例外通常被 `analyze_text` 捕捉並回 `None`。**並非所有失敗都被 `scenario_intro` 捕捉**：它直接呼叫 provider 並假設結果是 mapping；自訂 provider 直接 raise 或回非 mapping 時會冒出例外，H/名冊已完成但 F 不會開始。opening check 格式不合法（無 skill 或未知 type）則刪去 check，**仍走 scripted 開場**。改錯誤分類是 follow-up，不屬本次行為保持重構。

## Failure / Cancellation Matrix

下表的「無」指此次命令不新增；此前 state 一律保留。`CancelledError` 可在 `await reply`、`to_thread`、fallback 的各 await 發生；同步 SQLite commit 中途不因事件迴圈 cancellation 而產生半份 state。

| 位置／失敗 | H／權威 state | `game_started`、新 check、opening log | 可見輸出與重試 |
| --- | --- | --- | --- |
| H 前／H 提交失敗 | 無 H；交易 rollback | 無 | 通常尚無名冊；可重試 |
| H 後、名冊 reply 前或 reply 失敗 | H 留存 | 無 | 名冊可能未送／送出狀態不確定；可重試，可能再次看見名冊 |
| 擷取中取消 | H 留存；worker thread 可能繼續唯讀擷取 | 無 | 名冊已送；解鎖後可重試 |
| 擷取一般 provider 失敗／無開場 | H 留存 | 由 fallback 結果決定 | 名冊已送；走 F；非 mapping／冒出例外則不進 F |
| 擷取後、S 前取消／S conflict | H 留存；S rollback／未執行 | 無 | 名冊已送，沒有開場；可重試 |
| opening check blocker | H 留存 | 無 | 名冊後收到 blocker；處理既有 pending 後可重試 |
| fallback Narrator 失敗／取消 | H 留存；既有工具若先提交則依工具自己的交易語義 | 不設定 started、不新增 F opening log | 失敗文案若完成交付則可見；取消時可能只有名冊；可重試 |
| S/F commit 後、開場 reply 前取消／delivery 失敗 | H + S/F 留存 | 已開始；S 的 checks/log 或 F 的 log 留存 | 開場可能未送；一般 `/coc start` 會被已開始 gate 擋，不能用此命令補發；buttons 由 finally／recovery 處理 |
| 開場 reply 後、check instruction／maintenance 前取消 | H + S/F 留存 | 已開始 | scripted 可能只看見開場而未看見檢定說明；fallback 私訊／圖片可能未交付，但進入 `run_post_turn_maintenance_after_output` 後其 `finally` 仍排程維護；不重啟 |

此矩陣描述目前風險，不在重構時假裝外部 Discord 發送與 SQLite 可原子化。尤其 `asyncio.to_thread` 取消只取消等待，不能保證停止 provider worker；Option A 使其結果不會在取消後自行提交 state。

## Observable UX Ordering

拒絕時無名冊。通過 H 後，`build_readiness_roster(state, healed_notes, format_mention)` 用 H 後 snapshot 生成完整 HP/SAN／裝備／未認領預製角色名冊，**先 reply**，再開始擷取。S 成功：名冊 → 開場文字 → 有 `reason` 時的檢定指示；若 S blocker，名冊 → blocker。F：名冊 → `run_post_turn_maintenance_after_output` 的公開開場／失敗訊息 → 私訊／圖片；維護在輸出後排程。單次 call 最後才返回完整結果會把名冊延後，違反現有 UX，因此介面需容許早期 presentation callback。

S 的 pending checks 在權威提交後才由 router 的 post-turn hook 看到；Discord adapter 的 `ControlCompletion` 在命令前保存 pending diff，在外層 conversation lock **尚持有時** claim，將 `_buttons_posted` 寫成另一次提交，解鎖後才 publish。發布失敗走 pending-buttons 的 identity/recovery 流程。Game Opening 不製作按鈕，但 refactor 必須保留 hook 與外層鎖的相對時序。

## Problems / Leaked Knowledge

`system.py` 目前同時知道：就緒 admission、治癒先 commit、名冊先 reply、開場擷取、scripted/fallback 分派、全隊 candidate/未知技能的兩階段解析、history provenance、`game_started` commit、fallback 鎖與 supervisor turn kind、交付順序。這些決策彼此相關；若要更改開場規則，需碰 transport handler 與多個 domain collaborator。單純 `handler -> game_opening -> 舊 start helper` 且 handler 保留上述順序不符合 deep module delete test。

## Proposed Deep Module Boundary

建議 `app/services/game_opening.py` 為開場**模組**，對外一個小**介面**，內部協調 `character_service`、`scenario_intro`、`check_lifecycle`、`history_authority`、`state_transaction`、`supervisor`／Keeper 既有 fallback commit。它不匯入 Discord、命令 handler、通用 Narrator 實作或按鈕實作。`system.py` 只解析 `/coc start`、呼叫介面、以既有文案回覆；Router 繼續提供通用 conversation serialization 與 post-turn hook。

### Public Interface

建議概念型介面：

```python
async def open_game(
    conversation_id: str,
    actor_id: str,
    *,
    on_readiness: Callable[[Readiness], Awaitable[None]],
) -> OpeningResult: ...
```

`Readiness` 是名冊所需的不可變呈現資料（包括每位調查員的治癒 notes、裝備與未認領數），不含可寫 `GroupState`；handler 用 `format_mention` 呈現並在 callback 中 `reply`。`OpeningResult` 為少量 outcome（拒絕／被 check 擋／scripted 成功／fallback 結果）、開場文字、可選檢定 reason、既有 delivery 所需的私訊／圖片清單及 presentation key。文案維持 handler／presentation adapter；結果不需龐大類別階層。callback **只負責可見名冊**，domain 不得要求 handler 依 callback 回傳值決定下一個交易。module 在 H 成功後 await callback，再擷取與完成 S/F。若 callback 失敗，維持目前不繼續開場的語義。

介面不接受 pending dict、`sqlite3.Connection`、可變 `GroupState`、手動 lock、check candidate 或分段 commit 指令。Router 的通用外層鎖屬 command dispatch policy；start handler 不新增 lifecycle 專用上鎖。module 自己擁有 H/S 的 state lock 與交易、fallback 的 `narrating_turn`。fallback 結果交給既有 transport delivery collaborator；handler 不知道 F 的提交細節。

## Option A vs Option B

| Dimension | Option A：保留 Router 外層鎖 | Option B：分段開場、擷取時釋放鎖 |
| --- | --- | --- |
| locality | 開場政策收至一個 module | 同樣可收攏，但要多一套 claim/revalidation |
| latency | 同 conversation 仍等擷取與 fallback | 擷取可並行其他命令；fallback 是否也解鎖需另證明 |
| concurrency complexity | 沿用現有單 process 序列化與 state CAS | scenario/source identity、角色集合、pending、雙 start、按鈕及輸出需重驗 |
| behavior compatibility | 高；名冊與角色集合維持凍結 | 名冊後可切劇本或改角色，屬可見語義改變 |
| implementation size | 小；主要重置所有權 | 大；需調整 Router dispatch/hook 與 state machine |
| race risk | 既有跨 process／delivery 風險 | stale 擷取、ABA、舊 roster、新 timeline 的額外風險 |
| test burden | 特徵測試與既有雙啟動／conflict 測試 | 加七種 deterministic interleaving，及 source/version identity 驗證 |

## Selected Design

**選 Option A。** #173 的 Scenario Lifecycle 有 `scenario_library_id`、`scenario_variant_id`、`timeline_id`；劇本庫 manifest 有 `content_hash`，但目前 `GroupState` 沒有一個綁定 source version／fingerprint 的持久欄位。repair 可在原 timeline 更新 `scenario_text`；相同 library id／variant 不能單獨識別來源版本。只比對文字不足以處理同內容再發布的 ABA；硬比對 `state_revision` 則連 away/back、buttons claim、無關 state write 都會使擷取作廢，改變目前會持鎖完成的行為。且原本凍結的角色集合與名冊若解鎖會漂移。為這次純 architecture refactor 加 claim schema 或複雜 selectively revalidate 得不償失。延遲改善另案。

## Concurrency Protocol

Option A 維持 `Router conversation lock -> Game Opening -> (短暫 state lock + SQLite transaction)`；fallback 另按既有 `keeper turn lock -> narration lock` 次序。兩個同 conversation `/coc start` 依 Router 順序進入；第一個成功 S/F 後，第二個重讀見 `game_started=True` 而被擋。即使不同 process 繞過 Router 同一把 in-process lock，S 的提交 CAS 與 F 的 `start_game` 最新狀態檢查仍防止兩次 opening **commit**。但 S 在擷取之後才重讀並形成提交 snapshot；若另一 process **在擷取期間**換劇本，舊 opening 可能套到新 state。F 的 refresh 也可能把舊擷取結論用於新 state。這是目前存在的跨 process stale-source 風險，Option A 不把它偽稱為已解決；後續若要擴大跨 process 保證，先有來源 identity／測試及獨立行為修正。S 的早期 admission 在 outer lock 內，S 前重讀仍檢查 `game_started`；未來實作不可拿 H 的舊 snapshot 覆蓋新寫入。

本次**不**釋放鎖來執行慢速擷取，故不存在合法的 unlocked extraction apply protocol；不得把只檢查 `scenario_text` 的半成品當成 Option B。若後續要 Option B，先另案定義 extraction identity（至少 timeline + library/variant + 實際 source version）、回鎖後最新 admission（started、active、角色與 Luck／check blocker）、角色集變化規則、roster 已送後的 conflict UX，以及按鈕 hook 的 claim 次序，並證明 ABA 安全。当前行為凍結角色集合；若改選「commit 時最新角色」，需明列為產品行為變更，不能混在重構。

## Preserved Invariants

1. 戰鬥替換防護和現有五項就緒順序、非 KP 專用 admission 保持。
2. H 獨立、先於名冊與擷取；無 notes 不提交，已提交不因開場失敗回滾。
3. `pending_checks` 僅阻擋有 opening check 的 scripted S；無 opening check 與 fallback 不因此拒絕。
4. 全隊 opening check 全有或全無；未知技能成功後才寫入。
5. S 的 check/history/started 一次提交，並保留 ADR-0002 provenance。
6. F 重用 supervisor 與 `_commit_turn_result(start_game=True)`；失敗可重試，response chain 行為保留。
7. 名冊、開場、檢定指示及 buttons 的先後順序不變。
8. 同 conversation 開場仍持外層 lock；沒有新 schema 或 provider/OCR/combat 更動。

## Characterization Tests

實作前以真實暫存 SQLite 穿過 Router／新 module seam，將 mock 限在 provider、Discord/影像等外部協作者；觀察 persisted state、revision、log、pending、回覆順序。現有 `tests/test_unified_keeper_turn_flow.py` 只固定部分 fallback、兩名玩家 check id、Luck blocker 和 F 原子提交，許多 handler 測試 patch `load_state`／`commit_snapshot`，不足以固定持久化。

必補：無劇本、無角色、待擲預製 Luck、已開始、戰鬥替換拒絕、一般 pending check/Luck/待選劇本的非一般性 admission；無治癒不提交、H 增 revision 且早於名冊、S blocker／擷取例外／F failure 時 H 保留；scripted 無檢定、skill、SAN、畸形 skill、單人 pending check 或 Luck、全隊 all-or-nothing、未知技能只在成功寫入；兩筆 history provenance、相同 turn/timeline；found=False 與 provider failure fallback、F 成功／失敗重試／timeline mismatch、第二次 start；名冊先於開場、檢定指示在開場後、pending buttons 在 S commit 後可見；各重要 cancellation checkpoint 的可觀察 state 與輸出。

## Race Tests

Option A 仍須 deterministic 測試：同 conversation 並行兩次 start 只有一次 S/F 成功；在第一個擷取的 provider barrier 停住時，第二個 Router 命令（包括角色變更及 scenario switch）不能進入 mutation 段；**取得 S snapshot 後、commit 前**的直接交易 revision/timeline 衝突讓 S CAS 失敗；F 擷取後、最終 commit 前的 timeline 變更由 `_commit_turn_result` 擋下；pending buttons 只在 S commit 後 claim；取消擷取後 worker 不可自行提交；F narration 失敗不佔用 started。另用獨立跨 process barrier 測試釘住「擷取中換來源」的目前風險，不把它錯寫成已受 CAS 保護。若將來另案選 B，需再測：切劇本、換 timeline、雙 start、角色增刪／切換、pending Luck、pending check、無關 revision；事先明定每種 race 是繼續還是作廢，不得靠偶然的全 revision equality。

## Dependency Direction

```text
Discord adapter -> router（通用 conversation lock、post-turn hook）
                -> system start presentation -> Game Opening interface
                                            -> character_service / scenario_intro
                                            -> check_lifecycle / history_authority
                                            -> state_transaction / supervisor fallback
                                               -> Keeper 既有 turn commit
```

Game Opening 不反向 import handler、router 或 Discord。刪除此 module 的測試：若刪除後就緒、治癒、擷取分流、檢定准入、history 與 started 順序會重新散回 caller，才是有深度；若只剩一層轉發且 handler 仍協調 H/S/F，拒絕此設計。

## Non-goals

不改 Scenario Lifecycle、PDF/OCR、戰鬥與 ADR-0003 obligation scope、Keeper 規則、通用 supervisor／check semantics、storage schema、`/coc newgame`、provider error taxonomy 或 Discord 文案。既有 Discord/SQLite 非原子交付與跨 process 前置 admission 視為風險，不藉這次重構偷改產品行為。

## Implementation Plan

1. 先補前述持久化特徵與 deterministic race tests；現有測試需不改而通過。
2. 在 `game_opening.py` 定義小結果與 readiness callback；將 `start` 專屬的戰鬥替換防護從 `system.py` 的共用 guard 分支移入此模組，保持它在五項就緒檢查之前的順序；搬入其他 admission、H、擷取分流、S、F 協調，沿用現有 collaborators 與文案 key；不搬 provider/Narrator/按鈕實作。
3. `system.py` start 分支改為呼叫 `open_game` 並依結果呈現；Router 的泛用 lock/hook 不改；保持早期名冊 callback。
4. 對照逐階段 state／回覆／buttons，跑相關 tests、完整 pytest、ruff、mypy、`git diff --check`；檢查 `system.py` 不再懂 H/S/F ordering 或 pending internals。

## Risks

- 外層 conversation lock 長時間阻塞同 channel，是已知延遲成本，不是此次 architecture 失敗。
- `commit` 後、Discord reply 前的 cancellation 或發送失敗可能留下已開始但玩家未看見開場的狀態；本次保留。
- fallback 受限工具可能有獨立持久化效果，不能宣稱整個 fallback 是單一 SQLite 交易；必須保留既有 supervisor/turn contract。
- provider adapter 常見失敗映成 found=False，但非 mapping／未捕捉例外可在 H 後中止；要用測試記錄，改善另案。
- 按鈕 claim 是開場 commit 後另一次交易；交付 failure 依現有 recovery，不能把它移到 S 之前。
- conversation lock 是 process-local；另一 process 在擷取期間換劇本時，現有 S/F 沒有擷取來源 identity 的回鎖重驗，可能產生 stale opening。這是既有 correctness 風險，與本次是否把鎖往外搬是兩件事。

## Follow-ups

獨立評估開場擷取出鎖的 Option B、provider extraction error taxonomy、已 commit 未交付開場的重送 UX。這些皆需自己的特徵測試與產品決策。

## Ten design answers

1. H 是 pre-opening normalization；因為目前它先提交，失敗開場不會回滾。
2. H 已提交便保留；設計維持。
3. S 的全隊 check、history、started 必須同一次權威提交。
4. F 要沿用 `_commit_turn_result(start_game=True)` 的最新狀態、timeline、ledger、防重與 chain 語義，不寫新版本。
5. 本輪不把 slow extraction 移出 conversation lock。
6. 本輪不需 unlocked claim；若另案移出，現有 library id／variant／timeline 尚不足以識別同 ID 新來源，需另定 source version 與回鎖重驗。
7. 本輪角色集合在擷取期間不變；全隊 check 用 S 前重讀的同一批。Option B 的角色漂移須另作產品決策。
8. Router 序列化、S CAS、F 最新 started 檢查使只有一個成功。
9. `on_readiness` 先 reply；結果 delivery 在 S/F 後回開場，scripted reason 最後回；button hook 保持 commit 後 claim。
10. `system.py` 只需解析、render 與 transport delivery；不持有治癒／擷取／檢定／history／started 的 lifecycle policy。
