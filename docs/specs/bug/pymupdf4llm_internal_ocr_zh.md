# 關閉 PyMuPDF4LLM 內建 OCR

## 問題與目標

PyMuPDF4LLM 1.28.2 預設啟用自己的 Tesseract OCR。目前正式 `to_markdown()` 呼叫沒有覆寫預設，因此在專案的 Paddle 優先 OCR 流程之前，Tesseract 可能已經執行。於該呼叫傳入此版本支援的 `use_ocr=False`，讓 PyMuPDF4LLM 只提供原生文字與版面證據。

## 範圍

只在 `app/pdf_loader.py` 修改 PyMuPDF4LLM 呼叫，並增加保守、限縮的數值驗證 pass 與專用測試。保留 PR161 的 OCR 選用日誌。

## 不在範圍內

不改 Paddle OCR 接受條件、Tesseract 後備、Paddle Layout 排序、publication、provider 或遊戲流程。不修改第三方套件。驗證不能取代 canonical 文字或解除無關 warning。

## 資料與流程

`to_markdown(..., use_ocr=False)` 回傳原生文字及版面證據。既有低文字判斷再決定是否呼叫 `_ocr_image()`；後者仍先試 Paddle，失敗才用 Tesseract。

若高文字頁選用未變動的原生文字，而且 warning 包含 `numeric_pair_review`、`layout_numeric_loss`、`layout_pair_mismatch` 或 `source_pair_unresolved`，則額外執行一次 Paddle 全頁第二證據。Pair warning 保留原本檢查；`layout_numeric_loss` 只核對 layout 真正遺失的完整 mechanics（含骰式 modifier 與有順序的斜線式），每一項都必須在 Paddle 結果中靠近相同文字 anchor。頁面其他位置出現相同數字、anchor 不明或只確認部分項目，都不能解除 warning。原生 pair 本身未確定時不能只靠 OCR 確認。低文字頁略過此 pass，避免重複推論。Paddle 失敗或證據不足時保留 warning；此驗證不進 Tesseract。歷史 warning 與 canonical 文字維持原樣；`paddle_numeric_verification` 只記錄是否嘗試、狀態、已檢查／已解除／未解除 warning、缺失／確認／未確認的 mechanics、anchor 與 pair 計數，不保存 OCR 原文。最後待核對判定只略過明確解除的 warning。

相同 SHA 的 The Haunting 全書 smoke 共 27 頁：正常 Paddle OCR 12 次，數值第二證據 2 次（第 12、16 頁），沒有重複對第 17 頁推論。第 12 頁 `numeric_pair_review` 仍獲確認並解除最後 review。第 16 頁 layout 缺少一個重複出現的數值 `1918`，但無法唯一綁定遺失的局部上下文（合格 anchor 數 0），所以 Paddle 證據仍是 inconclusive，保留 warning 與最後 review。原生 canonical 文字維持不變。

`vision_review_required` 留在歷史 warning；有實際 vision 候選、pair 檢查乾淨且最後文字可用時，不由它單獨造成待核對。原本標為 `low_text` 的頁面，不能只靠較長的衍生 vision 描述宣稱所選來源文字已足夠。重播 SHA 相符的正式匯入報告後，第 7、16、17 頁仍待核對：第 7 頁另有 `ocr_evidence_loss`，第 17 頁被選用的非 vision 候選仍短於門檻。第 7、17 頁地圖仍保留；沒有暗中解除地圖或 OCR 的其他 warning。

## 測試與驗證

確認正式 PyMuPDF4LLM 呼叫明確傳入 `use_ocr=False`。比較單欄、雙欄原生文字及版面選擇；確認 raster／低文字頁進 Paddle，Paddle 採用時跳過 Tesseract，被拒絕或不可用時維持後備。測試數值確認、不一致、缺乏證據、Paddle 各種失敗狀態、無 warning 略過、低文字略過，以及 canonical 文字不變。用 Python 3.13 對 SHA256 相符的私人 PDF smoke，僅記錄第 12、16、17 頁的 sanitized 長度、狀態及 warning。執行完整 pytest、ruff、mypy、compileall、diff check。

## 本機 smoke 證據

已以 SHA256 找到與先前第 12、16、17 頁報告相符的私人來源。Python 3.13 本機重跑未出現 PyMuPDF4LLM 的 Tesseract／OCR 頁面訊息。全書 27 頁中只有第 12、16 頁額外執行數值驗證；既有正常 Paddle 呼叫為 12 次。第 12 頁唯一有爭議的 pair 獲確認，因此最後待核對判定解除該數值 warning，但原生全文與歷史 warning 均保留。第 16 頁數值行證據不完整，warning 與待核對仍保留。第 17 頁只走原本低文字 Paddle 流程一次，未進數值驗證。
