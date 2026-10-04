# 關閉 PyMuPDF4LLM 內建 OCR

## 問題與目標

PyMuPDF4LLM 1.28.2 預設啟用自己的 Tesseract OCR。目前正式 `to_markdown()` 呼叫沒有覆寫預設，因此在專案的 Paddle 優先 OCR 流程之前，Tesseract 可能已經執行。於該呼叫傳入此版本支援的 `use_ocr=False`，讓 PyMuPDF4LLM 只提供原生文字與版面證據。

## 範圍

只修改 `app/pdf_loader.py` 中的 PyMuPDF4LLM 呼叫及專用測試。保留 PR161 的 OCR 選用日誌。

## 不在範圍內

不改 Paddle OCR 接受條件、Tesseract 後備、Paddle Layout 排序、待核對頁判斷、publication、provider 或遊戲流程。不修改第三方套件。

## 資料與流程

不改 schema。`to_markdown(..., use_ocr=False)` 回傳原生文字及版面證據。既有低文字判斷再決定是否呼叫 `_ocr_image()`；後者仍先試 Paddle，失敗才用 Tesseract。警告與報告欄位維持原樣。

## 測試與驗證

確認正式 PyMuPDF4LLM 呼叫明確傳入 `use_ocr=False`。比較單欄、雙欄原生文字及版面選擇；確認 raster／低文字頁進 Paddle，Paddle 採用時跳過 Tesseract，被拒絕或不可用時維持後備。取得先前 log 對應的 PDF 後，用 Python 3.13 對同一檔案 smoke，僅記錄第 12、16、17 頁的 sanitized 長度、狀態及 warning。執行完整 pytest、ruff、mypy、compileall、diff check。

## 待確認問題

目前只有待核對的第 12、16、17 頁，尚未有對應 PDF 路徑；確認路徑後才能將 smoke 結果歸因於該報告。
