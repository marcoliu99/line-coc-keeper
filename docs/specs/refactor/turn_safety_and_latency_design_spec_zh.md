# 回合正確性、結果恢復與延遲量測

[English](turn_safety_and_latency_design_spec.md) | [文件索引](../../README_zh.md)

## 1. 狀態、來源與結論

分類：`refactor`，包含 bug 修正與效能工作包。狀態：**S0／S1 與獨立 S3 已核准；本分支已實作 S3**。基準：`main_v2` 的 `8e32683a3e3a3c0159153b3d96d9c6911ec04071`，包含 PR #94 及 review 修正。審查日期：2026-09-27。後續已對齊 `main_v2` `e03d5dc`，Review R1–R6 補強見第 12 節。分支：`refactor/turn-safety-and-latency`。

本規格完整閱讀使用者提供的兩份 ChatGPT 文件，並對照目前程式後，選擇採納其中建議。不將文件較舊的程式基準直接當成目前事實。原始提案僅含文件；本分支 S3 實作與隔離驗證見第 13 節，未遷移正式資料、部署或進行真實 API 測試。

| 輸入文件 | 原審查基準 | 原檔 SHA-256 |
| --- | --- | --- |
| `main_v2_detailed_fix_plan_ux_latency_v2.md`（F1–F8、UX、E2E） | `afe8ace` | `ce79390c4d79ebe24e1eb93ea0118a213d0c3d6b7fb9a9a3639f8b26053d7224` |
| `main_v2_python_optimization_safety_addendum.md`（P1–P8、C01–C20） | `c05e152` | `1a9d9afa11bee169e005ca066abef41b34f2495aac9c545f14556933a277660f` |

**結論：** 先採納正確性修正與結果等價的本地優化，保留正常 Executor 工具後接續及現有 Narrator。跨重啟操作恢復與輸出補送分成後續可獨立驗收的遷移。Executor 提早結束延後為預設關閉、必須證明工作流程涵蓋範圍的獨立實驗。

原文件是審查輸入，不是目前 repository 已有的介面。本提案以以下決策為準。各工作包獲核准並實作前，現有已實作規格繼續有效。

## 2. 目前證據與逐項採納判斷

「已確認」表示程式查核或下列隔離重現，不表示正式遊戲每回合都出錯。「優化機會」表示可量測候選，不表示已證明是延遲瓶頸。

| 原建議 | 基準版本的實際證據 | 決定與調整 |
| --- | --- | --- |
| F1 移動 | `router._handle_ordinary_text_message_locked`、`_run_sudo_act_locked` 在 Supervisor 前呼叫 `_resolve_map_action_transaction`。helper 保存另外讀取的 state；regex 與房名比對可直接改位置。 | **已確認 bug，第一批。** 改純 proposal、共用提交驗證及 caller 快照更新。地圖連通不等於通路無鎖、無危險；無地圖劇本也要支援。 |
| F2-A 部分成功 | `executor.run_executor` 在 Exception 時把 facts 換成一般錯誤指示，但 inventory events、pending 與 DB 變更可能仍在。`enforce_mechanic_check_consistency` 又把 incomplete 敘事換成一般警告。 | **已確認具體事實交接遺失，第一批。** 上下游一起修；不能說全部已保存效果都消失。 |
| F2-B 操作帳本 | `GameEvent` 只有 type/payload，沒有持久化 operation identity。已有工具 recovery marker；原始碼搜尋只找到產生／序列化，沒有使用 marker 阻擋新操作的入口。 | **恢復缺口，分批重構。** 先補即時程序內 worker ownership／hold，再談放寬並行；持久化 ledger 後做。本次未重現完整的晚完成 worker 競態。 |
| F3 輸出恢復 | `_deliver_side_effects` 是 best effort；DM／圖片失敗記 log，缺圖略過。已有 receipts／correction；目前 DB 表沒有通用 delivery outbox。 | **後續採納。** 先維持 inline 時機，改唯一 outbox claimant；沿用 receipts，不承諾 SQLite 與 Discord 間 exactly-once。 |
| F4 玩家 OOC | `intent_router.classify_intent` 把玩家整句括號訊息當 PURE_ROLEPLAY；Supervisor 的 canonical commit 未區分玩家 OOC。KP Assistant 已有獨立路徑。 | **已確認路由缺口，第一批。** 區分身分、訊息用途與輸出對象；不能只因括號就吞掉真正動作。 |
| F5 提前路由 | Supervisor 等 `build_context` 才分類。Scenario／Memory 已並行並各自降級。 | **保守採納。** 先用目前 state 選路，再取 optional retrieval；保留合理 ACK 回應，一般 gameplay 在證明等價前保留原檢索。 |
| F6 角色鏡像 | `_save_state_unlocked` 每次 `SELECT key, data FROM characters` 後，upsert 所有當前 aliases；caller revision 在外層 commit 結束前就更新。 | **已確認多餘工作及提交邊界風險。** 先精確 key 查詢／diff，commit 後才回填 revision。Membership 表視需求後做，不是 ledger／outbox 前提。 |
| F7 分階段 prompt | Executor／Narrator 都包完整 Keeper prompt，重建 dynamic context；gateway 廣泛格式化工具結果。 | **structured partial facts 後採納。** 共用規則區塊，精簡重複機器內容；普通與可用工具的 Narrator 能力不同。 |
| F8 唯讀與鎖 | Router 指令分支使用 conversation lock；有同步讀存。Gateway 已有 offload／shield，maintenance 已移至背景。 | **先盤點、抽出真正純讀。** 不能只刪 lock，或把與 event loop 共用的 mutable state 丟去 thread。搬出發送需先有 outbox／barrier。 |
| P1 已知參數 | Purchase 注入 `_turn_key`／`_owner_id`，search 注入 principal；尚無通用不可變 execution context。 | **逐步採納。** 注入權威欄位，不猜不明 target，也不能把合法的其他玩家目標改成呼叫者自己。 |
| P2 檢定證據重用 | Follow-up 會更新 state，接收 structured resolved outcome／action context；尚無通用版本化 evidence reference 交接。 | **後續採納。** 重讀仍有權限的來源 reference；不保存舊 prompt，不拿 PR #94 cursor 當永久證據。 |
| P3 RAG 分組 | `_result_rows` 每個選中 record 都掃 eligible siblings；PR #94 的 v4 已走 `_ranked_rows -> scenario_retrieval.project`。 | **縮小範圍。** v3／legacy 每次搜尋先分組，獨立量測；不能宣稱加速 v4。 |
| P4 token／prompt 重用 | PR #94 已以 `provider_history` 共用 `request_budget` 的歷史選取政策；`select_history` 仍多次編碼 history／suffix。 | **部分已完成。** 再重用單次 request 的選取與計數，保持選取結果完全一致；不能重新計入被裁掉的全歷史。 |
| P5 snapshot／DB | `gameplay_snapshot` 序列化 state、移除部分欄位再 deepcopy；Executor 有 before/after，驗證非預期變更。 | **先量測、保留驗證。** 先做 mirror／serialization，不換成 nested reference，也不信模型的 changed-fields。 |
| P6 固定流程 | Purchase 已有原子服務；戰鬥已有 damage/effect services；交接 validator 已接受 add/remove 兩種順序。 | **選擇性後續工作。** 可考慮已授權的原子交接，不重寫購買／戰鬥，也不連帶啟用 macro combat backlog。 |
| P7 提早停止 | `enable_wrapup=False` 省迭代上限後 wrap-up，未省正常工具後接續；普通 Narrator 無工具。 | **延後、關閉。** 工具成功、有 pending、模型 done 都不能證明整個玩家行動已處理。 |
| P8 敘事需求 | 已有 validated resolution、多 actor current state；一般 incomplete fallback 會蓋掉具體資訊。 | **與 F2/F7 採納。** 由 events、pending、具 scope 的輸出意圖建立需求；ID／regex 不能證明敘事語意完整。 |

