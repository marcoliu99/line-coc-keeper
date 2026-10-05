# Game Opening Phase 2：解鎖開場抽取

## 狀態與基線

- 狀態：已批准 Phase 2A；PR A #183 已 merge，持久化的 active source binding 已進入基線。實作仍須通過下列競態測試。
- 實作基線：`origin/main_v2` `066667812289e2128322cd17bd49c5d0646300dd`，已包含 #173、#178、#183。
- 目標：縮短 `/coc start` 持有同一 conversation mutation lock 的時間；過期抽取結果不得啟動新劇本、新來源或新 timeline。
- 範圍：**Design B / Phase 2A，只解鎖 `scenario_intro.extract_opening_narration`**。fallback Narrator、Guard、受限工具、commit 與 delivery 沿用既有鎖與 pipeline。

## 現況與鎖成本

`router.py` 把 `start` 放在一般 `_SYSTEM_COMMANDS` 路徑，`_conversation_lock_with_notice` 包住整個 `handle_system_command`，並在離開前執行 pending-button claim hook。`game_opening.open_game` 在此範圍內依序做 admission、state lock 下的 healing/可選獨立 commit、`on_readiness` Discord 回覆、`asyncio.to_thread(extract_opening_narration)`、scripted final commit 或 fallback。`scenario_intro` 同步呼叫 provider `analyze_text`。scripted 結果的 public reply、可選 check instruction，以及 fallback 的 `supervisor.run_turn`、Narrator、Guard、public/private/image delivery 都在 conversation lock 內。背景 post-turn maintenance 是排程後獨立執行；button claim 在 Router lock 的 `finally` 中，Discord button publication 在 Router 返回後。

state lock 只包短同步 read/commit，不包上述 await。fallback 另取 `narrating_turn`（Keeper turn lock → narration lock）；普通文字可能透過 KP Assistant priority gate → conversation lock → Keeper turn lock，或 commit 後 `TurnHandoff.to_narration`。`start` 目前不使用 handoff，`opening_fallback` 的 Narrator 仍可用受限工具寫狀態，故不能套用普通 `player_action` 的 read-only Narrator handoff。沒有可用的 production 開場抽取與 fallback 延遲分布；現有 request/LLM span 不能在本調查中推算兩者佔比。實作應量測 `opening.extract.duration_ms`、prepare/apply lock hold、stale discard 次數，不記劇本名稱。

## 來源身分調查與先決條件

`GroupState` 持久化 `timeline_id`、`state_revision`、`scenario_library_id`、`scenario_variant_id`、`active_chapter_id`、`context_chapter_ids`、`scenario_text`；**沒有 active source fingerprint / generation 欄位**。library manifest 有完整解析文字的 `content_hash`，`read_source` 會驗 manifest 與文字一致；然而 `load_context` 產生的是章節視窗文字，而不是完整來源。相同 scenario ID 可經 repair 原地更新，且 repair 保留 timeline。`_save_scenario_source` 會以相同 ID 取代 library 目錄；manifest hash 存在 filesystem，並未和 active `GroupState` 一起原子提交。`scenario_intro` 真正讀取的是 `GroupState.scenario_text`，不是 library file。

所以 ID、timeline 或 state revision 單獨都不足：ID 無法分辨同 ID repair；timeline 無法分辨同 timeline repair；revision 會使 away/back、按鈕 claim 等無關寫入平白丟棄抽取。只 hash active 章節文字也不足：repair 可更動其他章節，使目前視窗相同而 full source version 已改。只在 apply 前讀 filesystem manifest 亦無法與 SQLite start commit 做原子驗證。

**已完成的先決條件**：PR A #183 在 Scenario Lifecycle 既有 activation/repair/chapter 安裝邊界，將已驗證 library manifest 的 full-source `content_hash` 與 active context 寫入同一次 SQLite commit 的 `GroupState.active_scenario_source_hash`。舊 snapshot 或 legacy library 來源沒有 hash 時保持空值；Phase 2A 對這類 state 必須繼續使用 Phase 1 coarse-lock 路徑，**不可從目前 filesystem 猜測補值**。

