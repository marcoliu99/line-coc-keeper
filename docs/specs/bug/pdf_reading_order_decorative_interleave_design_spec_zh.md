# 防止 PDF 邊飾字形插入正文閱讀順序

## 問題與目標

Lightless Beacon 實體第 13 頁的 SHA-256 固定為
`14437ca4eca3b6c05b22403bdad30bdb8668f1837e0a2d0ee42e49c160d8aa07`。
目前採用 3,631 字的 PyMuPDF4LLM layout source，warning 為
`ambiguous_columns`。邊界裝飾的 PDF 字元被插入核心正文句子，是真實的
reading-order 錯誤。四組 SAN label/value 檢查仍 matched，因此數值檢查無法
察覺此問題。目標是在有充分結構證據時，阻止裝飾字元污染正文，但保留真正來源
與既有 review 判定。

本 branch 從 PR170 最新 `fix/pdf-rich-ocr-candidate-selection` head 建立。
背景報告位於獨立文件 branch `maintenance/lightless-beacon-warning-triage` 的
`docs/reports/lightless_beacon_review_warning_triage.md`（`b3346cd`）。

## 重現與成因

不呼叫 provider 的暫存重現腳本 `/private/tmp/reading_order_p13_assert.py`
走正式 `_pymupdf4llm_page_chunks()`，斷言正文不含插入字元；目前失敗。
頁面為 594 × 774 pt。texttrace 中，外側字形 span 位於 x≈26.9–58.9、
y≈57.9–201.9，字級 24；相鄰左欄正文從 x=66 開始，字級約 9。
字形與正文分屬不同 PyMuPDF block；渲染畫面是邊框圖案，雖然 PDF 將其
映射成逐字排列的字母。同一位置與結構的 signature 出現在 18 個不同頁面，
其字體未出現在中央正文。判定依賴位置、隔離、字級、跨頁重複與渲染，
不依賴特定字型名稱或字串。

native extraction 將邊飾字元與正文分列，並報 `ambiguous_columns`；
PyMuPDF4LLM 在重疊的 y 位置把它們交錯排入正文。loader 的 source selection
檢查文字／數值覆蓋，未檢查閱讀順序，因此選了受污染的 layout。
本次 Paddle Layout 是 `model_unavailable` fallback，不是成因。

在 PDF 複本上暫時移除該 span，能讓插入字元消失，四組 SAN 配對及可觀察
骰式也保留；**但重跑 parser 的結果同時漏掉其他真正正文詞語**。所以不能
把「redact PDF 後重新解析」的全文直接採用。正式修正必須只移除能逐字綁定
到邊飾 span 的字元；否則保留原來源和 review。

## 範圍與非目標

只動 PDF source composition 接點與相應測試，重用現有
`pdf_quality.block_evidence()`／`bind_repeated_vertical_evidence()`。
不改 OCR 引擎、provider、Paddle Layout、rich candidate 仲裁、vision/map、
`_page_requires_review()`；不處理手寫 handout、credits 頁或其他 warning 數量。
不得寫死劇本、頁碼、字型、字元或座標。

## 設計

1. 跨頁 span evidence 綁定後，只有同時符合多項裝飾證據才列為候選：
   不同頁面重複且正規化位置一致、外側邊界、明顯大於正文的 display size、
   字體不屬正文、獨立 text block、bbox 不與正文 line 相交，並能唯一對應
   進入 layout 的字元。單靠垂直方向、靠邊、字體或重複都不夠。
2. 在受影響的局部文字 window，以有幾何／欄位歸屬的非裝飾原生正文 line
   對 selected layout 做有限、單調且唯一的字元對齊。允許既有空白及明確
   換行連字差異；只刪除確定對應裝飾 span 的字元。即使單字中黏了字母，
   也需唯一對齊；不能用全域 regex 刪一般文字。
3. 對修正候選做驗證：其他正文片語、heading、label/value、骰式運算子、
   SAN slash 關係與百分比都不得消失或變更；移除的字元數與順序須與 trace
   一致。驗證失敗則保留舊候選及 review。成功時只改 canonical 文字，
   quality report 可記 status／span 數／移除字元數／原因，不存另一份全文，
   不宣稱新增內容已驗證。
4. 保留 warning history；若欄位結構仍有歧義，`ambiguous_columns` 可以保留。
   不加 warning 特例。只有 targeted sanitation 改變文字可用性時才依原規則
   重新判斷 low-text pending，不額外跳過 vision/map。

沒有持久化 schema 或 library migration；舊 quality report 可沒有新增的
診斷欄位。vision 衍生文字不能作為 canonical source 證據。

## 保護真正內容

sidebar、callout、caption、heading、footnote、handout label、真正直排文字
都需保留。它們不會只因靠邊或直排就被刪除。若某個真實內容碰巧符合部分
裝飾訊號，對齊或結構證據不足時 fail closed。匹配 window 之外的原順序不變。

## 測試與驗收

先建立會重現問題的最小 synthetic production-path fixture，包含雙欄正文
與外側裝飾 span；測試須在修正前失敗，修正後驗證正文順序，而非只驗 warning。
另外測正常左欄→右欄、邊飾、真實 sidebar/callout、heading、caption、
語義直排、stat/value、骰式加減、SAN slash、百分比。對齊不唯一或修正候選
漏掉真正正文詞語時必須 fail closed；後者由 redaction 探針證實有風險。

以相同 SHA PDF、已保存的 MarkItDown candidates 重播 p13 與全 43 頁，
不重新呼叫外部 provider。報告 selected method、短片段、warnings/review、
pair checks、每個 source／warning 變化，並確認 PR170 八個 rich candidate 頁
沒有 regression。成功條件是核心正文連續、邊飾未插入、mechanics 不受損，
且沒有非預期 source/review 變化；若 p13 仍錯序，必須 HOLD。

跑 targeted reading-order、rich OCR、full pytest、ruff、mypy、compileall、
`git diff --check`。未經使用者要求不開 PR。

## 取捨與待審問題

逐字綁定的局部移除，比 redacted PDF 重新解析更保守。遇到無法唯一對齊的
頁面可能繼續 review，仍比漏掉正文安全。實作審閱需特別確認不會把一般字母
誤認為裝飾。唯一待審的是：某個 fixture 若需要增加跨頁 span 的通用結構
檢查，應先用幾何證據證明，不能為了通過測試放寬。
