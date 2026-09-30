# PR155 匯入階段 PaddleOCR recovery

[English](pdf_ocr_production_integration_design_spec.md)

狀態：規格待確認；尚未實作 production runtime。
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
