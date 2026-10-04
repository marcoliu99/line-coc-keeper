# PDF 最終人工核對警告修正

[English](pdf_final_review_warnings.md)

狀態：已實作；指定真實 PDF smoke 待確認來源。Branch：`fix/pdf-final-review-warnings`。
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

同份 PDF 與 quality report 路徑尚待使用者指出；目前不能推測第 12、16、17 頁實際結果。除此之外直接沿用使用者給定規則，不擴大品質系統。

## 本次實作驗證

原條件下成功補足案例先重現誤報；修改後 `tests/test_pdf_loader.py` 通過，包括 199/200/250 字的 public extract_text 比較，warnings 不刪除。完整 pytest：1994 passed、1 skipped、183 subtests passed；ruff、mypy（128 files）、compileall、diff-check 全部 PASS。

指定第 12、16、17 頁仍未取得同份 PDF／quality report 路徑，未執行該真實 smoke，未宣稱移除任何頁。合併結論暫為 HOLD，待補該證據。OCR、Layout、玩家訊息及資料庫均未修改。