建議 token：

```text
OpeningExtractionToken(
    timeline_id,
    scenario_library_id,
    scenario_variant_id,
    active_chapter_id,
    context_chapter_ids,
    active_scenario_source_hash,       # DB 內已綁定的完整來源 hash
    context_sha256,                    # DB 中實際交給 extractor 的 scenario_text
    roster_identity,                   # 角色集合/綁定身分，非 source identity
)
```

每一欄都是從持久 SQLite state 得到的值；`context_sha256` 對 **實際抽取輸入** 算 SHA-256，不以長文字相等作比較。source hash 與 context hash 各有作用：前者辨別同 ID 不同完整來源，後者驗證抽取的章節視窗；variant 與 chapter binding 避免同來源不同呈現。相同 bytes 再發布而 hash 不變視為相同內容版本，因 extractor 僅依文字產出；若產品要求「每次發布即使內容相同也作廢」，須另有持久單調 publication generation，不能假裝 content hash 表達發布次數。filesystem 先發布、SQLite 後提交的既有非原子風險仍在；「active source」以 DB 已提交綁定為準，尚未 activation 的 library publication 不自動切換 active source。

## 三階段協定與 transaction boundary

```text
Phase 1 PREPARE — Router 提供的 conversation lock scope
  最新 state admission + combat guard
  state lock 下 heal investigators；有變化才獨立 commit
  從 healing 後 state 擷取 token、抽取文字與 immutable roster snapshot
  在 lock 內 await readiness callback（維持 readiness 的起始順序）
  Router claim hook 之後釋放 conversation lock

Phase 2 WORK — 沒有 conversation/state/Keeper/narration lock
  asyncio.to_thread(extract_opening_narration, captured_text)
  worker 只回傳資料；不得寫 DB、發 Discord 或自行 apply

Phase 3 APPLY — 重新排入 Router conversation lock
  state_transaction 的 SQLite BEGIN IMMEDIATE 內讀 latest state
  先比較 source/timeline token；再做最新 state admission/roster check
  scripted：基於 latest state register_many、skill install、history、game_started
            一次 authoritative commit，成功後才 deliver
  found=False：只有通過同一 revalidation 才進既有 locked fallback
  保持 lock 至最終 public/private/image delivery 與 button claim 完成
```

Router 擁有 conversation lock 的取得、排隊通知及 post-turn hook；Game Opening 擁有 prepare/work/apply 的**順序與 domain 判斷**。建議給 `open_game` 一個窄的 `mutation_scope` async context capability（由 Router 提供，不暴露私有 lock 物件）與 `on_readiness`、`on_completion` transport-neutral callbacks。Game Opening 在自己的流程內兩次使用 scope；handler 僅格式化與送出。最終 completion callback 必須在 apply scope 內，否則 commit 與 Discord 回覆之間會插入另一個 command，改變既有 final-delivery ordering。不得手動 `lock.release()/acquire()`，不得在 service import Router/Discord，亦不得讓 handler 呼叫 `prepare()`、`apply()` 來決定交易順序。Router 的 hook 在 prepare 退出時可執行一次（healing 不產生新 opening check）；最終 scripted commit 後必須再次在 apply 退出時 claim buttons，publication 仍在 command 返回後。

