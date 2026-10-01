# PR155 匯入階段 PaddleOCR recovery

[English](pdf_ocr_production_integration_design_spec.md)

狀態：使用者已透過 `implement` 授權實作；local runtime 已整合，最終驗證／review 進行中。
分支：`enhancement/pdf-multicolumn-ingestion`，PR #155，目標 `main_v2`。
基準：`ead28a4ac4ab7a7b7f9829cd2e2462fc3b2c4ef6`。
本規格擴充現有多欄 ingestion 規格，保留 reading order、map 與 durable draft contract。

## 目標與證據邊界

Import quality first, gameplay runtime simple。只對 suspect region 與真正 scanned／低文字頁使用 `PP-OCRv5_mobile_rec` 作第一個 local candidate；保留 Tesseract、MarkItDown OCR、bounded AI recovery。source evidence 與 deterministic rules 決定 publication，confidence 只能作 diagnostics。

獨立 benchmark 已保存於 `enhancement/pdf-ocr-benchmark` commit `9f48775`：13 個真實頁、12 個 verified regions。英文 mobile 與 server 都是 numeric 98/103，Tesseract 50/103；dice 6/6、skill 4/4；raw pair 34/50 對 22/50；實際 bbox table pairing 16/16 對 1/16。這是局部 pilot，不能證明全域安全。此次選定的多語 `PP-OCRv5_mobile_rec` 尚未在該次測試評估，必須另過 release gate，不得將英文模型結果改名為多語模型證據。

## 現有 seam 與 routing

`_repair_local_regions()` 依 replacement glyph／unresolved native pairs 選 block，保留兩點 padding、300 DPI、region budget、唯一原 block replacement 與 `accept_region()`。該 gate 只修 observable corruption，不因加入新 engine 就放寬乾淨 ambiguous table。沿用 intact words、numeric multiset、resolved pair checks，另補完整 dice（含 `1d10+DB`）及百分比保存 guard；不改 crop、DPI、threshold 或重造 numeric geometry。

Reading order 維持 PyMuPDF geometry → deterministic layout → difficult native page 的 Docling challenger → 原 block ID validated permutation。保持 `do_ocr=False`、`do_table_structure=False`、remote services disabled；不得以 Docling prose 替換原文。

Damaged／numeric suspect region：相同 crop → Paddle candidate／gate → rejected、empty、unavailable、error 時 Tesseract／同一 gate → 原有 AI region repair → unresolved。clean pairing ambiguity 沒有足夠證據就保留，不靠 confidence 猜值。

Scanned／極低文字 graphic page：local Paddle／Tesseract → existing difficult transcription fallback／deterministic arbitration。保留 native wording、numeric、pair、dice evidence；沒有獨立 critical numeric evidence 的 OCR 必須標為 unverified／review。雙 engine agreement 不等於 source authority，也不能把 OCR 自己從 pixels 猜出的值当成 independent validation。

Local failure 保留 MarkItDown OCR 與 `pdf_ai_repair` 的 page／region eligibility，不每頁逐一跑所有 engines、不重複 full-page transcription。AI 是 candidate，不是 judge。

Map candidate 必須獨立留在 analysis queue：graphic／map indication → page image → `scene_map.analyze_page_image()` → spatial graph。OCR 或 AI 成功不能 skip scene_map；保留 `floor_plan_graph_missing`、blocked publication、graph 與 prose 分開 acceptance。

## Adapter、CPU 與離線 setup

新增小型 `app/pdf_ocr.py`，只負責 local engine invocation、typed attempts／provenance；loader 保有 evidence／validation／replacement ownership。lazy candidate iteration 讓 Paddle reject 後仍跑 Tesseract，Paddle accept 則不多跑 fallback。保留既有 Tesseract language order、pytesseract 與 CLI；相容 `_ocr_image()` 不可成為 ingestion gate 的捷徑。

使用 bounded CPU worker，僅於需要 OCR 且 local model 檢查通過後載入 heavy dependencies。缺 dependency／model、corrupt、init／inference error、timeout 都記錄後 fallback，不讓整本 import crash、不下載。限制執行並收回 worker，不留無界 background inference。不要 plugin framework 或 language routing。版本紀錄必須来自實際 worker interpreter。

