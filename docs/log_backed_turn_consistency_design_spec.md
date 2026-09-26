# Log 驅動的回合一致性與重試診斷修正

狀態：已獲准並完成 runtime 與回歸測試；真實模型複測尚未執行。分支：`bug/log-backed-turn-consistency`。
基底：`origin/main_v2`，`95d8ca35e21379c9f976b295e60d3926f10d6c0f`。

完整程式追蹤、接口與流程圖：[source review](log_backed_turn_consistency_source_review.md)。

## 1. 目標與證據

依現有 main_v2 `.env`、隔離 DATA、玩家 log 的 50 組配對／100 回合測試，
修正可重現的狀態／敘事交接問題，改善重試診斷；不把統計不顯著視為程式錯誤。
測試執行模型為 gpt-6-luna、medium、MAX_TOOL_ITERATIONS=12。

本機證據根目錄：`/private/tmp/coc-scope100-mainv2/`。
包含 `results.jsonl`、`runs/<pair>-<arm>/{initial.json,final.json,result.json}`、
`REPORT.md`、`FAILURES.md`、`completion.json`。此規格保留必要摘要，
不把 API 金鑰、完整玩家紀錄、劇本或生產 DB 提交進 repo。

| 證據 | 觀察 | 可得結論／限制 |
|---|---|---|
| 全部 100 回合 | 375 次邏輯請求；完整／動態各有 121／113 次限流重試，明確等待 408／351 秒 | 有 429，不代表 semaphore 壞掉，也不能判定一定是 TPM 或 RPM |
| 耗時配對 | 動態減完整平均 -1.38 秒；95% bootstrap [-4.55, +1.72] | 尚無穩定加速證據；包含零不是 bug |
| 01/11/21/31/41，兩組 | 玩家澄清沒有攻擊；Executor 無工具；Narrator 接受澄清，最後仍提示投擲 | 10/10 原始契約失敗；但舊 action_context 指製作，不能直接認定必須取消 |
| 05/15/25/35/45 scoped | start_combat、add_npc_to_combat 等已執行；沒有 Marco pending；敘事描述出拳／等待判定 | 開戰先手為 Ken；不能盲目替 Marco 建 pending 或跳過 Ken |
| 25/35/45 full | 同樣無發話者 pending | baseline 也能重現 |
| 35 scoped | 無 pending，回覆仍說「完成這次鬥毆攻擊檢定後，才能確定結果」 | 無效的檢定後續指示未被目前一致性檢查攔下 |
| 07/27 scoped | Ken 背包仍有一瓶煤油，輸出卻稱用盡；歷史有移除「煤油兩瓶」紀錄 | current inventory 與历史事件被混讀；移除兩瓶不能證明未來永遠沒有煤油 |
| 10 scoped | skill_check 後 clear_pending_check；敘事沒提供調查結果 | 劇本允許粗略查看發現空心處；原測試要求一定 pending 太窄 |
| 36 scoped | 要求 Marco 等順位，建立 Ken DEX pending | 可能是合法暫緩；不可為提高測試分數強迫建立 Marco pending |

原測試契約通過 42/50、36/50，並非完整規則正確率。新的回歸條件要先
修正以上歧義，再評估行為改善；不改寫原 100 回合的分數。

## 2. 程式查核與根因分級

### 2.1 已確認：Executor 的決策理由沒有交給 Narrator

`app/services/prompt_config.py:EXECUTOR_INSTRUCTION` 告訴模型文字摘要給下一階段看；
`app/agents/executor.py:run_executor` 卻直接 await provider，丟棄回傳字串。
Narrator 只收到 gateway 記錄的工具結果；沒有工具時一律成為「這句話不需要任何機制判定」。
因此需要延後、等待別人、拒絕、免檢定成功、漏做工具，沒有可靠的不同狀態。
這是可由程式直接確認的交接缺口；既有 benchmark 未保存 Executor 最後原文，
所以不聲稱已讀到某次模型內部具體理由。

### 2.2 已確認：Executor 輸入缺少權威 pending；取消語意須重新核對

