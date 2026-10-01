# PR155 離線 OCR integration 驗證

已在 `enhancement/pdf-multicolumn-ingestion` 完成候選產生、驗證與 fallback integration；**尚不能宣稱 production 匯入品質已改善**。102 頁離線 extraction 沒有任何真實 OCR candidate 通過 gate，掃描頁待審數增加。未證實 numeric／pair／dice／原文保存退步；raw word counter 的唯一差異是 Markdown `_Corbitt`，原字仍在，JSON 保留該診斷。

## 最終架構與設定

Reading order：PyMuPDF geometry → deterministic layout → 困難頁 Docling native block permutation。Docling 保持 `do_ocr=False`、`do_table_structure=False`。另明確停用 PyMuPDF4LLM 1.28.2 預設隱藏 OCR，避免绕過 candidate gate。

OCR：需要修復的 region（既有 padding／300 DPI）或低文字 graphic page → `PP-OCRv5_mobile_rec` → deterministic validation → rejected／empty／unavailable／error 時 Tesseract → validation → 既有適用的 MarkItDown／AI repair → validation → unresolved。一般 narrative page 不跑全頁 Paddle。低文字頁有獨立有限 allowance；local execution 不扣 provider budget。

Maps：page image → `scene_map` → spatial graph。OCR 成功也不能跳過 scene_map；graph 不因空白／rejected description 消失。Gameplay runtime 沒有新增 OCR、vision 或 tool call。

版本：PaddleOCR **3.7.0**、PaddlePaddle **3.3.0**、PaddleX **3.7.2**。CPU，四 threads，MKLDNN 關閉；detector `PP-OCRv5_server_det`、max side 960，orientation／unwarping 關閉。預設 multilingual `PP-OCRv5_mobile_rec`，可 override，沒有 page language routing。

Heavy dependencies 分離在 `requirements-pdf-ocr.txt`。操作者明確執行 `scripts/setup_pdf_ocr.py`，使用 persistent Python 3.11–3.13 environment，下載官方模型並建立檔案 hash manifest。預設 persistent cache：`DATA_DIR.parent/models/paddleocr`；setup 拒絕 temporary cache。實測模型位置為 `/Users/marcoliu/workspace/line-coc-keeper/data/models/paddleocr`。驗證用 temporary worker venv 不代表 production 配置。

Config：`PDF_OCR_PADDLE_ENABLED=true`、`PDF_OCR_PADDLE_MODEL=PP-OCRv5_mobile_rec`、`PDF_OCR_PADDLE_MODELS_PATH`、`PDF_OCR_PADDLE_DEVICE=cpu`、`PDF_OCR_PADDLE_PYTHON` 與有限 timeout。Startup／ingestion 只讀 local models，檢查 artifact hashes，不自動下載；dependency、model、init、inference、timeout 失敗均記錄後走 Tesseract。實測另以 OS sandbox 禁止 network。

## Validation、provenance、identity

Acceptance 重用 numeric_pairs、check_pairs、_NUMBER、accept_region、native geometry／intact words。完整 dice／百分比 exact match、stat／skill pairing、數值不憑空增加、完整原文順序不改寫；damaged region 必須唯一可替換。Confidence 只供 diagnostics。已修復 region 只有通過相同 gate 才跳過 stale OCR。

AI repair 增加共享完整 dice／percentage guard，保留既有有 evidence 的 scalar completion contract。MarkItDown／generic vision text 也要通過 deterministic gate；無法證實的掃描關鍵數值留待審，不靠 engine agreement 或 confidence 猜值。

每次 attempt 記錄 engine、model、candidate、status、reason、elapsed_ms、pair checks、optional confidence。能看見 Paddle rejected → Tesseract accepted；前者 diagnostics 不污染最後 actionable warnings。完整 candidate／PDF 原文只保留 private ingestion artifacts，committed corpus report 僅 hashes、頁碼、metrics。

Extraction identity 加入 enabled／model／device、實際 worker PaddleOCR／PaddlePaddle／PaddleX 版本、adapter version、model state／digest、Tesseract identity。Pipeline **multicolumn-v3 → v4**；quality **ai-import-repair-v5 → v6**。舊 OCR identity cache 不靜默復用；同 identity native accepted pages 可復用，Continue／Status／Cancel tests 通過。

## 真實 corpus before／after

Baseline `ead28a4ac4ab7a7b7f9829cd2e2462fc3b2c4ef6`。相同 PDFs／dependencies／CPU／budgets，無 provider keys、Docling 關閉、OS network denied。共 **102 頁**。Baseline 包含 SDK 隱藏 OCR，after 刻意停用，因此此表是 safety／routing comparison，不是單純 recognizer speed benchmark。