### 隔離的基準重現

使用暫存 SQLite、清空 provider／bot keys、fake provider。地圖 fixture：起點 A、西側 B、東側 C、樓上 D，以及沒有連線的命名房間 X。每個輸入都從 A 開始，不需要 LLM 或 RAG API。

| 輸入 | 實際保存的房間 | 正確行為 |
| --- | --- | --- |
| `不要往左` | B | 留在 A |
| `樓上有聲音嗎？` | D | 留在 A，回答問題 |
| `向右走，不要往左` | B | 提出 C 候選，再依條件裁定 |
| `我查看左邊的門` | B | 留在 A，觀察不等於進入 |
| `他說「往左走」` | B | 留在 A，引言不是動作授權 |
| `我去密室` | X | 不可直接傳送到不連通房間 |

六個 helper 重現中，caller 快照都仍顯示 A。另一個 probe 成功執行 `add_carried_item` 取得 `rope`，再模擬 provider 例外：DB 保留繩索、`inventory_change` event 仍在，但 `narrative_facts` 變成一般錯誤指示，最終一致性輸出也沒交代取得物品。只有一名角色的 log-only save，仍掃全 characters 表並 upsert 兩筆 mirror。這些是功能與 SQL 觀察，不是速度量測。

既有隔離基準測試：**940 passed、1 skipped、33 subtests**。既有測試通過，不代表已覆蓋上述新反例；實作時要轉成永久 regression tests。Probe 沒有讀寫正式遊戲資料。

## 3. 目標與不可破壞的條件

1. 保留自然語言、合法多段移動、NPC 互動與敘事品質；不增加常態移動核准、已讀確認或重新宣告。
2. 以目前 state、已提交結果為準。移動提議、查詢成功、pending check 都不等於玩家行動完成。
3. 不增加固定 classifier／planner／judge／repair LLM 階段。玩家統一走 Supervisor；已結算檢定及開場後備保留既有限定 Narrator；KP Assistant 獨立。
4. 新 adapter 必須保留 check／Luck owner、timeline、actor／subject、revision、來源版本及收件權限。
5. 不完整依據代表未知；保留原文補查、必要護甲／攻擊／能力／限制，以及多隻同種怪物的不同顯示名稱。
6. 不重播已提交操作、不重擲已對外的結果、不把私訊失敗降級成公開輸出。
7. 保留同團 gameplay 序列；之後純讀可取得回合進行中的最新已提交快照，但操作仍重驗。
8. Check、骰值、Luck 維持原可安全發布時點；量按鈕實際可結算時間，不只亮起時間。Typing 不算有效輸出。

不在本批：換模型／reasoning、啟用 dynamic tool scoping、重翻劇本、自動翻譯來源、雙模型競速、未驗證公開串流、微服務、多程序同團 ownership、批次改寫舊 OOC，或讓 Python 通用推測故事後果。

## 4. 流程與接口

### 目前一般玩家行動

```text
Discord -> router [conversation gate, 讀快照 S0]
  -> map helper [讀 S1 -> 解析 -> 保存位置]
  -> Supervisor(S0, resolved_location)
      -> build_context [Scenario || Memory]
      -> 分類 -> Executor -> tools [各自提交]
      -> 驗證裁決 -> Narrator
      -> Guard -> 防雷 -> 機制一致性改寫
      -> canonical commit [重讀最新 state]
  -> 公開回覆 -> DM/圖片 [best effort] -> maintenance
```

### 目標流程（分批導入，目前未實作）

```text
Transport(message/interaction ID, actor) -> 適用時先 defer
  -> 純文字 hint
  +-> 已確認純讀指令 -> ReadView [短 read tx] -> 收件權限投影
  `-> 原 priority/conversation admission -> 最新權威快照
       -> state-aware RouteDecision + correction/pending 驗證
       +-> KP Assistant -> 原獨立 agent/權限/commit
       +-> PLAYER_OOC -> 公開/自身投影 -> 一次回答 -> 非正典輸出
       +-> ACK/roleplay -> 足夠的最小 context -> 原 Narrator
       `-> RetrievalPlan -> 授權 evidence [Scenario || Memory]
            +-> ordinary: MovementProposal [不寫 state]
            |    -> Executor <-> gateway/tools [共用 admission；先到達再做依賴效果]
            |    -> 僅無剩餘到達依賴時可 final 提交（§12.1）
            |    -> 最新 state + 已驗證 resolution/requirements
            |    -> 普通 Narrator [無工具]
            +-> resolved: 權威 check + Luck 最終結果
            |    -> 共用 admission 驗證/提交此 check 綁定的移動
            |    -> 限定 Narrator <-> 允許的後續 tools
            `-> opening fallback: 限定 Narrator <-> 開場 tools
                    |
                    v
            核對實際 outcomes、pending、execution health
            -> Python DeliveryEnvelope [facts / narrative / controls / audience]
            -> 機制一致性 -> Guard -> 重驗改過的文字 -> 最終收件安全
            -> delivery contract [失敗：一次安全 fallback 再驗；仍失敗則 blocked]
            -> final commit [canon/outcome/outputs]
            -> 立即 claim 發送 -> transport -> receipts
                 `-> server RecoveryMode
                      +-> delivery_only：補送封存內容，零模型
                      +-> render_only：tools=[] 且 callback 拒絕工具
                      `-> reconcile_required：核對未決效果，不重跑整回合