兩組 correction 案例工具軌跡皆空白；clear_pending_check 仍有提供。
既有 `docs/specs/bug-self-corrected-check-leaves-stale-pending.md` 曾加強工具說明，
但這批新樣本證明文字說明沒有涵蓋／可靠解決所有玩家撤回動作的情形。
Source review 發現更直接的輸入缺口：keeper._build_dynamic_prompt 沒有序列化
state.pending_checks / pending_luck_decisions；Executor 初始化自己的 check_status 為空，
既有 pending 是 Executor 完成後才由 Supervisor 補给 Narrator。模型只能猜歷史對話，
看不到權威 check_id、action_context、owner、狀態。
2026-09-26 本機以真實 01-full fixture（scene digest 讀取隔離為 None）驗證：
清空 pending 前後 _build_dynamic_prompt 字串完全相同；不需要 API 就可重現。

重要修正：該 fixture 的 skill 雖為投擲，action_context 卻寫「製作並妥善固定燃燒瓶」。
因此原本 stale_throw_cleared assertion 過度假定撤銷需求。沒有呼叫工具是觀察事實，
但不能把玩家否認攻擊等同取消任何製作風險檢定；需判定是否錯用技能／不需檢定／仍有效。
Supervisor 忠實把仍存在的 pending 交給 Narrator；單純刪掉最後一行提醒會掩蓋狀態錯誤。

另已直接重現 enforce_mechanic_check_consistency 的漏攔：空 pending/Luck/resolved 下，
「完成這次鬥毆攻擊檢定後，才能確定結果。」原樣通過。目前檢查偏向 /coc check、
按鈕、擲骰等字面，沒有涵蓋這種要求完成不存在檢定的形式。

### 2.3 已確認結構：背包是現況，消耗清單是追加式歷史

`add_carried_item` 更新 carried_items；`remove_carried_item` 附加
consumed_or_removed_items。重新取得同名物品並不移除歷史，這是合理事件保存。
`scene_digest._public_state` 同時輸出兩者；`keeper._build_dynamic_prompt` 也插入
latest digest，可能把較舊的背包／戰鬥快照與當前數值放在一起。
現有文字雖標示最新數值，仍沒有足夠明確區分「歷史移除事件」與「現在不存在」。
需解決的是上下文權威與快照新舊，而非清掉所有 tombstone 或補造物品。

### 2.4 429 原因仍不明；確認有診斷不足

`retry.py` 已有 jitter、Retry-After 與重試預算；OpenAI SDK max_retries=0，
避免 SDK／應用雙重重試。測試 concurrency=1 仍大量 429，所以降低本測試併發不能直接解決。
目前結果僅保存 rate_limit_exceeded、status=429、request ID 等；缺乏配額維度／reset
header，不能區分帳號其他程序流量、RPM、TPM、供應端節流。
另每個 fresh worker 都重新探測不支援的 temperature，一共 100 次協商重試；
這是測試冷程序特性，不能外推成暖 bot 每回合都有 400。

## 3. 修正方案

### A. 保留已存在那次 Executor 完成回應，明確交接裁決

- 不增設分類 agent、審稿 LLM、固定修補回合或額外工具步驟。
- 使用既有 provider.run_conversation 最後文字回應，要求小型 JSON 裁決。
- 新增內部 typed TurnResolution，放入 MechanicResult，非 GroupState 持久欄位。
- 欄位：disposition（no_mechanics／await_check／await_luck／deferred／
  resolved／resolved_without_check／cancelled／blocked／incomplete）、actor character_id、
  waiting_for character_id（可空）、相關 check_id、簡短 reason、evidence_refs。
- evidence_refs 只能指本回合真實工具結果、已載入劇本片段或可識別權威 state；
  自由文字理由不是新正典、不能建立檢定、扣血、建立敵人或修改背包。
- Python validator 依工具結果和最終 state 驗證 pending/Luck、角色、先攻與清除結果。
  自然語言的劇本裁決不聲稱能用 Python 完整驗證；沒有來源／解析失敗時為 incomplete，
  不以「無需機制」偽裝成功，也不再叫一次 LLM 自動補救。
- deferred 必須有可驗證的正在等待的人／既有 pending，公開回覆明確表示尚未執行。
  新開戰後以最終戰鬥順位判斷，不沿用開戰前的非戰鬥狀態。
- Narrator 收到經驗證的裁決及權威 state；不直接把原始 JSON／自由理由公開。
- 錯誤裁決不回滾已成功的工具、不要重播副作用；呈現未完成與下一個有效操作。
- 不新建自動 action queue，deferred 只表示本回合沒有執行，不暗示已排隊自動處理。

### B. 更正／取消和狀態一致

