# PDF 最終人工核對警告修正

[English](pdf_final_review_warnings.md)

狀態：已實作；The Haunting 真實 PDF smoke 已通過。Branch：`fix/pdf-final-review-warnings`。
基準：最新 main_v2 `92b52e4da88063d14ae3c9585fda9d5060d19b37`。

## 問題與目標

`extract_text` 在候選文字不足 200 字時保留 `low_text`，之後候選補足也不移除。最終判定只排除 `native_two_columns`、`layout_unavailable`，因此歷史不足與成功 `local_ocr_repaired` 都會造成玩家核對警告。只修最後是否加入 `review_pages`，不改修復或來源選擇。

## 最小設計

在 `app/pdf_loader.py` 增加私有 `_page_requires_review(row: dict, final_text: str) -> bool`。維持原介面與 report schema，不修改 warnings、evidence、candidates 或 repair 紀錄。

- informational 集合僅包含 `native_two_columns`、`layout_unavailable`、`local_ocr_repaired`。
- `low_text` 只有在最終選用文字仍低於 `_LOW_TEXT_THRESHOLD`（目前 200）時要求 review；達到門檻後只保留歷史紀錄。
- 其他 warning 預設要求 review，不以文字長度覆蓋；未知 warning 也保留。
- 原本空頁追加 `empty_page` 的行為不變。
- 使用最終選用來源文字判斷 low_text，不以後加的診斷標記補足字數。
- 只替換最終 review 條件；Paddle fallback metadata 不新增為 warning。

目前真正需 review 的 warning 包含 `ambiguous_columns`、`empty_page`、`source_pair_unresolved`、`numeric_pair_review`、`layout_pair_mismatch`、`layout_numeric_loss`、`layout_text_loss`、`ocr_pair_review`、`ocr_pair_mismatch`、`ocr_evidence_loss`、`local_ocr_review`、`ai_fields_unresolved`、`vision_failed`、`vision_pair_review`、`vision_pair_mismatch`、`vision_review_required`。不新增清單外的 warning 名稱，也不推測其他 warning 已被後續修復。

## 範圍與非目標

Runtime 修改限定 pdf_loader 與必要 tests。不修改 OCR、Paddle Layout、PR160 三欄、模型、套件、provider、scenario library、map、RAG、gameplay 或 Discord 文案。不重新匯入劇本或修改既有資料。

## 測試與 evidence

透過 `extract_text` stub 現有候選/修復回傳，檢查 review_pages 和歷史 warnings 不變：最終 low_text 補足、不足、剛好門檻、low_text + local_ocr_repaired、local_ocr_review、numeric_pair_review、ocr_evidence_loss、vision_review_required、兩個既有 informational warning，以及未知 warning。成功案例不依賴 Paddle/Tesseract 引擎名稱。保持 empty_page 行為。

真實 smoke 僅處理使用者所指同份 PDF 的 12、16、17 頁，或重用同次可信的最終 quality evidence；不硬要求全部移除。保存頁碼、最終字數、歷史 warnings、resolved/unresolved 分類與修改前後 review，完整 PDF 文字不寫入 log。資料在 temporary evidence 目錄，source 唯讀、不呼叫 save_scenario。

完成實作後執行 pytest、ruff check .、mypy app、python -m compileall app tests、git diff --check，再 commit/push。未要求開 PR，故不建立 PR。

## 待確認事項

使用者指定的 70dbe2a5 來源路徑已不存在；以現有 1f0e59f0/source.pdf 與其 quality report 進行驗證，PDF SHA256 與其他保存的 Haunting trimmed 副本一致。歷史與新環境的 OCR/provider 輸出未完全相同，不將兩者視為完全相同環境的 A/B。

## 本次實作驗證

原條件下成功補足案例先重現誤報；修改後 `tests/test_pdf_loader.py` 通過，包括 199/200/250 字的 public extract_text 比較，warnings 不刪除。完整 pytest：1994 passed、1 skipped、183 subtests passed；ruff、mypy（128 files）、compileall、diff-check 全部 PASS。

The Haunting PDF SHA256：`de28127fe4978a32076a4a5099496c0a377b91408b2eb402151bd5151009fcb1`。歷史 report 與新 extraction 的 review_pages 都為 [12,16,17]；舊條件套用同次新 evidence 也為相同清單。

- 第 12 頁：3196 → 3196 字；native_two_columns 為 informational，numeric_pair_review 未解，保留。無 OCR 呼叫。
- 第 16 頁：3356 → 3356 字；native_two_columns 為 informational，layout_numeric_loss 未解，保留。無 OCR 呼叫。
- 第 17 頁：29 → 169 字；low_text 仍不足且 vision_review_required 未解，保留。Paddle accepted，無 Tesseract fallback。

本機 Python 3.13 extraction 無 provider key，runtime network attempts=0，來源 hash 不變。使用原確認訊息函式得到第 12、16、17 頁警告。此 PDF 沒有僅剩已修復 warning 的案例；移除效果由 public extract_text regression 驗證，不宣稱真實頁號已移除。Evidence 保存在本機 temporary JSON，PR 描述提供上述摘要。重跑 pytest：1994 passed、1 skipped、183 subtests passed；ruff、mypy、compileall、diff-check 全部 PASS。OCR、Layout、玩家訊息及資料庫均未修改。