```

檢定結果路徑的移動提交，必須在 Luck／最終結果驗證後、限定 Narrator 使用結果前。Narrator 若執行允許的後續工具，一樣收集 outcome 並走最終驗證／commit，不再加普通 Narrator。開場優先沿用既有抽出的開場白，只有原本 fallback 才用模型。

| 邊界 | 保留的現有入口 | 擴充／消費端 |
| --- | --- | --- |
| Transport | `discord_bot`、command callbacks | 可信 source-event identity、delivery result；不保存 Interaction 物件 |
| Routing | `commands/router.py`、`agents/intent_router.py` | Input hint + state-aware `RouteDecision`；ordinary／sudo 共用 |
| Context | `context_builder.build_context` | 純 base projection，再 `RetrievalPlan` enrichment；worker 輸入不可變 |
| Movement | `intent_parser`、`scene_map`、map handlers | `MovementProposal`／共用 service；取消 Supervisor 前提交 |
| Execution | `executor.run_executor`、gateway、`keeper._execute_tool` | `ToolExecutionContext`、observed outcomes；沿用 domain mutators |
| Admission | 所有 mutator／時間線切換入口 | 共用 `MutationAdmission`；hold owner、authority、evidence 與寫入前重驗 |
| Output contract | 既有 Narrator 回應、renderer／commit | 模型 mixed 分段候選 -> server `DeliveryEnvelope`；分開正典與收件投影 |
| Recovery mode | 既有 task ownership；後續 ledger／outbox | Server `RecoveryMode` 優先於 turn_kind，恢復敘事不得開工具 |
| Checks | check／Luck callbacks、`_finalize_check_result` | 原 identity／result + optional proposal/evidence refs |
| Resolution | `turn_resolution.validate_resolution` | 用 committed move event 驗證 proposal；disposition 與執行健康分開 |
| Narration | 普通／限定 `narrator`、`prompt_config` | 分階段規則、具 scope 的需求；不指示不可用工具 |
| Persistence | `_mutate_and_save_state`、`_save_state_unlocked`、`_commit_turn_result` | 同 connection 的 state/event/output hooks；commit 後更新 caller |
| Delivery | reply／DM／image callbacks、post-turn hook | 每個 logical output 唯一 claimant；沿用 receipts/corrections |
| Recovery | async task ownership、既有 markers | 即時 admission hold，後續加 durable reconciliation |
| Maintenance | 背景 summary／memory／checkpoints | 共用 mirror/commit、timeline/source 失效；不加前景摘要模型 |

## 5. 第一批：具體正確性與本地成本修正

### A. 路由與移動（F1/F4）

`RouteDecision` 區分 `speaker_role`、`message_mode`（IC/OOC/mixed）、`turn_kind`、輸出對象、正典政策及檢索計畫。明確 OOC／高信心規則提問可分流，不能只看標點。`（我往右走）` 仍是行動；`（為什麼要骰？）然後我往左走` 保持一個 request、分開 spans。未知語句在已會執行的 gameplay 呼叫內理解。Exact ACK 不自動花 Luck、骰定或接受購買。

PLAYER_OOC 第一版不給工具。公開回答只用公開 context，授權自身秘密用私人輸出。不能借 Keeper 廣義 read-only 工具（其中有骰子）或 KP Assistant 未過濾 state。OOC 不進 canonical log、summary、public memory；若存 OOC history，要有 conversation／timeline／recipient scope 及容量上限。不回溯刪舊 log。

混合訊息可在既有模型回應中回傳 IC/OOC 分欄，不新增一次 request。Renderer 維持合理的合併回覆，只有驗證過的 IC spans 進正典。原始輸入及 span 對照保存在具 scope 的 audit。分欄格式損壞時，不能將全部回覆升為正典、重播效果或新增固定 repair call；用已驗證完成事實及安全說明降級。

`MovementProposal` 是不可變值：proposal ID、timeline、actor/subject IDs、origin、destination candidate/path、assertion kind、原始 span、map/source version、evidence refs。Destination 可空，source span 是資料，不是指令。第一階段可用回合內 ID；restart-safe ID 隨 ledger 才提供，不能提早宣稱。

- 分別解析觀察、否定、提問、引言與行動子句，保留原句；不能以整句「含不」就全擋。
- Graph 只證明候選拓樸。目前匯入圖沒有完整權威 lock/trigger conditions；欄位缺少不代表可自由通過。房名／RAG 命中是候選，不是傳送授權。
- 無地圖劇本照常依劇本裁定，不為新介面硬造節點；只有來源／已成立遊戲事件支持時才記敘事位置。
- 已知合法多段路線不強迫逐房宣告；在真正的選擇、檢定及場景反應點停下。必要條件未知時仍交既有 Executor。
- 驗證目前 timeline、active character、origin、source version、動作授權及適用 check/Luck 最終結果後，只提交一次。別人的獨立 pending 不一律擋住本角色。
- 沒有剩餘到達依賴機制、且反應點條件／依據已核定時，Executor final 回應可攜帶移動 proposal decision（詳 §12.1）；未驗證前仍不可信。本地 commit 先產生 event，再驗證 completed resolution，最後給 Narrator。不執行損壞／incomplete JSON，不新增固定收尾請求。
- Commit 後更新完整 caller 快照。摘要等無關 revision 變動可重驗，不能覆蓋新 state 或再走一次。Sudo 保留實際 actor 與 subject。
- Final 回應後若移動驗證失敗，誠實標示 incomplete/blocked；修原 bug 所需額外補查要與一般延遲案例分開報告。

### B. 部分成功與最終輸出（F2-A/P8）

保留 `TurnResolution.disposition`。另加 execution health：`completed`、`partial`、`failed`、`recovery_required`，及 structured observations。Lifecycle 的 accepted/running 屬 turn persistence，不混入 final resolution。

`ToolExecutionContext` 保存可信 actor、subject、timeline、entry kind、已授權 check/decision identity。Adapter 移除模型提供的私有權限欄位再注入伺服器值。未知 target／quantity／action 仍是待 domain 驗證的模型輸入；合法跨角色工具必須維持可用。

`OperationOutcome` 包含 effect class（pure read/random/state/output intent）、已觀察成功／拒絕／未知、tool identity、verified events、pending changes、具 scope 的輸出意圖、failure code。第一批只是程序內 observations，不從 state delta 單獨推斷原因，不公開 raw errors/private results。

正常／例外共用 result builder。保留已觀察成功效果、既有 pending/Luck、骰值。Partial 不改成 PURE_ROLEPLAY，也不等於 complete。有預算時沿原本 Narrator 一次說清楚，否則輸出安全的已完成事實及剩餘事項。也必須改 incomplete consistency renderer，否則最終警告仍會洗掉修好的交接。

所有改文都在最後收件權限／防雷檢查之前。Guard 改文後，先以確定性邏輯重驗機制條件，再做最後收件檢查，不新增固定模型修復迴圈。現在 Supervisor 先防雷、之後才機制改寫；回歸測試要涵蓋 deterministic fallback 帶入 protected content 的情況。私人／圖片輸出也做收件人驗證。

取消在 worker 結果被妥善處理後繼續傳播。Mutating worker 超過 grace 仍未結案時，必須立即以程序內 hold 阻擋相關 gameplay，直到 reconcile；只排背景 marker 保存不能填上窗口。純讀仍可用。尚無 ledger 時不宣稱跨重啟可恢復。

### C. 精確鏡像寫入與 commit 語意（F6-A/P5）

抽出純 mirror projection，保留 owner／character-ID aliases、退休／手動角色卡及原順序。同一 write transaction 讀舊 group payload，得到 old/new 精確 keys，只讀這些 keys，將實際 serialized rows 與新預期值比較。只刪可證明歸屬的 stale keys，只 upsert 變動／缺失 rows；未變資料保持 `updated_at`。只比較 old/new projection 不足以修補缺失 mirror。

移除熱路徑全表掃描。歷史 orphan discovery 是明確的一次性 dry-run/repair，不每次 save 執行；歸屬不明保留並報告。Membership index 等 orphan 管理／查詢需求證明必要再做。禁止用未轉義的 prefix `LIKE` 當歸屬。

先在本地準備 revision/timeline 與 final serialized payload，序列化結果同時用於 bytes metrics；外層 transaction 成功後才同步 caller。必須涵蓋 maintenance 直接呼叫 `_save_state_unlocked`，不只普通 save。保留 revision conflict、group/mirror 原子提交與 newgame 清理。未知 mutating fields 仍走完整 before/after snapshot 驗證。

## 6. 第二批：減少重複工作，保留玩法內容

1. **先路由再 enrichment（F5）：** 純 hint＋最新 state 決定各 source skip/proactive/tool-only。保留 Scenario/Memory 並行及獨立來源狀態；只跳過可確認不必要的檢索，未知 gameplay 保留原政策。Context builder 變快卻增加 tool/model rounds 算失敗。
2. **Request 內 history/prompt 重用（P4）：** 同一 immutable history/model/tokenizer/budget/min-turn key，共用 admission/provider 的 selected history/counts。PR #94 已完成共用政策；不可用 mutable object identity 當 key，不漏工具／結果，也不假設分段 BPE 相加等於整份序列化。最終候選須精確核對；cache 有上限，避免為省 tokenization 卻反覆 hash 全歷史。
3. **Eligible legacy RAG 分組（P3）：** chapter/visibility 過濾後才分組，保留順序、去重與 budget markers。V4 必要片段 traversal/continuations 不動。未證明 hotspot 與排序等價前不改 NumPy/heap。
4. **版本化 evidence（P2）：** 保存 source/variant/record/span/visibility/actor/action/check references 與完整性需求，不保存整份舊 prompt。Follow-up 重新授權、取回來源、更新動態 state，補查新增分支。缺漏／部分／過期不算完整。PR #94 cursor 綁 query/history、只存程序內，是分頁識別，不是永久 check reference。
5. **Stage prompts（F7/P8）：** 先抽出文字等價的共用區塊，再移除普通 Narrator 不適用的工具指示／raw duplicates。`NarrationRequirements` 帶原 intent、verified disposition/health/events、各 actor pending、已提交場景、允許 evidence、private recipients。保留風格、日常小物政策及全部劇本機制；必要區塊放不下不能靜默消失。
6. **純讀路徑（F8-A）：** status/sheet/help/where handler 與 post hook 要先盤點 mutation、button claim、receipts 才能 allowlist。Group 與 turn status 在同一短 read tx 取得，離開 event loop，回獨立且過濾過的 snapshot，回合進行中用自然文字標示。保留 gameplay locks；bounded workers/ownership 避免遺棄寫入及前景飢餓。

各項獨立量測，不以一個開關同時換模型、歷史政策、工具清單與輸出長度。

## 7. 後續：持久化操作與輸出

### 操作持久化（F2-B）

使用可信 transport event identity，不用訊息文字。

| 記錄 | 穩定 keys／必要資料 |
| --- | --- |
| Turn | `turn_id`；unique `(platform, event_kind, source_event_id)`；conversation/timeline、actor/subject、lifecycle、resume policy |
| Operation | `operation_id`、turn、native call ID 加 response identity 或 dispatch ordinal、input digest、effect class、status、saved result |
| Committed event | event ID；unique `(operation_id, effect_index)`；affected IDs、payload、visibility/recipient、source/version |
| Output | output ID；unique `(turn_id, logical_key, part_index)`；immutable payload/hash、destination、stream 順序/dependencies、status/lease/receipt |

相同 operation ID 但不同 input 應拒絕；相同 input 的兩個不同 calls 仍是不同操作。Transport 重送只能恢復／查詢原 turn，不能再派第二次 Executor。沒有 transport ID 的 legacy internal caller 不能宣稱 transport 去重。

Dispatch 前持久化 operation identity。本地 mutation 用同 connection：驗 operation/timeline/authority -> 讀最新 state -> domain mutation -> group/mirror diff + operation outcome + events -> commit -> 回結果、同步 caller。Random outcome 對外前保存，重試讀回。盤點一個工具多次保存的情況（含 resolved-check event），合併 transaction 或用穩定 child operations，parent 保持 partial。Transaction 內不等網路。

私人／圖片意圖也要在 final narration 前持久化，填補「工具已提交、Narrator 未提交」的窗口。重啟核對 local prepared/committed，依已知 receipts 及獨立 RecoveryMode 只恢復無工具 render/delivery（詳 §12.2）；不盲重播不明外部操作。沿用既有 check、purchase、correction identities。

### 即時且可恢復的輸出（F3/F8-B）

Canonical text、final outcome、output drafts 共用短 commit；已穩定 pending 及 Python roll feedback 可在原安全時機先提交輸出。Enqueue 前固定分段、recipient、版本化 asset hashes；不把 Python callback、單一可變頁碼或短期 Interaction token 當永久目的地。

第一版維持 inline：commit -> 立即 claim -> send -> receipt。Bounded recovery worker 接手未送資料，polling 只補救。Sender/hook/worker 共用 logical-key claim，不能雙發。沿用 narrative receipts，讓 correction 的訊息 ID 仍有效。

狀態：`pending -> sending -> sent`；明確未送的暫時失敗 -> `retry_wait`；權限／資產問題 -> `blocked`；可能已被 HTTP 接收 -> `uncertain`；尚未送出的舊 timeline -> `cancelled`。用 lease-token compare-and-set 擋晚回 worker 覆寫新 claim。同 output key 的 payload hash 不同是錯誤。

後續再拆 required public、actor-private decision、supplemental 有序 streams，限制跨 stream concurrency。Barrier 只影響相依行動；無關玩家／團與純讀不等裝飾圖片，不增加已讀確認。Rollback 先停新 send admission、協調有限 in-flight、取消舊未送 rows。晚到且不確定的訊息要 receipts/compensation；DB 重驗無法和外部 HTTP 發送原子化。

Discord nonce 只處理近期訊息，不是永久 exactly-once；採用前核對安裝的 SDK 支援。參見 [Discord Create Message](https://docs.discord.com/developers/resources/message#create-message)。

### 遷移與回退

另建有版本的 relational migrations，不能把新表名塞入既有通用 `key/data` `_TABLES` loop。Repository 驗 identity、ownership、status transitions；若用 FK，要覆蓋刪除／retention 測試。既有 SQLite backup 要包含新表；asset retention 保留 pending output 需要的版本。使用 backup API，不單拷 WAL 模式的主 DB 檔（[SQLite WAL 文件](https://www.sqlite.org/wal.html)）。

Schema 啟用時暫停 gameplay/background writers；legacy mirror cleanup 先 dry-run；用暫存副本做還原測試。新 ledger 從切換點才啟用，不從舊敘事反推 operations。設定去重 tombstone 保存窗口；未結案 operations／引用資產不可 GC。

效能旗標可恢復較完整 prompts/retrieval 或較慢純讀，不可恢復提前移動、OOC 污染正典或不安全重播。Outbox 回退須安全 drain/pause、唯一 sender，不能立即切回另一套競爭的 legacy helper。部署仍假設每個 conversation 只有一個 gameplay owner。

## 8. 延後的完成邊界優化（P6/P7）

來源、收件者、數量、授權都明確時，可用既有 inventory 規則做原子交接。戰鬥子流程沿用既有 services，在所有 check/Luck/defense 選擇點停下；工具批次化不等於少模型請求。

`TurnBoundaryGuard` **不在本提案前幾批實作或啟用**。後續實驗需註冊完整工作流程、可信入口 ID、完整 input coverage、整批 provider calls 全數歸帳、committed/rejected receipts、目前 scope、完整必要依據、pending/output intentions，且沒有不明 worker。邊界為 continue Executor／yield to 原有玩家選擇／ready for Narrator／recovery required。

不能因一個成功 call、另一玩家的 pending、搜尋命中、模型 done 或 partial response 早停。整批 calls 都要處理，但 prerequisites 未定的不能盲執行。一般自由敘事保留原接續。用 typed local receipt，不偽造模型 JSON；三個 provider 共用權威驗證。

Shadow 只計算假想邊界，既有 Executor 照常繼續；預設不額外執行副作用或模型。後續還需搜尋、機制、私訊就是 premature-stop 反例。觀察不到差異不是故事完整性證明；特定流程啟用前另行審查。

## 9. 實作順序與驗收門檻

| 工作包 | 範圍 | 相依 | 完成門檻 |
| --- | --- | --- | --- |
| S0 | Baseline fixtures、request/interaction trace | 既有 observability | 正確任務基線及 bug 反例可重現 |
| S1 | F2-A/P8 partial facts、最終輸出契約、所有入口 worker hold（R3/R4） | S0 | 錯誤保留真實結果；成功路徑不加 LLM |
| S2 | F4/F1 mode、到達依賴、共用 commit、mixed 分段（R1/R5） | S0；整合 S1 outcomes | ordinary/sudo/no-map/check 都涵蓋；不加常態玩家操作 |
| S3 | F6-A 精確鏡像、commit 後 caller sync | S0 | 無全表掃描；log-only mirror writes=0；commit failure 測試 |
| S4 | P1/F5/P2 權威注入、路由、證據重用 | S1/S2 | 機制／保密相同；少重複 requests 或已證明本地收益 |
| S5 | P3/P4/F7 分組、history 重用、stage projection | S1；保留 PR #94 | 精確選取／排名及敘事品質驗證 |
| S6 | F8-A 純讀盤點、bounded I/O | S3、task ownership | 純讀不等模型；同團 mutation 序列 |
| S7 | F2-B ledger、transaction 整合 | S1/S3、全部 mutator 盤點 | commit/crash/idempotency 故障矩陣通過 |
| S8 | F3 即時 inline outbox | S7 | 不雙發；按鈕時機不退步；重啟只恢復輸出 |
| S9 | F8-B 獨立發送 streams/barriers | S8 | 順序、rollback race、慢 stream 隔離通過 |
| 後續 | 可選原子流程／early-boundary shadow | 另行核准、證據與測試 | 不隱藏 gameplay、保密、敘事退步 |

每包獨立 review，不一次把全部架構合成效能修補。S1、S3 是最小且能獨立產生價值的起點。本文件本身不代表已核准實作。

## 10. 測試與效能評估

以下是待建立／延伸的測試，不宣稱已通過；已有同契約測試就延伸，真正不同的行為才新建檔案。

| 領域 | 必要案例 | 延伸現有／新增測試 |
| --- | --- | --- |
| 移動 | 六個 probe、肯定右移／進入、無圖、合法多段路、鎖／trigger、雙 actor、sudo、stale map/timeline、成功骰但 Luck 未定 | 新 movement proposal/commit；unified turn、priority integration |
| OOC/mixed | 全半形／多行提問、動作括號、NPC 引言、混合一次完成、不提權、自身秘密與公開 context、格式錯誤 | 新 player OOC routing/visibility；KP Assistant、spoiler |
| Partial | Item/HP/check 成功後 provider error、Narrator fail、工具拒絕、private output、已知骰、worker 超 grace、不從 diff 猜原因 | `test_turn_consistency_handoff.py`、`test_narrator_check_consistency.py`、async provider contract |
| 最終安全 | 機制 fallback 帶入 protected name/text、Guard 改文、incomplete 保留可見效果 | Narrator consistency、spoiler policy |
| 持久化 | Log-only 零 mirror 寫入、alias 精確變更、缺 mirror 修復、他團前綴碰撞、退休/newgame、stale revision、outer commit fail、maintenance caller | `test_state_persistence.py`、state-loss；新 mirror diff |
| RAG/history | V3 分組全文／順序／scope 等價、v4 必要規則／cursor 不退步、長歷史選取一致、來源/check/actor 變動失效、保留原文補查 | `test_scenario_authoring.py`、scenario template/RAG、input budget |
| 純讀／併發 | Provider 等 Event 但 status 可讀 committed view、攔 handler 隱性 save、兩 mutation 序列、取消/shutdown、背景飽和 | Conversation/priority；新 read snapshot/worker lifecycle |
| Ledger | 同 operation 重試與不同 operation 相同 input、state/event 原子、commit 後回傳前 crash、骰保存、Narrator 前 private intent、native ID 碰撞 | 新 ledger atomicity/recovery；resolved-check/purchase |
| Delivery | 多段部分失敗、DM 拒絕不公開、已接收後 timeout、ack fail、stale lease/asset/timeline/button、無 polling 立即 claim、慢 stream 隔離 | Pending button/receipt/correction；新 outbox/delivery |
| 完成邊界 | 只有 search、別人的 pending、傷害未套用、必要 CON、多動作未完成、provider incomplete、不明 worker | 後續實驗涵蓋補充文件 C01–C20；前幾批不 early stop |

Integration 在 import `app.db` 前設定暫存 DB/data/import/backup，以 fake transport/provider、固定骰及 Event/fake clock 測試。Python 取消／ownership 按部署 runtime 核對，保留 task reference 並觀察／核對結果（[Python asyncio 文件](https://docs.python.org/3/library/asyncio-task.html)）。故障注入不碰正式環境。

效能報告分 **local CPU/DB、retrieval/embedding、provider 真實 attempts/continuations、admission/retry、queue、delivery**。納入 input/output/cached tokens、mirror 讀寫、完整回合成功率、機制／工具準確度、事實遺漏／隱私、fallback/repair/clarification、同一任務的玩家操作數。量 defer、首個有用結果、button visible/effective、骰值回覆及完整必要敘事。

同 scenario/state/input/model/reasoning，一次改一項；分 cold/warm、單團／多團，真實 API 交錯跑 baseline/variant，減少時段影響。失敗／timeout 不剔除。舊 API 報告只是背景，不能冒充本次基線。Mirror 用 1/10/100/1000 個合成群組、固定目標群組大小，比 SQL／examined rows 及重複量測，不憑空承諾速度。

未來 live evaluation 前要指定案例數、模型、request/費用上限、統計方法。本次 spec 任務未授權也未執行真實 API。按 baseline 噪音預先訂容忍範圍；信賴區間無法判斷就是不確定。正常成功路徑不增加固定 LLM 階段與玩家決策；修正原 bug 新增的必要工作要單獨解釋，不能混在一般效能比較內。

## 11. 設計審查決策與既有契約

建議先核准 S0–S3，再量測、審查 S4–S6；S7–S9 保留為分批恢復架構。Early stop 維持關閉。S0–S3 不需要先決定 membership 表、NumPy、換模型或新部署服務。

S7/S8 實作前，需定案各入口 transport identity、durable payload retention、最長 recovery age、blocked/uncertain delivery 的操作介面及 SDK 能力。不確定的私人發送預設走核對，不公開、不重播 gameplay。每包列未覆蓋範圍，不能把整份提案標成完成。

相關契約：[統一回合](unified_keeper_turn_flow_design_spec_zh.md)、[裁決交接](../bug/log_backed_turn_consistency_design_spec_zh.md)、[敘事邊界](../enhancement/narrative_boundaries_design_spec_zh.md)、[原子購買](../bug/purchase_turn_provenance_design_spec_zh.md)、[Token 准入](../enhancement/token_admission_evaluation_design_spec_zh.md)、[外部整備／PR #94](../enhancement/external_template_authoring_design_spec_zh.md)、[狀態保存](../feature/state_persistence_design_spec_zh.md)。Dynamic tools 與 macro combat 仍是獨立 backlog。

### 程式碼導覽

以上查核位置：[router](../../../app/commands/router.py)、[移動 parser](../../../app/intent_parser.py)、[legacy adapters](../../../app/legacy_commands.py)、[Supervisor](../../../app/agents/supervisor.py)、[Executor](../../../app/agents/executor.py)、[tool gateway](../../../app/agents/tool_gateway.py)、[intent router](../../../app/agents/intent_router.py)、[Narrator](../../../app/agents/narrator.py)、[resolution](../../../app/services/turn_resolution.py)、[prompt policy](../../../app/services/prompt_config.py)、[group repository](../../../app/repositories/group_state.py)、[DB](../../../app/db.py)、[Keeper services](../../../app/keeper.py)、[RAG](../../../app/scenario_rag.py)、[v4 retrieval](../../../app/scenario_retrieval.py)、[history selection](../../../app/services/input_budget.py)、[embedding cache](../../../app/embedding_cache.py)。連結指向工作分支程式；前述 baseline commit 固定本次審查的歷史意義。

## 12. 2026-09-27 Review 補強決策（R1–R6）

已閱讀 `turn_safety_and_latency_spec_review.md`（SHA-256：`22e00d613e144f40da08e0857932d8fd947f749b87cfbcaf98c6f62ca8baa6b4`），並對照最新 `main_v2` `e03d5dc`。原審查固定的規格版本為 `8de40eb`；本節是後續設計修訂，仍待核准，沒有新增 runtime 實作或宣稱 SR 測試通過。#95／#96 修改整備檔案與匯入辨識，未更改本節核對的 Supervisor／Narrator／gateway／spoiler 核心。原先 940 測試為歷史基線；匯入修正的 987 測試也不能冒充本 refactor 的新驗收。

| Review | 決定 | 必須完成的工作包 |
| --- | --- | --- |
| R1 移動因果與證據入口 | 採納；限制 final-response commit，新增到達依賴契約 | S2 |
| R2 恢復能力 | 採納；RecoveryMode 與 turn_kind 分開，render-only 雙層禁用工具 | S7/S8；S1 不自動重啟帶工具模型 |
| R3 共用 mutation admission | 採納；hold ownership 與 effect class 是執行政策 | S1，S6/S7 延伸 |
| R4 最終輸出仍可操作 | 採納；server delivery envelope 與有限 fallback | S1 |
| R5 混合 IC/OOC | 調整後採納；模型候選與 server 投影使用不同型別 | S2 |
| R6 逐入口請求帳本 | 採納；結構 trace 與真實模型分布分開驗收 | S0，後續各包 |

本節具體化第 4–10 節；若原簡圖可能被解讀為「任何移動都在所有工具之後」或「恢復一律呼叫原 Narrator」，以以下限制為準。原則、正常遊戲能力與 early-stop 關閉政策不變。

### 12.1 R1：先有有效到達，才可執行依賴到達的效果

「進書房，拿桌上的信」必須先驗證並提交到達，才能取得室內信件；「吃手上的乾糧，再試著進書房」則允許先保留已完成的吃乾糧效果。不能將全部工具按名稱排序，也不能因最後移動失敗而回滾所有先前合法效果。

- 到達依賴須引用 server 核定的當前位置或同一 action 的 committed arrival event。`MovementProposal`、RAG 命中、模型的 `requires_arrival=false` 都不是授權。依賴適用於物品、旗標、線索、檢定、資源及輸出意圖；只要求與該效果實際相關的到達，不把既有背包操作一律綁到新位置。
- 需要到達後繼續機制時，移動在既有 Executor 的工具序列內經共用 service 提交，更新完整 state，再執行依賴效果。所需 check/Luck 未定就停在原有玩家選擇點。保留原本必要的模型接續，不增設固定的「確認已移動」請求。
- Final-response movement commit 僅適用於**沒有未執行的到達依賴機制**，且目前合法反應點所需依據與條件已核定的情況；之後可直接進原 Narrator。若發現尚有必要工具，不得把這條捷徑當 early stop，也不能叫無工具 Narrator 補做。
- 共用 `MutationAdmission` 覆蓋 ordinary、sudo、明確 map 指令、final-response service、resolved-check follow-up。檢查 authority、timeline、actor/subject、origin/source、correction/recovery hold 及本動作 required-evidence completeness。不能只在 gateway 外層檢查，也不能用「版本相同」取代「依據已完整」。
- 移動續接綁 `action_id`、proposal/check/decision IDs、actor/subject、origin/path/source version 與核准結果條件。偵查成功不等於開鎖成功；Luck 未定不是 final result。既有骰值不能重擲；來源／路徑改變須重新裁定必要部分。
- 第一版要列出會受位置約束的 domain mutators 及其資料來源。模型提供的 dependency metadata 只作候選；對無法判定的劇情前置條件，沿既有檢索／裁定或保留未完成，不能聲稱 Python 已能理解所有自由敘事。無地圖劇本照常使用有來源支持的位置，不強迫建圖。
- 每次位置／arrival event 與相應 effect 各有明確 transaction 邊界，不跨 LLM 等待持有 DB transaction。

```text
「進書房，再拿信」
  -> MovementProposal（不改位置）
  -> 既有 Executor：查所需條件
       +-> check / Luck 未定：保存等待，不拿信
       `-> 共用 admission -> 移動 + arrival event 提交
             -> 更新 state -> 驗證拿信的到達依賴 -> item event
  -> resolution -> 原 Narrator

「移動後只需敘事」
  -> Executor final 候選 -> 同一 admission / evidence 驗證
  -> move + arrival event -> resolution -> 原 Narrator
```

