# Paddle OCR 選用結果日誌

## 問題與目標

目前 INFO 日誌無法看出 Paddle OCR 是否被採用，或頁面文字最後是否來自原有的 Tesseract 後備路徑。只記錄實際選擇結果，不改變 OCR 文字、驗證或後備行為。

## 範圍

Production 最多修改 `app/pdf_loader.py`，必要時修改 `app/pdf_ocr.py`，並增加 OCR 專用測試。沿用現有 `OcrResult.status`：`accepted`、`rejected`、`unavailable`、`error`、`empty`。

## 不在範圍內

不修改 OCR 接受條件、SAN／骰子／數值檢查、語言順序、timeout、Paddle Layout、PDF 匯入判斷、地圖、provider 或遊戲流程。INFO 日誌不得包含 OCR 原文或 PDF 內容。

## 資料與流程

不新增資料結構或 schema。在 `_ocr_image()` 真正選用文字的位置：

1. Paddle `accepted`：記錄 `ocr=paddle status=accepted`，回傳 Paddle 文字。
2. 其他 Paddle 狀態：記錄 `ocr=paddle status=<status> fallback=tesseract`，照原流程執行後備 OCR。
3. 只有現有任一 Tesseract 分支回傳非空文字時，才記錄 `ocr=tesseract status=accepted` 並回傳。

`fallback=tesseract` 表示進入後備流程；Tesseract 真正產生結果時才有 accepted 日誌。未呼叫 Paddle 時不記 Paddle 嘗試。

## 測試與驗證

在 production `_ocr_image()` 路徑捕捉 INFO 日誌，覆蓋 Paddle 五種狀態。確認 Paddle 採用時不呼叫 Tesseract，其他狀態仍進入原後備流程，且 Tesseract 採用時另有日誌。執行 Python 3.13 真實 OCR smoke，以及完整 pytest、ruff、mypy、compileall、diff check。

## 待決問題

無；既有狀態值及後備分支已定義所需行為。
