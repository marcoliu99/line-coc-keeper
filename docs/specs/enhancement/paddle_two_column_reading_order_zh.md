# Paddle 雙欄原生文字閱讀順序

狀態：使用者已核准設計與 public seams，implementation 已完成。

## Baseline 與證據

從最新 main_v2 `f98dcaa6da8a4cc49a4661b11425db33a53c60d7` 分支。這是 PR157 的 merge commit，PR HEAD 為 `e63337feb41a8166d03f22f533974e84945a245a`；沿用其 OCR，不修改。

五頁證據：`/Users/marcoliu/workspace/paddle-layout-order-audit-20261003/`。subset SHA256 `362531220b2ad0d5d4479b6c5fed754de9a3660d6d1245727cd5127f0f4b322e`。PyMuPDF4LLM 1.28.2 為 1/5，PP-DocLayoutV3 為 4/5，三張真實雙欄均改善，單欄正常，跨欄標題三欄仍失敗。因此只處理明確雙欄。

## 目標與非目標

只有明確雙欄且所有原生文字行都能完整保留一次，才使用 Paddle 區域與 order 重排 PDF 原字。其他情況維持原本候選選擇、修補與 fallback。

不做整頁 Paddle OCR、不改 OCR／SAN／骰子修正、不新增依賴、不降版 interpreter、不加跨 interpreter worker、不重做 importer。不改 scenario library、admission/publication、reparse、map/topology、/coc start、遊戲、combat/check/SAN、RAG、AI provider；不帶回 PR155 states/gates、不做性能優化。單欄與三欄不採用新排序。沿用 PaddleOCR 3.7.0、PaddleX 3.7.2、PaddlePaddle 3.3.0、CPU。

## 最小模組與接點

預計新增 `app/pdf_layout.py`、`scripts/setup_paddle_layout.py`；`app/pdf_loader.py` 只加小接點；新增正式 tests/fixtures、`.env.example` 與簡短 README setup。`app/pdf_ocr.py`、既有 OCR setup、OCR requirements、遊戲/scenario/provider 模組不動；無 persistent schema 改動。

`pdf_layout` 包含 optional predictor、本機模型檢查、原生行配對、保守雙欄判斷與 structured result。public page adapter 回 accepted 原字或 fallback，帶有限 reason、初始化與 inference 時間。不修改 PDF，不回 OCR 文字。

```text
原 native / PyMuPDF4LLM 候選選擇
-> optional 本機 Paddle layout 候選
   -> 安全明確雙欄：使用原字重排結果
   -> 其他：保留原已選候選
-> 原 local repair / MarkItDown / AI repair / vision stages
```

接在原 native/layout evidence checks 後、修補前。accepted 使用明確 extraction method，保留既有 evidence/pairs/repairs。fallback 不增加 gameplay/admission gate 或新 review warning；回傳 tuple 與 callers 不變。

## Explicit setup，不允許 runtime 下載

獨立 `PDF_PADDLE_LAYOUT_MODEL_DIR`，預設 `~/.cache/line-coc-keeper/paddle-layout`。`PDF_PADDLE_LAYOUT_ENABLED` 預設 true，和 PR157 optional local OCR 一致；本機模型不完整就立即 fallback，先於 import Paddle／渲染。false 必須等同原流程。

Setup 只顯式下載官方 PP-DocLayoutV3 archive，驗證非空 `inference.json`、`inference.pdiparams`、`inference.yml`，用 temporary staging 安裝。採 stdlib 與現有 OCR setup 慣例，不加套件；implementation 時驗證官方 URL。runtime 指定 model name 與本機 model_dir，CPU、MKL-DNN off、四 threads，停用 SDK model-host checks，不走預設模型解析/自動下載。

依模型目錄 lazy 初始化並重用 predictor，串行鎖保護多執行緒。缺檔、缺套件、不支援 interpreter、壞模型、空/壞結果、init/inference exception 均 fallback。現有 Python 3.14 沒有官方 Paddle wheel，仍如 PR157 保留原擷取；真正 Paddle 路徑須既有支援的 Python 3.9–3.13 環境。不偷偷加 worker，也不宣稱 3.14 支援 Paddle。

## 保守採用條件