| 書 | 頁數 | Review 前 → 後 | Blocked 前 → 後 | 秒數 前 → 後 | Paddle attempts／rejected／failed | Tess fallback／accepted |
|---|---:|---:|---:|---:|---:|---:|
| The Haunting | 27 | 3 → 13 | 2 → 13 | 22.086 → 133.891 | 8／8／0 | 8／0 |
| Dead Boarder | 32 | 8 → 11 | 3 → 9 | 25.477 → 184.516 | 14／14／0 | 14／0 |
| The Lightless Beacon | 43 | 15 → 27 | 6 → 26 | 47.139 → 96.040 | 16／15／1 | 16／0 |

總計 Paddle **38 attempts、0 accepted、37 rejected、1 failed**；Tesseract **38 fallbacks、0 accepted**。AI repair request accounting **2 → 2**，實際成功 provider responses **0**。Source-pair unresolved／numeric-pair review／AI-fields unresolved 各 **0 → 0**；local OCR review **2 → 2**；floor-plan graph missing **6 → 6**。這些低 warning count 不代表 image-only 數值已驗證。

剩餘 review 頁（physical one-based）：Haunting **7、11、17–27**；Dead Boarder **1、3、12、19–21、23、25、27、29、31**；Beacon **1–5、13、16–18、25–42**。完整 blocked lists、per-page metrics／warning／identity 見 [extraction JSON](pdf_ocr_production_integration_results.json)。離線未產生實際 map graphs；regression test 驗證文字 OCR 成功仍執行 map analysis。

## 獨立 verified region 模型結果

實際 multilingual adapter 跑同一組 **12 個人工 verified real region crops**，與 full extraction acceptance 分開解讀。Private crops 由 hash 綁定；[model JSON](pdf_ocr_production_model_validation.json) 保存每個 region 與獨立 synthetic 結果。

| 指標 | 先前 Tesseract pilot | Multilingual mobile |
|---|---:|---:|
| Numeric glyph exact | 50/103（48.54%） | 96/103（93.20%） |
| Raw _NUMBER exact | 38/103（36.89%） | 94/103（91.26%） |
| Raw label/value pair | 22/50（44%） | 32/50（64%） |
| Dice exact | 5/6 | 6/6 |
| Skill pair exact | 4/4 | 4/4 |
| Added numeric | 7 | 0 |
| Text coverage | 見先前 pilot | 396/409（96.82%） |

仍有 table line-order／spacing／漏值；glyph recall 好不代表 pairing 正確。此 multilingual model 未重跑 geometry table score，不把先前英文／server 的 98/103 當成此模型。每次 crop 都是 cold worker；median **3283.039 ms**，不是 warm per-page throughput。未單獨量 CPU／RSS。

Mixed EN／ZH synthetic fixture 原文、stats、skills、dice exact 且修復通過，cold 5343.664 ms；CJK font ASCII stress 產生錯值，被 gate reject。Synthetic 不替代 real corpus。

Apple Silicon macOS CPU 真實執行通過；**Linux end-to-end 尚未執行**。Deployment 前仍需 Linux CPU smoke、image-only source validation 與真實 map／provider 路徑驗證。不能以放寬 gate／confidence threshold 解決 unresolved。

## 檢查與修改檔案

- Full pytest：**1719 passed、2 skipped、152 subtests passed**，20.92 秒，九個既有 warnings。
- Ruff：通過；mypy app：125 files 通過；compileall app tests：通過；git diff --check：通過。
- Standards review：runtime 無 findings；一個文件 numeric denominator 錯誤已修正。
- Spec review：P1 AI 完整 dice／percentage guard 已修正並加 regression tests；複查無 blocking runtime findings。

Runtime：app/pdf_ocr.py、pdf_loader.py、pdf_quality.py、pdf_ai_repair.py、config.py。
Setup：scripts/setup_pdf_ocr.py、requirements-pdf-ocr.txt、.env.example、README.md。
Tests：tests/test_pdf_ocr_recovery.py、test_pdf_ai_repair.py、fixtures/pdf_ocr/mixed_language.json。
Validation helper：scripts/experiments/validate_pdf_ocr_integration.py；另有 bilingual design specs、catalog、本報告與兩份 JSON。

建議保留已實作的 candidate／gate 架構，但 rollout 前完成上述缺口；本證據不足以宣稱 production import 已改善。MarkItDown、pdf_ai_repair、Docling ordering、scene_map 均保留；gameplay modules 未改動。