**驗證與寫入須在同一 SQLite authoritative transaction**：若用 `state_transaction.mutate`，在 mutation callback 對 `ctx.state` 比較 token、admission 與 group blocker，然後寫 final state；不得在 transaction 外 load/compare 再以另一份 snapshot commit。`commit_snapshot` 的 revision CAS 可攔掉並行寫入，但不應以 prepare 時 revision 相等作全流程 admission；apply 對最新 state 做選擇性 identity 驗證。若需要在 transaction 中把 `register_many` blocker 轉成現有 presentation result，維持 all-or-nothing 與原本拒絕文案。不得把 healing 併入 final commit。fallback 仍委託 `supervisor.run_turn(turn_kind="opening_fallback")` / Keeper `_commit_turn_result(start_game=True)`，重用 timeline、double-start、log/idempotency 保護。**fallback 的最終 Keeper start transaction 也必須在寫入前檢查同一 source token**：另一 process 可能在 fallback Narrator 執行時 repair 同 ID 來源，只有進 fallback 前重驗並不足夠。這應是既有 commit primitive 的窄 guard，不能在 Game Opening 重寫 fallback commit。若無法把 guard 傳到該 transaction，Phase 2 HOLD。當前 opening restricted tools 的 private/image 請求仍須在成功 commit 後才 delivery；未來若工具清單加入事先持久寫入，須重新審核此保證。

## 角色集合、readiness 與最終 admission

選 **C3：eligible 調查員集合或 active investigator 綁定改變時，丟棄抽取並請求重試**。理由：Option A 使這一集合在 readiness 至 opening 期間固定；C1 會對已離隊角色註冊 check，C2 會對沒出現在早期 roster 的新角色套用 opening check。`roster_identity` 應涵蓋排序後的 owner→character ID 與會影響 opening group-check membership 的 active binding／eligible 狀態；不要納入 HP、SAN、persona、技能值或整個 `state_revision`。final apply 仍以最新角色能力值建立 check candidates 並重新執行 admission；這些無關 identity 的資料變動本身不作廢抽取。

選 **R1**：healing 仍是獨立 pre-opening commit；readiness roster 仍在 extraction 前送出。解鎖後 readiness 可能因其他指令而過期，apply 必須重查，必要時回覆目前 blocker 或 retry。**D1 已批准**：允許別的 command 回覆出現在 roster 與 opening 中間，保留同一 `/coc start` 的因果順序 `roster → opening → optional check instruction`。這是 Phase 1 不會有的**刻意 UX interleaving**；不建立新的 output scheduler。readiness callback 失敗仍保留已提交 healing 且不抽取。

final admission 重新執行目前真正的規則：combat replacement guard、active/scenario_text、characters、pending pregen Luck、game_started；scripted 有 group check 時交給 `register_many` 判定 pending generic check / Luck decision；scripted 無 check 與 fallback 不增加 generic pending gate。source identity mismatch 與 admission changed 分別回 `stale_source` / `state_changed_retry` 或既有 rejection。另一個 start 可同時做昂貴 extraction，但 SQLite final mutation + game_started gate 只能容許一個成功；**不新增 durable/in-memory opening claim**，因此取消/崩潰沒有 claim recovery 負擔。

## 抽取期間 mutation 決策表

| Mutation | 抽取作廢？ | 最新 admission？ | 理由 |
|---|---|---|---|
| away/back | 否，若 roster 內容/eligible 集合未變 | 是 | 無關 revision 不應單獨廢棄；若角色呈現改變則 retry |
| setpersona | 否 | 是 | 不改 extractor 的 scenario 文字 |
| era | 否；若角色 readiness/能力因此變動則 retry | 是 | 依角色投影判斷，不用全 state revision |
| KP Assistant transfer | 否 | 是 | 身分轉移不更換來源 |
| HP/SAN/skill 等角色變動 | 否，除非改變 eligible membership | 是 | check candidate 使用最新值；不把數值塞進集合 identity |
| character add / retire | 是 | 是 | C3，集合變動 |
| active investigator switch | 是 | 是 | C3，綁定變動 |
| pending check created | 否 | 是 | 有 opening group check 才由 register_many 阻擋 |
| pending Luck decision created | 否 | 是 | 保留有/無 group check 的不同政策 |
| pending pregen Luck created/resolved | 否，若角色集合不變 | 是 | 最新 early admission 必須重查 |
| scenario repair，同 ID 新來源 | 是 | 是 | full-source hash / context binding 改變 |
| scenario use / new upload | 是 | 是 | ID 或 timeline 改變 |
| timeline reset / newgame | 是 | 是 | timeline token 改變 |
| map position change | 否 | 是 | 不改抽取內容 |
| public log append | 否 | 是 | 不改抽取內容；若已 start 由 game_started 阻擋 |