驗收沿用 review 的 **SR-M01–M07**：鎖門不拿信、final 入口不能繞過依據、move event 先於 item event、乾糧效果保留、Luck 等待、偵查不能授權穿門、無地圖合法移動不增加例行確認。

### 12.2 R2：恢復模式獨立於原回合類型

| 模式 | 模型／工具能力 | 行為 |
| --- | --- | --- |
| 正常 ordinary Narrator | 原有無工具敘事 | 不受恢復限制影響 |
| 正常 resolved/opening Narrator | 原有受限工具 | 合法後續照常處理 |
| `delivery_only` | 0 生成請求、0 遊戲工具 | claim 並補送已封存 payload |
| `render_only` | 必要時一次既有敘事階段，強制 `tools=[]`；callback 同時拒絕全部遊戲工具 | 只根據已驗證且有收件權限的 events/receipts 敘事 |
| `reconcile_required` | 不重新派整個 Executor／帶工具 Narrator | 先核對哪些效果完成、未執行或不確定 |

`RecoveryMode` 由 server 決定，優先於 `turn_kind`，不能因原回合是 resolved check 再啟用傷害或 advance 工具。只在 ledger 已有可信 action/step identity、缺失步驟明確且仍被授權的固定流程內，才允許有針對性的恢復。一般自由敘事沒有此證明時保留 partial，告知已完成與未決部分。