- 優先補權威 pending context：Executor 執行前提供當前 owner/character、type、skill、
  check_id、timeline、action_context、Luck 是否已擲；明確區分本玩家與其他玩家。
  值直接來自當前 state，不從 history／scene digest 重建；必要時包含 choice 防守上下文。

- Executor 提示詞明確區分「取消未擲行動」、「更正檢定參數」、「骰已結算」。
- 接受取消時先調用 clear_pending_check；更正依原型重建技能／選項／防守檢定，
  不把防守 choice 扁平化成普通技能。保留既有 HP、Luck 和防守上下文規則。
- cancelled 裁決必須符合被引用 pending 已消失，且 owner、character、timeline 相符。
  如果聲稱取消但 state 沒改，輸出 incomplete，不能同時說取消成功又請玩家擲舊骰。
- 不能只用「沒有」「不是」關鍵字自動刪資料；不能從 Narrator 自由文字推動 mutation。
- 不取消已結算結果／待 Luck 當作未擲骰；不同玩家 pending 不能被誤清。
- PR #86 的 /coc correct 尚未合併進本基底；本修正不假定該入口已存在。未來整合时保留其場外權限與流程，不另造第二個更正入口，也不把核准文字自動當作 pending mutation。

### C. 背包現況與歷史快照分離

- Executor/Narrator 都明示目前 active-character carried_items 是現有背包權威。
- 歷史 remove 只代表那次移除；不可推論同類物品永久為零，也不可再扣一次。
- 相同名稱重新取得、兩瓶拆成一瓶、轉交別人時都保留歷史，不能跨角色套用消耗。
- 組 prompt 時把 digest 的機制快照與 live state 分離；carried_items、HP、pending、
  戰鬥順位採即時 state；場景線索與歷史事件可保留，不把舊 inventory 當現在值。
- 不改舊 DB、不刪事件、不用模糊字串自動合併「煤油兩瓶」與「一瓶煤油」。
- 材料確認不清楚時用 get_character_sheet 或現有工具查證；拒絕理由須引用真實缺料，
  不能只因 history 說用過而否認 live inventory。保留原 RAG 檢索／敘事規則。

### D. 重試診斷與已知可避免等待

- 增加安全 allowlist telemetry：Retry-After、rate-limit limit/remaining/reset
  的 request/token 維度（SDK 有暴露才記）、delay_source、retry_after_s、
  backoff_s、參數協商 vs 429 分類。不要記完整 headers、prompt、錯誤 body 或 key。
- header 缺失就 unknown；不要從錯誤文字猜 TPM。以本機安裝 SDK／官方規格確認欄位後實作。
- 保留 timeout、取消、retry budget、slot 在 backoff 前釋放的行為；不加重試次數。
- 可新增明確「省略 temperature」設定，預設保留現在行為；讓已知不支援該參數的部署
  不必每次冷啟動先送一次無效請求。不硬編碼模型名稱，不擅自修改 main_v2 .env。
- 參數協商只接受明確 unsupported-parameter 4xx；不得因 429／認證／timeout 的字串
  出現 temperature/reasoning 就誤刪參數。現有 offending 判斷過寬，需獨立測試。
- 這批 log 不足以授權設計跨程序 TPM 排程；先改善診斷，限流本身無法保證從程式消除。

## 4. 流程與接口

```text
player / existing command router
  -> supervisor.run_turn
      -> context_builder.build_context (RAG rules retained)
      -> intent_router (no new LLM)
      -> executor.run_executor
          -> provider.run_conversation
              -> request/retry boundary -> safe retry metrics
              -> tool_gateway -> keeper._execute_tool -> persisted state
              -> existing final response -> parse TurnResolution
          -> validate resolution against final state + observed tool results
      -> mechanic_result {facts, check_status, turn_resolution}
      -> narrator.run_narrator
          <- live inventory / initiative / checks
          <- history labeled as history, validated resolution
      -> existing guard + deterministic consistency checks
      -> save / reply

No fixed additional LLM review request.
Resolved-check followup / opening fallback / KP Assistant retain their boundaries.
```

## 5. Tests：先重現，再修正

新增匿名小型 fixture，由真實 log 的必要 state 建構；不帶整份劇本／個資。