「否」表示可重用 extraction result，**不表示略過 final admission**。所有政策須由真實 state mutation 的持久結果測試，不以命令名稱推斷。

## 2A / 2B 與 A / B / C 選擇

| 面向 | 2A／B：只解鎖 extraction | 2B／C：連 fallback 解鎖 |
|---|---|---|
| lock latency | scripted provider 時間移出；fallback LLM 仍阻塞 | provider 與 fallback Narrator 皆移出 |
| concurrency | 一次 prepare/apply 重驗 | 另需跨工具寫入、Guard、turn commit 的 handoff |
| stale 風險 | extracted text/found=False 必須驗 source | 再加 Narrator 輸出、工具結果、delivery stale |
| supervisor 依賴 | 沿用 locked fallback | 必須重設 opening_fallback handoff 與工具語義 |
| output ordering | roster 與 opening 可插入他人訊息 | fallback public/private/image 更易交錯 |
| 大小/回歸面 | Router scheduling + Game Opening + source binding | 再擴及 supervisor/Keeper/Guard/delivery |

| 性質 | A：現有粗鎖 | B：解鎖 extraction（選定） | C：完整 opening handoff |
|---|---|---|---|
| extraction 阻塞 mutation | 是 | 否 | 否 |
| fallback 阻塞 mutation | 是 | 是 | 否 |
| source stale 控制 | process-local，跨 process 既有風險 | 持久 token + SQLite 原子驗證 | 同左且需保護工具/輸出 |
| narration ordering | 現狀 | fallback 現狀；roster 可 interleave | 需跨 pipeline 重設 |
| cross-process 要求 | 既有不足 | 持久 source binding | 持久 binding 與更多 turn identity |
| code/rollback/test/deployment 風險 | 最小 | 中；無新 claim rollback | 高；工具與 delivery 面擴張 |

**選 B / 2A**，但以 active source binding 與完整 race merge gate 為先決條件。若實作評估發現無法在 DB transaction 內原子比較 binding，**HOLD，退回 A**；不可退化成只靠 process-local lock 或 manifest pre-read。現無 production 延遲分布，不能宣稱 2A 已消除「主要」延遲；fallback 時間仍可能是主因。C 留獨立研究。

## Deterministic race-test merge gate

使用 real temporary SQLite、真實 `GroupState` / Scenario Lifecycle transitions 與 `state_transaction`；只以 `threading.Event` 控制同步 extractor（它在 `to_thread` 中）、`asyncio.Event` 控制 delivery/Narrator；不使用 sleep 猜時序。每測試：等 extractor entered → 用另一 Router command 或獨立 DB connection 提交 mutation → release extractor → 檢查持久 state、history、pending checks、roster/opening/他人 reply 次序、button claim。跨 process equivalent 測試不能分享 Python lock，應以獨立 connection 或 process 寫入 authoritative state。