Operation retry 讀回原結果。重新生成的 call ID 不能自行認領已完成 logical step；也不能用 tool name + input hash 全局去重，否則兩次合法同參數攻擊會被吞掉。固定流程的 step identity 要區分合法重複效果。第一批程序內 observations 不提供跨重啟自動恢復承諾。

驗收 **SR-R01–R06**：已扣血／advance 不再執行、必要步驟缺漏仍 partial、有封存輸出補送不呼叫模型、正常 follow-up 工具維持、不同 call ID 不重做同一已完成 logical step。

### 12.3 R3：所有變更入口共用 hold，且只有擁有者可解除

`MutationAdmission` 的消費端清單必須列出 Executor、restricted Narrator、check/Luck callbacks、sudo、購買確認、map、角色切換，以及 newgame/rollback。S1 完成前需逐入口標註實際呼叫點與測試；不能只測 gateway。

Hold 至少帶 conversation、timeline、受影響 scope、task/operation identity、generation/owner token。取消路徑須在釋放相關 gameplay admission 前使 hold 可見；在寫入 transaction 或緊鄰權威鎖邊界重新檢查。只有相同 owner/generation 的確認完成、拒絕或 reconciliation 才能解除；舊 task 的 finally 不得清除新 hold。結果不明不能憑逾時解除。