Config 沿用 app convention：`PDF_OCR_PADDLE_ENABLED=true`（release gate 成功才作 default）、`PDF_OCR_PADDLE_MODEL=PP-OCRv5_mobile_rec`（可 override）、`PDF_OCR_PADDLE_MODELS_PATH` 預設 persistent `DATA_DIR.parent / models / paddleocr`、`PDF_OCR_PADDLE_DEVICE=cpu`、finite worker timeout，必要時 explicit worker Python path。production model cache 不可 `/private/tmp`；不 GPU autodetect、不 mandatory CUDA。

獨立 `requirements-pdf-ocr.txt` pin 已測 PaddleOCR 3.7.0、PaddlePaddle 3.3.0、PaddleX 3.7.2，不讓 minimal bot install 拉 heavy runtime。bot 目前 Python 3.14，已測 Paddle CPU 是 Python 3.11；`scripts/setup_pdf_ocr.py` 支援指定 compatible persistent environment，遇不支援 Python/platform 明確失敗。

Explicit operator setup：安裝 optional pinned packages → download recognizer 與必要 detector 到 persistent path → verify artifacts／file digests → model manifest → 印 config／requirements。Runtime 所有 model 都指定 local dir，停用 source checks／remote retrieval，incomplete artifact fail closed to fallback。正常 startup／ingestion 無下載、無 cloud OCR；以 deny-network 執行驗證。temporary PNG 可以安全 temporary file，但模型 cache 不行。

Detector／recognizer resize 與 CPU settings 必須明確且可重現，先採 benchmark bounded CPU detector 設定。這不改 crop／DPI／threshold，也不暗稱不同 detector 設定有相同成本或輸出。

## Provenance、warning 與 budget

擴充現有 `local_repairs`，保留 original、bbox／crop、ocr_text、pair checks、final region status。新增 ordered `ocr_attempts`：engine、model、candidate、status（accepted／rejected／unavailable／error／empty）、reason、pair checks、optional confidence／runtime。Paddle rejected → Tesseract accepted 必須兩筆可追蹤，region final accepted；scanned page 同樣保存 attempts。完整 source／transcription 只留 private ingestion artifacts，不 commit corpus report。

Attempt diagnostics 留 audit，final review reasons 依選中頁與最終 local／AI／map outcome 計算。Tesseract 修好後，不把 Paddle mismatch 算成 page unresolved；也不能只因 adapter 跑過就清 warning。維持 warnings compatibility，列 resolved／remaining defects。

維持原 region budget，一個 crop 不因兩個 engine 算兩次。另列 local_paddle_attempts／accepted／rejected／failure、local_tesseract_attempts／fallback／accepted。local 不占 Docling／provider／paid vision／AI budget，原 AI／layout bounded resumable contract 保持。

## Identity 與版本

將 extraction pipeline `multicolumn-v3` 升為 `multicolumn-v4`，因 OCR priority／gates／audit／resume semantics 都改變。renderer／layout algorithm 沒改就不 bump；新增 complete dice／percentage gate 改 quality semantics 時升 quality identity。

`ocr` identity 包含 enabled、primary／configured model／device、adapter version、實際 worker PaddleOCR／PaddlePaddle／PaddleX versions 或 unavailable、model manifest digest、可取得的 Tesseract identity。missing／corrupt 不可看起來與 ready 相同；path 是 setup location，digest 才代表內容。

維持 whole-identity equality：old Tesseract accepted cache（含 native pages）在新 identity 全部 reprocess，不新增跨 identity native reuse。相同 new identity 的 unaffected accepted native page 可依原 policy reuse。Continue／Status／Cancel、leases、累積 provider budget、failed draft 不改。

## 檔案與非目標

預期 runtime：app/pdf_ocr.py、pdf_loader.py、pdf_quality.py、config.py。setup：scripts/setup_pdf_ocr.py、requirements-pdf-ocr.txt、env／setup docs、雙語 spec／catalog。tests 包含 adapter、loader、numeric pairs、AI、multicolumn、draft/resume、config。另加可移除 offline corpus helper 與 sanitized JSON／Markdown。

不得刪 MarkItDown OCR／shim／pytesseract／CLI。不得改 Keeper、combat、agents、tool routing、RAG、dice／NPC／narration runtime、scene_map semantics。不得加 Camelot／Surya／Azure／JEV。不得 whole-book Paddle、新 parser architecture、LLM judge、page language routing；不授權新增外部 service 上傳 document，existing bounded provider route 保有原 owner/config。

## 驗證與 release gate