1. 舊 pending：先驗證 prompt 真的包含 state 的 check_id/action_context/owner；取消成功真的刪除；只聲稱取消但沒工具操作不能呈現成功；
   更正重建；另玩家 pending 不變；Luck／已結算不被刪；舊按鈕失效。
   拆開「明確撤回投擲攻擊」與「否認攻擊但仍做有風險製作」fixture，不要求後者必須取消。
2. 開戰先攻：start + add 後 current=Ken，Marco 動作 deferred；不偷推順位、扣彈、
   建無效 pending、敘事宣稱命中；合法反應／NPC 防守不被一律 off-turn 禁掉。
3. 在輪到玩家的場景，await_check 必須有真 pending；無 pending 的「完成檢定後」
   指示遭攔下；不只搜尋 /coc check 字面。
4. Executor JSON 回傳可交接；格式錯誤／未知 actor／假 evidence／工具已部分完成時
   使用 incomplete，既有工具副作用不重放；provider call 次數不增加。
5. 牆壁依劇本免檢定可揭露；不得為契約分數強迫擲骰；找不到依據時 unknown／補查。
6. 背包保留一瓶、歷史移除兩瓶；同名重新取得、不同角色持有、移除後未補貨；
   digest 舊背包不覆蓋 live state；不刪歷史、不憑空新增物品。
7. retry：429+Retry-After、缺 header、無效 header、header 大小寫、cancel、永久 4xx、
   參數協商誤判、omit temperature on/off；SDK max_retries 維持 0，預算維持原值。
8. snapshot／統計分析：429 與 400 次數分開，缺 tokenizer 不算零 token；
   不以目前過窄的 actor pending assertion 當完整 correctness oracle。

測試層級：真實 tool handler 的本機 DB 整合 + mock provider 的完整 supervisor 流程；
不是只測 prompt 字串。確認實作後跑相關 tests，再跑 repo 規定的 ruff/mypy/compileall/pytest。
模型是否真的選對工具仍須真實 API 驗證；本地 mocks 不能證明 LLM 選擇改善。
本輪不另外執行付費 100 回合。未來實測應使用修正後契約、相同資料配對，
並區分冷程序／暖程序，保留 raw 含重試耗時與 retry breakdown。

## 6. 範圍、非目標與待確認取捨

- 不變更動態工具清單策略；先修共同回合流程，避免把基底 bug 當 scoping 的效果。
- 不更換模型／推理強度，不固定多一次 LLM 審稿，不破壞原 RAG 減少往返設計。
- 不改敵人初始化的 armor/attacks/abilities 和多敵人不同名稱提示詞。
- 不重新引入 keeper.run_turn 玩家路徑；共用 tool handler 仍保留。
- 無持久 DB schema migration；新增 TurnResolution 為當回合內部資料。
- 嚴格裁決驗證可能使部分輸出顯示「尚未完成」，但不應以漂亮敘事掩蓋機制漏做。
- 需確認：先修權威 pending 輸入、敘事檢查與 inventory 快照，再採 A 的明確裁決交接（利用原完成回應），而非僅追加 prompt 字句；
  這是主要介面改動，須補無額外固定往返、三 provider 回傳型態相容的測試。

## 7. Changeset

- 起點：95d8ca35e21379c9f976b295e60d3926f10d6c0f
- 本次：log／程式根因調查與待審 spec；runtime 未修改，尚無 bug 已修好之宣稱。

## 8. 本機 source probe（零 API）

以 01-full initial.json 建立真實 GroupState，mock latest_digest 為 None，僅呼叫 prompt builder：
`dynamic_prompt_identical_with_without_pending=True`。
以空 check_status 呼叫 enforce_mechanic_check_consistency：
`invalid_instruction_passes_guard=True`（句子如 §2.2）。
兩項是修復前重現，尚不是通過的回歸測試；runtime 修改後應把期望反轉，並納入正式 tests。

## 9. Detailed source review update

51 個既有相關測試通過，但零 API 的新探測確認權威 pending 輸入缺失、Executor 裁決交接遺失、舊背包快照混入與無效檢定指示漏攔。修正優先序為權威 context、歷史 projection、明確裁決交接與最終 next-action 一致性；詳見 source review；以上為實作前的診斷紀錄。


## 8. 實作結果（2026-09-26）