第一版 scope 無法可靠切分時，保守 hold 同團 mutation；已核對的純讀及其他團仍可執行。Timeline 切換必須等待舊 worker 安全結案，或在實際寫入前核對其原始 expected timeline；禁止舊 worker 重新讀新 timeline 後套用舊效果。維護／背景 writer 也須受 timeline、revision 與提交所有權約束。

| Effect class | 執行政策 |
| --- | --- |
| Pure read | 僅獨立快照，無共享 state 刷新；可停止等待，但持有並觀測 task |
| Random outcome | 已定結果必須保留；未知結果核對，不重新擲骰假裝同操作 |
| State mutation | commit 未知時保留 ownership/hold，不放行衝突寫入 |
| Output intent | 穩定 logical key 與收件人；重用已有意圖，不因取消丟失或重建關鍵輸出 |

多效果工具必須套用所有相關約束；有 shared-state refresh 或隨機結果就不能僅因位於 `READ_ONLY_TOOL_NAMES` 而走 pure-read 取消／重試政策。裝飾圖的慢發送不構成無限全團 hold，S8/S9 另以 output dependency/barrier 管理。

以 Event 阻塞 worker，逐入口嘗試衝突變更；驗證純讀可回應、其他團可行動、舊 owner 不能解除新 hold、已知骰不重擲、shutdown 不假稱 worker 已停止。明確區分程序內安全與 S7 之後的持久化恢復。