Regression 涵蓋中英文 Investigator／調查員、STR／DEX／SAN／幸運、技能 %、1d6+2／2d6／1d4／1d10+DB exact；swap／ld6+2 rejection；HP unsupported completion；intact prose；Paddle reject 後 Tesseract accept；dependency／model／init／inference failure；both fail 保留 MarkItDown／AI／review；warning isolation；OCR success 仍 map analysis；budget separation；offline runtime；identity change／same-identity resume；Continue／Status／Cancel。

執行 full pytest、ruff check .、mypy app、python -m compileall app tests、git diff --check。CI mock engines 不需安裝 heavy runtime。macOS CPU 真 model smoke 在 deny-network 跑；Linux CPU 必須區分真的執行與僅 dependency compatibility 判斷，不假稱測過。

以相同 dependencies／config／budgets／provider availability 重跑 baseline 與 new local CoC corpus：The Haunting、The Lightless Beacon、Dead Boarder、多欄 golden、character sheets、stat／skill／dice tables、known numeric／glyph warning pages。real／synthetic 分開。逐 PDF hash／physical page 比較 review_pages、blocked_pages、source_pair_unresolved、numeric_pair_review、local_ocr_review、ai_fields_unresolved、floor_plan_graph_missing、各 engine attempts／accepted／rejected／fallback 與 AI requests。map graph 分開保護，不把 offline-only 結果說成 full provider/map validation。

優先 exact numbers → pairs → dice → skill → source wording → order → coverage → speed。commit 只含 hash／page／metric／error／timing／最少 diagnostic values，不含 copyrighted PDF、整頁或 prose。

真實 corpus 若 numeric／dice／pairing／source preservation 惡化：保留 tests／evidence、維持 prior production default，明確報告 failed release gate；不得強行啟用。unresolved 仍可見／可 resume。最終回報涵蓋 architecture、files、versions/model/device/setup、fallback/gates/provenance/identity/version、各書 before/after、numeric warnings／counts、maps/unresolved、全部 verification。

## 待 review 決策

Model、fallback order、offline 原則依使用者指示已固定。依 branch/spec workflow，此具體規格需明確確認後實作。persistent compatible worker environment、保守 identity invalidation、scanned critical fields review 是明確提出的 implementation choices；不將先前英文模型 pilot 當多語 production default 已驗證。


## 實作 checkpoint

Paddle attempt 使用 bounded 短生命 CPU subprocess，不在 bot 載入 Paddle；每次 latency 含 initialization。worker interpreter 明確指定，identity 記錄實際 package versions。region padding／DPI／原 region allowance 不改；low-text page OCR 另有等於 local OCR limit 的有限 allowance，分開紀錄。

完整 dice／百分比與 intact token order guard 補上現有 acceptance 缺口。source region 必須可唯一替換才標記 accepted；若選中來源已有通過同一 deterministic repair gate 的恢復 region，就跳過過期 OCR 工作。未驗證的 vision transcription 只留 derived/private evidence；無支持的 critical mechanics 阻擋 publication。valid map graph 不因 description 空白／不合格被丟棄，但 source validation 不放寬。

platform／full-provider 限制會記錄於 validation；deny-network corpus 結果不能宣稱 paid vision／map full import 成功。

最終結果見 [驗證報告](pdf_ocr_production_integration_validation_zh.md)。完整 expression guard 亦套用 AI repair；明確停用 PyMuPDF4LLM 隱藏 OCR。Worker setup baseline 為 Python 3.11–3.13。操作者已以 `$implement` 授權，local integration 完成；deployment 限制保留於驗證報告。


## v4 重新評估：公平比較與 image-only acceptance（已授權實作）

本修訂依 2026-10-01 的 `pr155_v4_ocr_reassessment_next_fix.md`，取代把 v3 before/after 當作 Paddle acceptance threshold 的要求。operator 已透過 implement 授權本次修訂。

### 問題與範圍

102 頁比較混入 PyMuPDF4LLM hidden OCR 與舊 vision local fallback。multilingual verified crops：numeric glyph 96/103、production numeric 94/103、dice 6/6、raw pair 32/50、skill pair 4/4、added numeric 0、coverage 396/409，支持保留 Paddle，但不證明 production publication 改善。full corpus unresolved pairs 與 numeric pair review 都是 0→0，不做大型 pair-resolution rewrite。