```text
最新 state + 同 timeline 歷史事件（排除舊背包／戰鬥快照）
  -> Executor：角色、pending、Luck、背包、先攻、敵方完整機制
  -> 工具逐次提交 state；回傳 tool:N + 最新狀態投影
  -> 既有 completion 回傳 TurnResolution（不新增 LLM 請求）
  -> Python 驗證角色／timeline／check ID／成功工具引用／實際狀態
  -> Supervisor 按裁決指定角色整理檢定與 Luck，保留其他角色資料
  -> Narrator 依工具事實與已驗證裁決敘事
  -> Python 檢查下一步指示；不完整／暫緩／取消使用確定性回覆
  -> 玩家
```

- `turn_context.py` 集中權威狀態與歷史投影；空背包也明確提供。
- `turn_resolution.py` 驗證 `no_mechanics / await_check / await_luck / deferred /
  resolved / resolved_without_check / cancelled / blocked / incomplete`。
  `resolved` 接受已擲骰結算或可核對的狀態變更；擲骰必須引用當前角色、當前 timeline 的成功結果。
  非擲骰變更通過驗證後正規化為 `resolved_without_check`。
- 驗證只讀；JSON 格式錯誤或證據不足會標示 incomplete，不重做已提交工具、重骰或回滾。
- 取消必須有真實 clear_pending_check；暫緩不得掩飾已花費的彈藥或資源。
- 其他角色的 Luck 不覆蓋裁決指定的待檢定；所有角色的狀態仍保留在投影中。
- retry 診斷補充安全 quota headers、delay_source、Retry-After；不改既有重試預算。
  `OPENAI_OMIT_TEMPERATURE` 預設 false；僅明確 unsupported parameter 400 才協商移除參數。
  未修改實際 .env 或生產 DATA。
- PR #83 已另提交相容性規格 `151a1a6`（基底對齊 merge `0f09b10`）。
  建立檢定後提早結束仍未啟用；將來必須保留可驗證裁決交接。

驗證使用隔離 SQLite 與真實工具，provider/Narrator 使用 mock；涵蓋取消、多人等待、
歷史消耗後重新取得、工具部分完成、錯誤裁決與無效下一步指示。全套測試結果見 source review。

限制：來源引用可證明模型確實取得依據，不能證明所有劇本語意推論都正確；
`blocked` 的自然語言理由也不是完整規則驗證。下一步文字檢查是有限規則，非語意審稿模型。
尚未重跑付費真實 API，不能據此宣稱模型正確率或耗時已提升。


## 9. PR #89 真實 API 回歸修正

10 案診斷發現四個已提交變更的回合被降為 incomplete：獨立交接被既有偵查 pending
阻擋，另三案將非擲骰工具完成稱為 resolved，與原驗證器定義不符。使用者已要求先修本 PR。

```text
工具執行前：記錄背包／戰鬥旗標／發話者資源
  -> 真實工具提交
  -> 保存參數、結果、執行前資料與資源是否曾改動（僅內部）
  -> completion 裁決
  -> 本次新增／更換任何角色檢定或 Luck？是：不得完成
  -> 仍有發話者舊 pending？僅允許已驗證的獨立同物品交接；Luck 不豁免
  -> 已結算骰，或可核對的完整背包變更／戰鬥結束
  -> 非擲骰完成正規化為 resolved_without_check
  -> Narrator 同時收到「本次已完成」和「舊檢定仍待處理」
```

- 非擲骰證據採明確工具類別：add/remove_carried_item、end_combat。
  所有相關變更工具均須成功並被引用；背包最後回執須等於目前 state，工具必須有實際變更。
  end_combat 須原先 active、現在 inactive。查詢／空搜尋／no-op 不能獨自證明 resolved。
- 獨立交接豁免：恰好移除發話者一個物品、加入另一角色相同物品，完整核對數量差；
  僅可伴隨 search_scenario/get_character_sheet 查詢，舊 pending 原樣保留。
  舊製作檢定、新增他人檢定、Luck、失敗或漏引用工具仍保守判 incomplete。
- 暫緩還要查每個工具是否曾改動發話者資源；先扣彈藥再補回仍不可稱完全未執行。
- 原地修正不新增 LLM 請求、DB schema 或限流策略。驗證仍不保證劇本推論／所有工具意圖正確。
- 測試使用真實 Keeper 工具與 SQLite，涵蓋交接保留舊檢定、製作、結束戰鬥、失敗／缺引用、
  新建他人檢定、Luck、查詢與 no-op、彈藥補償；模型 API 複測另列，不能以 mock 當成實测。