### 12.4 R4：最終安全檢查後仍保留合法結果與有效操作

由 Python 建立 `DeliveryEnvelope`：`output_id`、audience/recipient、已允許的 fact/event refs、narrative、mechanical feedback、interaction refs 及 canonical policy。即使 S1 尚未改成結構化模型回覆，也可把既有文字包進 server envelope；不為此改所有 provider 或再叫一次模型。

```text
候選敘事 + server 投影的 facts / controls
  -> 機制一致性 -> 原有條件式 Guard -> 最後收件／防雷
  -> validate_delivery_contract（只核對，不改文）
       +-> 通過：保存／發送
       `-> 失敗：一次 deterministic、已投影 facts / controls fallback
             -> 相同安全及契約檢查
                  +-> 通過：保存／發送
                  `-> 仍失敗：標記 blocked，保留既有 pending／結果，走安全通知／核對
```

契約檢查核對本次應交付且對此收件人可見的硬資訊和 controls；不以文字關鍵字證明全部敘事語意。按鈕繼續使用原 check/decision ID、owner、timeline，不重建 pending，也不重複發第二組操作。私人 pending 由私人輸出滿足，不為公開訊息的完整性暴露秘密。所有 fallback 均經同一安全檢查，無固定 LLM repair、無無上限改寫；原敘事通過時保持風格。S1 記錄 delivery contract 結果，S8 才保證 outbox 持久化補送。

驗收包括秘密片段與合法 pending 同時存在、private pending、Guard 改文、fallback 仍不安全，以及無錯誤的敘事維持原樣。不能把「未洩漏但玩家已無法操作」視為成功。

### 12.5 R5：模型候選分段與 server 輸出投影分開