新增同 checkout／dependencies 的 Paddle OFF/ON 比較與 image-only positive path。保留 accept_region() 與 native-evidence accept_transcription() 的保守規則。必要變更限 loader/quality、private draft serialization、extraction identity、現有 validation script 與 loader/draft tests；gameplay 不變。

### Routing、資料與發布

區分短 native text、無 native text、graphic evidence、map candidate、character-sheet/table-like 與 ordinary illustration；不能只靠字數判定 scanned page。短 native 且 mechanics 有效時保留 native，除非有 missing-source evidence。無 native 的文字影像走 image transcription；純插圖不因 OCR 無法認證文字而阻擋。OCR 失敗不能直接把不明影像視為插圖。

private page artifact 新增 image_transcription：engine、status（unverified／authoritative）、candidate、reason、independent evidence provenance。單一 local candidate 保存為 unverified／no_independent_evidence。保留獨立 attempt diagnostics。draft 告知 operator 需 provider verification 或 manual approval；unverified 不得進 canonical source 或 authoritative gameplay RAG。

優先採 MarkItDown OCR 或 AI vision independent candidate：numeric multiset、完整 dice、percentage 必須 exact；已知 label/value pairs 相容，沒有 unsupported additions 或 mechanics 衝突。prose 不要求 byte-identical，但重大 deletion、contradiction、invented mechanics 必須拒絕；無法確定相容時維持 review。保存來源身分，避免 wrapper 重用同一 local output 被誤算 independent evidence。Tesseract 仍為 fallback／diagnostic，不是必要條件或唯一 authority。manual approval 保留既有 operator ownership 與 draft identity checks，只計算實際核准。

Paddle rejection 不得惡化已有 safe native/layout 的 disposition。local_ocr_review、vision_empty、transcription_unverified、empty_page 必須代表 unresolved source defect。map 即使 OCR 成功仍獨立跑 scene_map；graph failure 與 OCR failure 分開。

### Identity、metrics、公平比較

為 routing/publication semantics 更新 pipeline identity；保留 OCR model/package/cache identity 與 strict resume equality，舊 cache 不得跳過新驗證。候選全文只存 private artifact。

新增 counters：paddle_region_attempts、paddle_text_repairs_accepted、paddle_page_transcriptions_authoritative、paddle_page_transcriptions_unverified、paddle_rejected、paddle_failed、tesseract_attempts、tesseract_text_repairs_accepted、tesseract_page_transcriptions_authoritative、markitdown_transcription_agreements、ai_transcription_agreements、manual_approvals、image_only_pages、image_only_authoritative、image_only_unverified。保留相容舊 counter，標明計數單位，不混合 region repair 與 page authority。

輸出 sanitized pdf_ocr_controlled_ab_results.json。同 code/dependencies/PDF hashes/budgets，兩邊 hidden OCR OFF；Docling、MarkItDown、provider availability、review/publication rules 相同，只切換 Paddle enabled。OFF 為 explicit Tesseract；ON 為 Paddle/gate/Tesseract。比較 review/blocked、attempts、accepted repair、authoritative transcription、unresolved scanned、numeric/dice preservation、known pair failures、runtime。無 native 的 mechanics 不可用空 baseline 評分：使用 verified reference 或明示 unavailable。記錄 run identity/configuration/errors，不把 v3 差異叫 Paddle regression。

### 驗證與 rollout

tests 涵蓋 independent agreement、numeric/dice/percentage 衝突、unsupported additions、pair swaps、prose loss、來源重用、單引擎 private persistence、canonical exclusion、identity/resume、短 safe native、rejected challenger、插圖、OCR 成功仍處理 maps。controlled offline real corpus A/B 與 provider-enabled 真實 image-only／floor-plan tests 分開；後者檢查 scene_map rooms/exits 與完整 fallback 是否降低 unresolved。須有真正 Linux CPU smoke。缺 credentials/corpus/infrastructure 時明示未執行 gate，不以 synthetic 代替。

執行 pytest、ruff check .、mypy app、python -m compileall app tests、git diff --check。rollout 需 controlled A/B、provider image-only、provider real map、Linux CPU smoke、safe-page non-regression 全通過。此前僅宣稱 verified crops candidate 較 Tesseract 好，production image-only acceptance 尚未完整驗證。

### 非目標與取捨

