# Semantic topology discovery 與 deterministic publication proof

狀態：提案；runtime 實作待明確確認。分支 enhancement/pdf-multicolumn-ingestion／PR #155。取代 pdf_haunting_source_topology_validation.md 中尚未核准的 narrow-grammar proposal。

## 問題與信任邊界
現有 source-topology-v2 只識別固定肯定 assertion。Grammar miss 不等於否定來源，也不是 source unsupported 的證據。Legacy Haunting physical p10–12 提示：外側木板 → 可探索 wall space → 內側牆 → Room 4；但有多欄交錯、缺 current PR155 quality provenance、無 certified inventory，只能 discovery，不具 topology authority。本輪不重建／reimport 頁面，也不重跑 map-image provider。

拆成 import-only 三層：寬鬆 candidate discovery；嚴格 deterministic evidence binding；嚴格 certification/publication。保留 regex fast path；一個 assertion 命中不能阻止其他未命中窗口的 discovery。Gameplay、certificate verification、library read、移動均不呼叫 discovery/model。

## 1. 有界 candidate discovery
新增聚焦 app/pdf_source_topology_discovery.py，不造 generic plugin framework。沿用 analysis_provider registry 的 structured text-only analyze_text，不呼叫 image/vision、不新增 provider routing。文件是 untrusted data，不是指令。Model 只提出可能 source assertion；不得建立 room/edge/barrier state，或發明位置、breakability、方向、difficulty、HP/armor、hidden status、progression necessity。Confidence 只供診斷。

Coarse lexical cues 與已知位置 context 只選窗口，不作 acceptance gate。預設：每 window 最多 3 個連續 physical pages、12,000 Unicode chars；每 import 最多 4 windows／requests；每 window 最多 8 candidates。不拼接非相鄰頁，不刪除中間文字製造 continuity。無法放入連續窗口的 oversized evidence 保持 incomplete。Import 內相同窗口不重送。Config 可關閉或降低有限 budget，不能授權較弱 proof。透過 provider-local text options 設 timeout、zero retries，不改既有 caller 預設。source_topology_discovery_requests 與 OCR／Docling／map-image／AI repair 計數分開。

Candidate 使用 closed candidate/claim types、candidate-local barrier/location IDs、有序 proposed segments、source kind；逐 assertion 引用 start、各 barrier、enterable intermediate、destination、ordering/continuity、optional condition。每個 reference 包含 physical page、full-source 絕對 Unicode offsets、exact quote/reference。Model 回傳 hash、label、confidence、evidence class 都不可信，binder 自行計算／判定。未知欄位、invalid／oversized structure fail closed；不得截短 chain 隱藏後續 barrier。

## 2. Deterministic evidence binding
Pure public bind_candidate seam 接受 candidate、source context、certified inventory。檢查 exact span/quote、page-marker 歸屬、local source hash、canonical identity、bounded adjacency、source provenance。Hash 只能證明文字存在，不能證明語意支持連線。每個 assertion 還須通過 role-specific deterministic proof：location reference、barrier/action、enterable-space consequence、next barrier identity、destination consequence、explicit order/continuity、optional condition。可支持跨句／頁的明確 narrative clause；不信任模型指定的 role/type/paraphrase/confidence。

Evidence class：EXPLICIT、SUPPORTED、AMBIGUOUS、UNSUPPORTED。初版 certification 只接受 EXPLICIT；SUPPORTED 保留給另行記錄、既有標準允許的 deterministic rule，不能由模型 judgement 授權。未識別 paraphrase 留在 discovered candidate，標 EVIDENCE_INCOMPLETE／AMBIGUOUS，不標 SOURCE_UNSUPPORTED。Discovery 刻意比可安全認證的 proof rules 寬。

初版 proof-rule families：(1) 明確 barrier removal/break condition 的 consequence 揭露／到達可進入空間，再以唯一 inner-barrier reference 及其 consequence 綁定具名 destination；(2) 連續明確 barrier-crossing clauses，每段指定到達的位置。既有 full-assertion grammar 保留為另一 proof recipe。Recognizer 檢查原始 cited text 及 bounded source context，不把 LLM 改寫後的 normalized sentence 當 evidence。Synonym 可先 discovery，即使沒有 proof recipe；recipe coverage 不足保持 incomplete。每一 action-to-result relation 與同一中間結構都須有 evidence，不用 generic bag of words。

Ordering 必須有同一結構／位置的明確 causal/continuation evidence，不只 increasing offsets、wall token 重複或距離近。跨頁代名詞須有唯一已驗證 antecedent；多個可能 space/barrier 或 interleaved ambiguity fail closed。每道 barrier 有獨立 evidence/identity。具名 start/intermediate/end 必須唯一綁既有 inventory，包括 source-supported floor/section disambiguation。缺 room identity 為 ENDPOINT_UNRESOLVED，不建立故事房間。Unnamed source_transit 須明確 enterable、沒有正典名稱、穩定 evidence hash、kind=source_transit、player_label=""。逐 barrier progression／movement／discovery／fail-forward 保持；fail_forward 必須有自己的 explicit source proof，不推測 difficulty/tool/HP/armor/retry restriction。