不讓同一個模型可填 schema 同時包含可寫 `server_output_projection`。採用兩個責任明確的型別：

```text
ModelReplySegments（僅 mixed 模式；模型候選）
  schema_version
  segments[]: text, source_request_span_refs, proposed_mode, proposed_event_refs

DeliveryEnvelope（Python 核定；模型不可寫）
  request_id / turn_id / output_id
  audience / recipient
  verified facts / interaction refs
  final text / canonical policy
```

混合回覆由**既有排定的最終 Narrator 回應**產生分段；Executor 保留機制／resolution 職責，沒有第二個 OOC Agent。普通模式維持原字串介面，由 server adapter 包裝。tool-enabled follow-up 的最終回應如需混合內容也沿同一既有回應，不新加敘事階段。

Speaker role、工具能力、context 可見性、收件人及 canonical policy 都由 server 決定。輸入 OOC assertions 在 context／行動交接時就不能當成已成立事實；不能等到 final commit 才處理「我早就有鑰匙」。輸入／輸出 spans、event refs 都須驗存在、範圍、適用與權限，沒有證據時不得把整段升為正典；這些檢查仍不宣稱可完整證明自然語言的語意。

Canonical projection 與 delivery projection 分別計算。一則 request 可產生公開 IC 和僅自身可見的 OOC 回覆；不強迫玩家拆句，也不強迫所有輸出併成公開文字。Context 先依權限過濾；不能只靠模型自行分欄防止洩密。

Malformed／覆蓋不明時，沿 R4 保留已驗證效果與安全未決說明，不把整份原文或模型回覆直接寫入 canon、不重播工具、不加固定 JSON repair。確實無法裁定的歧義才要求釐清，並納入品質／玩家操作成本，不能一律回問以逃避解析。

驗收：同訊息問規則＋移動、OOC 宣稱持有鑰匙、私人 OOC＋公開行動、模型偽造 role/recipient/canonical、損壞或漏段回覆。必須檢查輸入紀錄、assistant log、summary、memory 各自的正典投影，不能只測畫面分段。

### 12.6 R6：每個入口都有請求與體驗帳本

S0 為每個固定 fixture 記錄 route、input/state/scenario/model/reasoning、generation requests、provider attempts/retries、Executor／Narrator tool-response rounds、embedding/search、queue、Guard/repair/wrap-up、有效按鈕、骰值與完整必要敘事時間，以及玩家必需決策數。

下表是基本形態，**不是固定上限或可刪工作清單**。原有條件式 Guard、retry、wrap-up 另列並計入總量；特殊 gameplay 所需請求按 fixture 的正確完整行為核定。

| 路徑 | 基本生成形態 | 約束 |
| --- | --- | --- |
| 已核定純讀 status/sheet | 0 | 不引入模型 |
| ACK／純角色扮演 | 1 Narrator | 保留合宜回覆 |
| gameplay 無工具 | 1 Executor + 1 Narrator | 不新增固定分類／審核 |
| gameplay 有工具 | b+1 Executor + 1 Narrator | b 為工具回應輪數；不能無故多一輪確認 |
| resolved-check／Luck | k+1 限定 Narrator | k 為後續工具回應輪數；不再串第二套 Agent |
| opening fallback | k+1 限定 Narrator | 有現成開場白時仍直接沿用 |
| delivery_only | 0 | 補送已封存內容，不重新生成 |

Mixed/OOC 以既有實際路徑及新的正確性需求建立專屬 fixture，不能憑新 route 名稱宣稱省了請求。Fake provider 驗證 deterministic trace；live API 比較按 route 的分布、續接／fallback／repair 率及預先訂好的非劣界線，不能保證每次模型選擇完全相同。

報告按 route 列樣本數、success/partial/timeout/fallback、generation/attempts、足量樣本下的 p50/p95 與信賴區間。修正 bug 必須增加的工作單列原錯誤與正確流程、原因及玩家成本，不能把正常退步都稱為 bug 修復。失敗、不完整敘事、人工處理和按鈕有效時間都計入。未有量測前不承諾秒數或百分比，也未授權此輪呼叫真實 API。

### 12.7 更新的工作順序與討論重點

先 S0；S1 要完整包含 R3/R4，S3 可獨立處理。S2 必須先落定 R1/R5 的移動依賴與分段提交契約。S4–S6 再逐項量測；S7/S8 依 R2 能力矩陣實作持久化恢復，S9 再拆發送順序。Early-stop 仍關閉。

建議第一個可 review 的實作範圍為 S0 基線與 S1 的部分成功／安全輸出；hold 的所有入口驗收不可因拆 PR 就省略。若拆分 S1，應明確標示哪些只是 partial facts，哪些已完成 mutation admission，不能先宣稱整個 S1 完成。後續再接 S3 與 S2，避免一次混合所有 schema 與行為改造。


## 13. S3 實作證據（2026-09-27）

分支：`refactor/state-mirror-s3`。S0／S1 在另一分支；S2 等 S0／S1。此次核准不包含 S4–S9 或提早終止 Executor。

`character_mirror_projection` 從序列化的團狀態取得 owner／character 精確別名，保留退役、手動角色及既有順序。交易只以分批 `IN` 查詢舊／新鍵值聯集，與實際資料比對，因此角色未改但鏡像遺失時仍會修補。未改的列保留 `updated_at`。撞到其他團擁有的鍵值會拒絕整筆交易；無法證明歸屬的歷史孤兒保留，交由獨立校對遷移，不靠前綴刪除。

`StateCommit` 在外層交易成功提交後才同步 revision／timeline 並記成功紀錄。一般存檔、記憶維護、checkpoint rollback 都共用此路徑，團資料、鏡像、回溯前備份保持原子性。狀態大小量測重用寫入時的序列化結果，移除重複序列化整團的工作。未修改 schema，也未執行正式資料遷移。

```text
狀態鎖 -> BEGIN IMMEDIATE -> 舊團狀態與新舊精確鍵值
  -> 查對應鏡像 -> 寫變更/遺失列；刪已證明過期的列
  -> 寫團狀態 -> 外層 COMMIT -> StateCommit 同步呼叫端
       失敗：回滾；呼叫端 revision/timeline 不變
```

驗證：隔離資料完整測試 **994 passed、1 skipped、33 subtests**；Ruff 通過；mypy **83 檔通過**。新增提交失敗、鏡像寫入失敗與原子性、遺失別名修補、時間戳不變、退役別名、鍵值碰撞及有界查詢測試。原有維護與回溯測試持續執行。

`python3 scripts/benchmark_state_mirrors.py` 可重做隔離合成量測：每種規模 20 次、目標團固定大小且只改紀錄。1／10／100／1000 團時，均僅讀 **2 列鏡像、寫 0 列、刪 0 列**；本機交易中位數為 **0.564／0.575／0.584／0.475 ms**。這不是 API 或完整遊戲回合耗時保證。