保留 PP-OCRv5_mobile_rec、PaddleOCR 3.7.0、PaddlePaddle 3.3.0、CPU、explicit setup、persistent offline cache。不加入 Surya/Camelot/Azure/JEV，不開 Docling OCR/table structure，不用 confidence publish，不放寬 numeric/dice，不改 gameplay。實作前依既有 quality conventions 確定 narrow deterministic prose comparison；不確定就保留 review。驗證資料只屬 import-time，不建立 gameplay dependency。

本次實作補充：image evidence／verification／metrics 集中於 import-only app/pdf_image_transcription.py；新 verification 共用 durable layout provider budget，在 dispatch 前 checkpoint。明確 AI 無文字分類加上 Paddle empty 可排除 Tesseract noise；其他衝突候選保持 private。Linux CPU smoke 由獨立 PR workflow 執行，下載僅發生於 explicit setup。驗證結果另見 pdf_ocr_image_only_validation_zh.md。


## 已授權的 graph correctness／Linux safety 後續修正（2026-10-01）

Operator 明確授權直接在 PR155 修改 production code。維持既有 OCR／reading-order 主架構、pinned offline models、exact numeric/dice gates、hidden OCR OFF 與 Docling OCR/table structure OFF。最新已交付 pipeline 是 multicolumn-v5（需求引用較早 v4），本輪 map 認證 contract 會推進 extraction identity。不得改 gameplay movement、RAG 或規則。

- 分開 map 未分析、分析失敗、graph 缺失、invalid、incomplete、verified。Generated graph、驗證結果、image-visible labels 與 bounded repair provenance 只存 private page report；產生圖本身不代表認證。
- Structural validation 檢查 typed nonempty unique room IDs、有效 entry reference、local／cross-map target syntax、compass 與 exits。Duplicate／self／conflicting／asymmetric indoor edge 為診斷；不強制所有連接 reciprocal。
- Publication 前獨立重新看原圖，核對所有 room/location labels（含立面／其他樓層）、entry 與每條 proposed edge。實牆不得視為通道；不確定 geometry 或遺漏 image-visible locations 維持 incomplete。不得為通過檢查虛構房間或入口。
- 每頁最多一次 image-grounded graph repair，可修 structural 或 image-evidence 缺陷；輸入包含原 PNG、目前 graph 與明確錯誤，保留 source transcription。所有 generation／audit／repair 實際 dispatch 共用既有 durable provider budget，先 reserve/checkpoint；SDK 零 retry 且 timeout 有限。記錄 attempt、provider、image/input graph hash、errors、output graph、validation、elapsed time。修後重新 validate／audit，仍失敗就 block。
- Loader 僅回傳 certified graphs，invalid/incomplete graphs 留 private draft evidence。Library publication 再做 structural check，PDF graphs 必須有匹配 certified import report，才寫 scene_maps。推進 map／extraction identity，舊認證不能繞過新 gate。
- Linux smoke 分成 neutral pinned raster 的實際 offline CPU inference 與 corrupted-dice rejection。保留 1d6+2 原 raster，以未修改的 mechanics gate 拒絕 ld6+2。跨 host 比對固定 PNG／font/render metadata hashes；驗證 cache ready 與 network denied。
- 保留 image-only agreement／conflict／local-only 與 text-free illustration safety tests。尋找 real image-only authoritative positive，不放寬 lexical/mechanics gates；若證據不足，保留 blocker。

已同意的公開測試 seams：scene_map validator；pdf_loader.extract_text publication/report/cache；scenario_library.save_scenario persistence；real CPU smoke 與 worker network policy。重跑 provider-enabled Haunting p7、Beacon p16，特別人工核對地下室實牆與 Service Room／Lamp Room／Lantern Gallery。保留已有公平 OFF/ON non-regression 證據並執行最終 relevant comparisons。Rollout 必須兩張圖 verified、Linux positive／corruption tests 通過、至少一個 real independent authoritative image-only case，以及 persistence safety 全達成；任一 gate 未完成維持 hold。

後續 operator 指示：macOS Apple Silicon 才是 production correctness gate；Linux CPU smoke 改列 optional portability check。上述 rollout 使用 macOS positive inference／corrupted-mechanics rejection，Linux 結果仍分開回報。

必要的 entry 必須指向既有 room。若原圖無法確認入口，candidate 保留 private 並阻擋發布；不得虛構入口來通過驗證。地圖衍生描述只存 private/report artifact，不混入 canonical PDF transcription。