## 3. Canonical authority 與 certification
Source context 區分 legacy discovery text 和 validated final canonical source。Canonical eligibility 由 deterministic factory 讀實際 final rendered pages、current PR155 quality/provenance、extraction identity 推導；不能是 model 欄位、僅 manifest content_hash 相符，或 caller trusted=True。檢查 selected page text/hash、physical page coverage/order、full rendered source hash、accepted source disposition、無 source-blocking reasons。Importer 僅在既有 final-source gate 建 context；library/cache verification 以 stored ingestion provenance、exact scenario.txt/PDF identity 建等價 context。Legacy 缺 provenance，即使 candidate 清楚，仍 CANONICAL_SOURCE_UNAVAILABLE／AWAITING_CANONICAL_SOURCE。

只有 canonical eligibility、全部 role proofs、唯一 endpoint、完整有序 segments、certified visual graph 均通過才 certify。現有 source certificate 加 canonical-quality identity、accepted binding/proof hash、assertion evidence/order hashes。Visual Phase1/Phase2、reading-order、OCR、source-publication 規則保持；source overlay 不能把 unverified visual map 升成 certified。

Accepted proof 與 raw proposal 分開保存在 private ingestion provenance。Verification 重播 canonical eligibility、deterministic proof/builder，不呼叫模型；只重跑舊 grammar 不足以驗證 semantic-discovered route。Span/hash/order/barrier count/quality identity 改變，certificate 與 graph-bound runtime receipt 失效。重用獨立 visual proof 時移除 source proposal/overlay，待 final book source 再 bind。Visual-only certificate 保持既有契約；舊 source overlay 缺新 proof 則 fail closed。

Source topology/evidence binding version 須升級（擬 source-topology-v3）。Extraction identity 加 discovery policy/version/config/provider-model identity、binding/canonical-quality versions。依既有 resume/cache contract 判斷是否 multicolumn-v8 → v9，並明確測試；不機械式改 visual certificate version。

## Diagnostics 與 publication
Private closed states：DISCOVERY_NONE、DISCOVERY_CANDIDATE_FOUND、EVIDENCE_INCOMPLETE、EVIDENCE_AMBIGUOUS、CANONICAL_SOURCE_UNAVAILABLE、ENDPOINT_UNRESOLVED、ORDERING_UNRESOLVED、CERTIFIED，另存 provider/budget/invalid-reference reasons。AWAITING_CANONICAL_SOURCE 表示等待 validated reconstruction。SOURCE_UNSUPPORTED 須有實際 deterministic contradiction/unsupported claim，不能由 grammar miss 得出。DISCOVERY_MISSED_SUPPORTED_SOURCE 僅在 independently verified canonical reference 明確證明漏掉 route 時作 evaluation diagnostic；模型不能自己宣告，empty discovery 也不能推導。

未認證 proposal 不改 visual graph、ordinary exits、source text、world state。Private audit 保存完整候選，public 僅 sanitized counts/status/hash。Report sanitization 須涵蓋新 book-level／row-level discovery 欄位及 map_analysis，避免 copied public report 洩漏 private windows／quotes／candidates。Topology/map failure 保持 derived warning／READY_WITH_WARNINGS；canonical source 獨立不安全仍遵守原 hard block。Authoritative boundary 維持既有 graph quarantine。Repository/public reports 不保存原劇本文字或 raw model output。

## 整合範圍與測試
預計修改：新聚焦 discovery/binding module；pdf_source_topology.py builder/proof seam；pdf_map_analysis.py certificate replay；pdf_loader.py final-source context／import／identity；scenario_library.py provenance／read verify；config.py/.env.example 有限 discovery controls；必要時 provider-local analyze_text timeout/retry options。不改 Keeper／combat／RAG／tool routing、OCR/PaddleOCR、Docling、two-phase visual extraction、production difficulty，不重設 map-state framework。

已議定 TDD seams：candidate discovery、pure binding/canonical-eligibility factory、certificate/library/cache replay、import orchestration、既有 route/movement authority。測跨句／相鄰頁／不同措辭 high recall；provider unavailable/error/timeout/budget/oversized output；offset/source identity；quote/hash 正確但 role 互換；高 confidence 錯誤／低 confidence 正確 proof；provenance 缺失／不安全；immutable source；發明 endpoint/name/hidden/difficulty；continuity/order；兩道獨立 barrier、逐 barrier retry/failure、逐層 discovery；read/runtime 零 model calls。

Negative controls：不相關相鄰 wall/boards、shared wall、單一 barrier 不補第二段、nonenterable/ambiguous cavity、非相鄰頁連線、多 antecedents、否定／conditional-event confusion、source/hash/span/order tampering、flattening。保持 single-barrier／secret-door／Beacon visible topology。Legacy Haunting 10–12 可提出 candidate，但另行 revalidate 前保持 AWAITING_CANONICAL_SOURCE；synthetic canonical/visual fixture 不得報為真實 certification。執行 pytest、ruff check .、mypy app、compileall app tests、git diff --check，再獨立 Standards/Spec review。

## 待補輸入
使用者最新訊息停在第一個 negative control 範例中途。後續補充約束須在 dependent behavior 實作前納入。本設計審閱 checkpoint 本身不授權 model/provider validation run。