| ID | 插入事件／預期 |
|---|---|
| RACE-01 | S1 → `scenario use` S2；S1 prose/check/history 全丟棄，S2 不 started |
| RACE-02 | 同 ID full-source V1 → V2 repair，含 active context 文字恰好相同的案例；V1 丟棄 |
| RACE-03 | newgame／timeline replacement；舊結果丟棄 |
| RACE-04 | 兩個 start 同時 extraction；只一個 game_started、history pair、group checks，輸家不送舊 prose |
| RACE-05 | add/retire/switch active investigator；C3 retry，不對舊或新集合安裝 partial group check |
| RACE-06 | pending pregen Luck 在抽取中出現；最新 admission 拒絕 |
| RACE-07 | pending generic check 出現；scripted no-check 可 start，group-check all-or-nothing 阻擋 |
| RACE-08 | pending Luck decision 出現；保留 no-check/group-check/fallback 三路差異 |
| RACE-09 | 只做 away/back、persona、map、log 等無關 revision；identity 不變且 admission 通過則可 start |
| RACE-10 | 取消 `to_thread` await 後 worker 完成；healing 保留，絕不 late apply |
| RACE-11 | 不共享 process lock 的另一 DB writer 改 source binding；舊結果被 SQLite 內 token 驗證拒絕 |
| RACE-12 | `found=False` 後、fallback 前切 source/timeline；不得對新 source 啟動舊 fallback 判斷 |
| RACE-13 | apply 驗證後競爭者寫入；同一 SQLite transaction／CAS 保證不可交錯或覆蓋 |
| RACE-14 | roster → 他人 command reply → opening 的 D1 訊息順序；同一 start 的 opening/check instruction 仍依序 |
| RACE-15 | scripted pending checks commit 後 Router hook 才 claim，且 Discord publish 在 command 返回後 |
| RACE-16 | `found=False` 已通過 apply revalidation，另一 DB writer 在 fallback Narrator 中 repair 同 ID 來源；Keeper final start commit 拒絕舊 token，且不送成功 opening/private/image |

另保留 Phase 1 characterization：healing 獨立 commit、readiness callback failure、scripted single commit、group blocker atomicity、history authority、fallback retry/timeline guard、delivery-after-commit failure、cancellation。效能 gate：extractor barrier 阻塞時另一個無關 mutation 可完成；`T_opening_extract_lock_hold` 不包含 provider extraction duration；fallback 仍持 lock 的觀測須如實報告。

## Crash、取消與失敗

- prepare/healing 後失敗：healing 保留、roster 可能已送；無 opening commit，可重試。
- extraction worker 被取消：thread 可繼續跑，但沒有 commit capability；呼叫 task 不再 apply。
- stale source/timeline/roster：不寫 check、history 或 game_started，不送舊 opening；回 retry/stale result。
- `found=False` 也必須通過 token 與最新 admission，才能走原 fallback；fallback Narrator 失敗仍可重試。
- scripted final commit 成功而 Discord delivery 失敗：維持既有 started state，不 rollback；再次 start 被阻擋。
- 無 durable claim：兩個抽取可重複耗費 provider 費用；崩潰無 claim 殘留。這是刻意取捨。

## 依賴方向、非目標與實作順序

`Router scheduling → system presentation → Game Opening orchestration → scenario_intro / state_transaction / check_lifecycle / history_authority / existing supervisor`。source identity 由 Scenario Lifecycle 安裝 active context 時持久綁定；Game Opening 只讀 binding，不接管 PDF/Markdown parser。Game Opening 不 import Discord、Router、buttons。不得改 opening prompt、pending check/Luck 規則、healing、ADR-0002 history authority、fallback tool contract、combat、一般 provider 或 OCR 行為。

實作順序：① source binding 契約與舊 snapshot 相容測試；② race tests 先紅（含同 ID 同 context repair）；③ Router 提供兩段 scheduling scope、Game Opening 保持單一 intention API；④ scripted/fallback revalidation 與 SQLite 原子 apply；⑤ output/button ordering、取消、跨 process 測試；⑥ latency 指標與全回歸。若 ① 無法保證活躍來源版本，停止 ③。

## 待審核決策與風險

1. D1 訊息交錯已批准；PR description 必須明列，並用 deterministic test 固定。
2. `active_scenario_source_hash` 以相同完整解析文字視為同版本；若發布事件本身必須作廢 extraction，需改用 persisted publication generation。
3. active source binding 的所有寫入點與 legacy snapshot 的 coarse-lock fallback 必須完整盤點；遺漏 chapter advance/rollback 將使 token 不可靠。
4. filesystem library publication / SQLite activation 仍非原子；token 必須定義為 DB 已啟用的來源，不能假設 filesystem 當下 manifest 就是 active binding。
5. Phase 2B（fallback unlock）需另外設計工具寫入、Guard、Keeper handoff 與 public/private delivery 的 ordering；本規格不授權其實作。