1. 在 inference 前取得非空 native PyMuPDF 行、原字串、穩定 ID、PDF bbox；只渲染該頁 150 DPI 作 layout，不建立 OCR detector/recognizer 或完整 PP-Structure。
2. 檢查 box/class/order 格式與範圍；圖片/chart/seal/header/footer image 不配文字。嚴重文字區域重疊、模糊配對、重複/壞 order 不採用。
3. 依實際 pixel/PDF 比例，把每行配到交集／原生行面積最高且至少 0.15 的單一區域。缺配或明顯模糊就整頁拒絕；不可先補未配行到尾端再 accepted。原字不修正、不新增、不替換。
4. 用 body text-region 水平區間重疊群組，確認恰有兩個分離欄、有明顯 gutter（初始最低頁寬 2%）、各有多行。這只是保守 eligibility，不取代 Paddle order。跨欄 body、單欄、三個以上或欄數不明全部 fallback，不重建三欄。頁首/尾/number/document title 不定義正文欄數。
5. 正文與欄內標題區域須屬於兩欄之一；跨欄標題只允許在正文上方，放兩欄前。正文中的跨欄標題視為不明而 fallback。order 若進右欄後又回左欄就拒絕，不自行猜新欄序。
6. 採 Paddle region order，區塊內 y→x。沒有 body order 的明確 header/footer 放前/後，如驗證實驗。標題須在兩欄之前；要求完整左→完整右，不以 token 完整代表欄序正確。
7. native IDs 恰好一次、無缺配/新增行、原字/text tokens 及正負號/百分比/骰子/完整有序 SAN loss 都保留。保留大小寫與標點。正式 tests 覆蓋 `1d6`、`1d10`、`1d4+2`、`SAN 1/1d6`、`50%`、`+20`、`-10`。失敗就回原候選。

Threshold 是保守初值，不代表任意 PDF 語意保證。若三個已驗證案例無法安全通過，先回報設計衝突，不悄悄擴大範圍或對單頁調參。fallback 可能保留原本欄序錯誤，但不能讓原本可匯入的 PDF 因此失敗。

## Logging 與時間

只 log `layout=paddle status=accepted` 或 `layout=paddle status=fallback reason=<有限code>`、初始化與每頁 inference 時間。不寫原文、raw result、可能含文字的 exception message/traceback。reason 可為 disabled、model_unavailable、backend_unavailable、inference_error、malformed_result、not_two_columns、overlapping_regions、incomplete_mapping、ambiguous_mapping、invalid_order、content_mismatch。init/inference 分開計時，與之前約 0.91–1.24 秒/頁比較，不做優化。

## 正式 tests 與待確認的 public seams

建議確認三個觀察介面：`pdf_loader.extract_text` 的最終文字/fallback、`pdf_layout` public 原生行＋regions 純排序 adapter 的 order/eligibility、explicit setup entry point 的本機模型/禁止 runtime 下載行為。不測 private implementation，CI 不下載權重。

把既有五頁 native positions、模型 boxes、獨立人工 expected order 轉為小型 repository fixtures，記錄來源/hash/versions。CI 只替換 external model boundary，使用真正 production 排序邏輯。三欄 fixture 必須拒絕，不當成修好。另用同一份 subset、本機顯式準備的權重做限定 live CPU smoke；不跑 scenario import 或整本。

至少測：普通雙欄左1/2/3→右1/2/3；大/跨欄標題→完整左→右；技能/法術/插畫雙欄；單欄原最終文字不變；三欄拒絕並維持舊流程；disabled/缺模型/缺套件/壞模型/init或inference exception/空壞結果均 fallback 且無下載；缺配/重複/模糊/嚴重重疊/交錯order 拒絕；原字行ID/數字/骰子/SAN完整；lazy init、localpaths、安全logging、分開timing。

在確認的 seams 用 vertical TDD，每次一個失敗行為→最小實作→針對性檢查。最後跑 `pytest`、`ruff check .`、`mypy app`、`python -m compileall app tests`、`git diff --check` 與雙軸 review。環境無法跑某項時用既有 dev dependencies 解決或如實回報；完成後 full suite 一次，只有新變更或失敗才再擴測。

## 交付與審閱決策

交付 baseline/final SHA、實際檔案範圍、五頁結果、所有 fallback、文字/數字/骰子/SAN、init/每頁時間、指定checks與 MERGE READY/HOLD。implementation commit/push 到 `enhancement/paddle-two-column-reading-order`；沒有明確要求不開 PR。

使用者以「開始實作」核准本 spec 與 public seams。layout optional、本機 cache 限定；Python 3.14 維持 fallback，不新增 Paddle 執行機制。

## 實作驗證

三張既有真實雙欄頁的 production adapter 輸出完全符合獨立 expected text；單欄、三欄回舊流程。CI 使用保存的原生行與模型區域，並測試真正 extract_text 接點及缺模型／套件／初始化／inference／配對失敗 fallback。額外拒絕欄內逆序及合併區域內明顯平行正文，僅作安全 veto，不重建三欄。

本機 CPU smoke 封鎖 socket 連線，五頁零網路嘗試。模型 hash 與前輪 A/B 一致；詳見 [測試結果](paddle_two_column_reading_order_results.json)。正式 OCR 與遊戲流程未修改。
