# 依 PDF 來源證據去重，不依正文文字去重

## 問題與證據

《The Lightless Beacon》PDF 第 13 頁（SHA-256
`14437ca4eca3b6c05b22403bdad30bdb8668f1837e0a2d0ee42e49c160d8aa07`）
在「South Pier」標題下，渲染畫面只出現一份段落開頭。PyMuPDF 的 native
text、block/line/span 與 text trace 也各只有一份。該頁 content stream xref
為 `71`；PyMuPDF 沒有提供這一行個別 text object 的 xref。來源是 block 5、
line 1、span 0，bbox 約 `(66, 189, 284, 204)`，9 點、水平正文。
text trace 的序號為 26、type 0、opacity 1，只有一組符合的字元。頁面裁切圖
也只顯示一份，沒有重疊的文字層。

但 `use_ocr=False` 的 PyMuPDF4LLM 原始 Markdown 將同一段 52 字開頭輸出
**兩次**：第一次緊接標題（第 3 行），第二次是後續段落的開頭（第 5 行）。
完整文件 chunk 的 offset 分別為 `447–499`、`502–554`。專案的空白正規化
沒有產生或移除重複；native extraction 只有一次。來源選擇採用了已重複的
`layout`，而邊飾局部修復只移除六個無關的 margin glyph，仍留下兩份正文。
因此兩個 Markdown 位置對應同一個 PDF 來源 run；分類為
**RECONSTRUCTION_DUPLICATION**，首次出現在 PyMuPDF4LLM 重建邊界。
目前沒有證據可指定其內部哪條 traversal path 重複輸出，不在此猜測。

本規格接續邊飾插字修復，只處理剩餘的來源重複輸出；不授權一般字串去重。

## 目標、範圍與非目標

讓後續實作只移除「同一 PDF 來源證據的額外輸出」，並保留作者刻意重複的
文字。變更限於 PDF layout/source composition 接點與對應測試。不要修改
OCR、Paddle Layout、MarkItDown 候選選擇、vision/map、mechanics comparator 或
最終 review 規則。不可修改 PDF 後整頁重解析並採用新文字。production 規則
不得依賴劇本、頁碼、特定句子、字型或固定座標。

## 綁定來源的設計

1. 以 PyMuPDF block、line、span、texttrace、bbox、方向和 extraction order
   建立逐頁 source ledger。每個可觀測來源 run 的**頁內身分**由結構 provenance
   決定，不能只用文字。若沒有個別 PDF object ID，明確記錄此限制，不可
   虛構 object ID。即使文字相同，不同來源 run 仍是不同身分。
2. 以來源的原字元（只允許明訂的空白／換行格式差異）、相鄰且唯一的來源行、
   column/region membership，以及單調來源順序，將疑似 Markdown 位置
   對齊 source ledger。字串相同只代表候選 match。只有兩個輸出範圍唯一
   對應**同一**來源身分、沒有第二份來源 run 支持同樣文字，而且周邊 geometry
   可辨認哪個位置接續真正段落時，才有資格去重。heading、body、caption、
   sidebar、footnote、column 邊界都必須限制對齊。PDF 若有兩份來源 run，
   即使標題與正文文字相同也必須保留。
3. 只做有界的局部編輯，移除額外輸出的範圍。不能全頁刪除相同段落，也
   不能依長度或語意猜哪份留下。沒有獨立且唯一的 provenance 時，微小的
   空白或標點差異也不能觸發 fuzzy dedup。若無法唯一指派來源或留下的位置，
   或範圍跨結構邊界，保留原 layout 文字與 review。
4. 以 source ledger 和未修改的 selected text 驗證編輯：每份不同來源 run
   仍須在正確 region 中呈現；非重複名稱、實質片語、標題／正文順序、
   stat/value binding、骰式運算符、SAN 表達式與有意義數值都要保留。
   重複 mechanics block 若無法證明只有一個來源身分，必須 fail closed。
   保留原 warning 歷史與 `ambiguous_columns`；去重成功本身不能清除 review。
   只儲存精簡診斷 metadata（狀態、來源 run 身分或 hash、輸出範圍、移除字數、
   失敗原因），不儲存第二份全文。

不新增持久化 schema 或 gameplay state migration。舊 quality report 沒有可選
metadata 時仍可讀。判定必須 deterministic、local；不用 LLM 或 provider 決定身分。

## 安全案例與測試

合成 fixture 必須走 production source-selection path 與局部編輯，不只測
matching helper：

| 來源／輸出證據 | 必要行為 |
|---|---|
| 同一來源 span 被輸出兩次 | 來源及相鄰行唯一對齊後，只移除多餘的一份。 |
| 兩個不同 block 文字相同 | 兩份都保留。 |
| PDF 有兩個視覺重疊的文字 object | 只有能證明結構等價且僅一份語意來源才可去重；否則保留並 review。 |
| 兩欄刻意重複同一句 | 兩份都保留。 |
| running header 與正文使用相同標題 | 兩份都保留；region 身分不同。 |
| 重複 stat/mechanics block | 除非證明來源身分、完整運算符與 label binding，否則 fail closed。 |
| 空白／標點有些微差異 | 不得只靠文字相似度 fuzzy dedup；仍須 provenance。 |
| 輸出與來源或保留位置有多種對齊 | 不修改，保留 review。 |
| 真正 sidebar、caption、footnote、vertical semantic text | 即使與鄰近文字相同也要保留。 |

以相同 SHA 的第 13 頁，再以已儲存候選跑 43 頁 replay，不呼叫外部 provider。
驗收須確認段落只保留一份且順序連續、沒有其他 canonical text 變動、沒有
mechanics 遺失，warning/review policy 不變。邊飾局部修復須另外維持測試。
實作階段執行 targeted tests、完整 pytest、ruff、mypy、compileall 和
`git diff --check`。若 provenance 不足，留下重複並回報 **HOLD**，不能
依內容文字自行去重。

## 取捨與 review gate

目前 PyMuPDF4LLM 輸出沒有 span ID。實作必須先證明回到 PyMuPDF 來源
geometry 與相鄰來源順序的唯一對齊，才能編輯；無法證明的實際 extraction
重複可能因此暫時不修。這是刻意的保守選擇。本規格已可供 implementation
review，但本輪沒有 runtime 變更。
