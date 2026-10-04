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

## Source-run provenance model

source run 是能唯一綁定 texttrace glyph geometry 的最小 PyMuPDF
line/span 字元範圍。其身分只在**單次 extraction 的頁內**穩定，不保證跨
PyMuPDF 版本持久。必要欄位是 page index、block/line/span index、原始
字元序列及 span 內 offset、writing direction、bbox、extraction order。
另須唯一綁定 texttrace 的 sequence number、字元區間與 glyph bbox；若
沒有 texttrace，或一段來源可對到多個 trace 區間，就不能自動去重。
p13 的同一 trace run 包含數行來源，因此不能只用整個 trace bbox 或
sequence number；必須定位相應 glyph 區間。

字型與字級用來佐證 block/span-to-trace 對應及 region 連續性，不能單獨
定義來源身分。若 API 真能提供個別 text object 的 xref，可以記錄；
這不是必要條件，也不能把 page content-stream xref 冒充 text object ID。
即使文字、字型與 bbox 重疊，不同 block/span 仍是不同來源身分。

比較不同 geometry 記錄前，先正規化頁面座標。診斷 fingerprint 將座標
量化至 0.25 PDF point；實際比對未四捨五入的 bbox，每個邊緣最多相差
0.5 point，且 glyph 方向相容。容差只能佐證單一 block/span-to-trace
match，不能合併兩個不同來源身分。缺 bbox、超過容差或存在多個可行
trace match 時 fail closed。fingerprint 同時記錄頁面尺寸與正規化規則。
原始字元留在 transient ledger；quality metadata 只記身分 hash，不記正文。

## Layout-to-source alignment 與重複資格

1. 將 selected PyMuPDF4LLM output 切成有界的段落／行範圍。先用原字元
   對齊疑似重複範圍，只允許 deterministic 空白與換行正規化；標點和
   mechanics operator 必須保留。在 source-order anchor 附近局部搜尋，
   不做全頁任意搜尋。文字相同只提出候選對應，不能獨立證明來源。
2. 用相鄰且**唯一對應**的來源 run，判斷每個輸出範圍的 column/region、
   前後來源順序與段落連續性。來源 run 的 PDF geometry 決定 region；
   輸出字串本身沒有 PDF bbox。跨 column、heading/body、caption/body、
   sidebar、footnote 或無關段落邊界的對齊須拒絕。不可用語意相似度或
   LLM 判斷。
3. 只有兩個不同輸出範圍都唯一對到**同一 source-run identity**、raw
   span 與 texttrace 中該 run 各只有一份、native extraction 也只有一次、
   rendered evidence 沒有第二個 semantic region，而且不存在能解釋任一
   輸出位置的另一個來源 run，才可判為重複。對齊與 region 指派都要唯一。
   兩個相同 PDF block、兩欄刻意重複、重疊的 PDF 文字 object 都不是
   「同一來源 run 輸出兩次」。runtime 須透過 texttrace 排除第二個被繪製的
   文字 region；真實 PDF 驗證時另檢查渲染裁切圖。若 image-only 內容可能
   含有第二份語意文字且無法排除，保留 review 並拒絕自動去重。畫面僅是
   佐證，不能代替 source ledger。

## Deterministic survivor 與 fail-closed 規則

對每個可能保留的位置，先虛擬刪除另一份，檢查兩側 source-run 順序。
保留的那份，必須讓相鄰唯一對應的來源 run 依 geometry reading order
接續同一來源段落，包括下一個來源行及其 region。打斷這種連續性的孤立
輸出才是刪除候選。p13 的後一份接續下一行 PDF 正文，前一份則是標題後
孤立前綴；這只是該頁的 evidence，**不是**通用的「保留後一份」規則。
若兩種保留方式都成立、都不成立，或相鄰來源無法唯一對應，就不修改。
不能預設保留第一份或最後一份。

局部編輯只移除可證明的多餘輸出字元範圍及其附著的局部分隔符，不變動
周圍內容。禁止全域段落／行 cleanup、`text.replace`、相同字串 hash 過濾、
語意去重、fuzzy match 或整頁重解析。些微格式差異也不能在缺乏唯一
provenance 時成為去重理由。來源 match 不唯一、layout 多個 match 而
無法選擇 survivor，或未解邊飾插字落在對齊窗口，都保留 selected text
與 review。

## 處理順序與 preservation

選擇**邊飾修復 → 來源重複去重 → 最終 preservation**。邊飾修復先移除
獨立綁定的 margin glyph，避免污染精確 output-to-source alignment；它
保有自己的 evidence record 與局部驗證。接著去重使用*邊飾修復後*的
文字及 offset，不能重用修復前 offset。若邊飾修復失敗，或去重窗口內
還有不確定 glyph，該窗口的去重須 fail closed。兩種 transformation
各自保留紀錄。

在提出去重結果後，除各階段局部驗證外，還要對組合結果做最後一次
source-bound preservation。每個**不同** source-run identity 仍須在正確
region 與順序中出現。mechanics 出現次數應和 source ledger 比，不應
和已重複的 layout 比：兩個來源 run 的合法重複 stat block 要保留兩份；
同一已證明來源 run 的兩次輸出才能減為一份。檢查 stat/value 與
skill/value binding、骰數／骰面／`+`、`-` modifier、SAN slash 順序、
百分比、damage、名稱及其他有意義數值。任何檢查失敗，還原 dedup 前的
selected text，保留 warning history 與 review。

可選 quality record 至少有 `status`（`duplicate_source_emission_repaired`、
`duplicate_source_emission_ambiguous` 或明確的 preservation failure reason）、
來源身分 hash、邊飾修復後的輸出範圍、保留範圍、移除範圍及字數、
source/trace/neighbor match 是否唯一，以及 preservation 結果。這些
布林證據不應寫成主觀機率 confidence。保留 `ambiguous_columns` 與既有
final review policy；去重成功不代表全頁已驗證。metadata 不複製完整
source prose。不新增持久化 schema 或 gameplay state migration，舊
quality report 沒有此可選紀錄時仍可讀。

## 安全案例與測試

合成 fixture 必須走 production source-selection path 與局部編輯，不只測
matching helper：

| 來源／輸出證據 | 必要行為 |
|---|---|
| 同一來源 span 被輸出兩次 | 來源及相鄰行唯一對齊後，只移除多餘的一份。 |
| 兩個不同 block 文字相同 | 兩份都保留。 |
| PDF 有兩個視覺重疊的文字 object | 保留並 review：兩個 raw object 不是同一 source run 的重複輸出；日後 source-object dedup 需要另一份證據與設計。 |
| 兩欄刻意重複同一句 | 兩份都保留。 |
| running header 與正文使用相同標題 | 兩份都保留；region 身分不同。 |
| 兩個來源 region 有相同 mechanics block | 兩份都保留，包括兩份 `SAN 1/1D6`。 |
| 一個 mechanics source run 被輸出兩次 | 來源與 survivor 證據唯一，且通過 source-ledger mechanics preservation 才能移除一份。 |
| 空白／標點有些微差異 | 不得只靠文字相似度 fuzzy dedup；仍須 provenance。 |
| 輸出與來源或保留位置有多種對齊 | 不修改，記 `duplicate_source_emission_ambiguous`，保留 review。 |
| 真正 sidebar、caption、footnote、vertical semantic text | 即使與鄰近文字相同也要保留。 |

須包含 p13 形狀 fixture：單一 raw span 輸出兩次，一份是孤立前綴，
另一份接續段落；證明 survivor 根據來源順序選出，不依輸出先後，並測試
相反輸出順序。另測先有邊飾插字再出現來源重複，以及邊飾修復不確定時
阻止同窗口 dedup。測試須確認 warning/review 保守維持。

以相同 SHA 的第 13 頁，再以已儲存候選跑 43 頁 replay，不呼叫外部 provider。
驗收須確認段落只保留一份且順序連續、沒有其他 canonical text 變動、沒有
mechanics 遺失，warning/review policy 不變。PR170 八個 rich candidate
頁的選擇行為須維持；p3、p18、p29 不變。邊飾局部修復須另外維持測試。
實作階段執行 targeted tests、完整 pytest、ruff、mypy、compileall 和
`git diff --check`。若 provenance 不足，留下重複並回報 **HOLD**，不能
依內容文字自行去重。

## 取捨與 review gate

目前 PyMuPDF4LLM 輸出沒有 span ID。實作必須先證明回到 PyMuPDF 來源
geometry 與相鄰來源順序的唯一對齊，才能編輯；無法證明的實際 extraction
重複可能因此暫時不修。這是刻意的保守選擇。

## 實作紀錄

`pdf_quality.repair_duplicate_source_layout()` 現在檢查唯一的 raw span 與
painted trace run、native/layout 出現次數、同一 block 的相鄰來源行，以及
唯一的段落流向 survivor。`pdf_loader.extract_text()` 在 decorative repair
後呼叫它。對齊不確定或 mechanics preservation 失敗時，selected source 與
review 維持原狀。新測試涵蓋不同來源 block、雙欄、header、overlay、兩種
survivor 位置、模糊對齊，以及合法與重複輸出的 mechanics。
對齊不明或 preservation 失敗會新增需 review 的 warning；成功修復不會
清除原有 warning。

相同 SHA 的 43 頁 saved-candidate replay 中，只有第 13 頁 selected text
改變：decorative repair 後的 3,620 字變為 3,566 字，重複開頭由兩份變
一份。23 個 review pages、66 項 warnings 與八個 rich candidate 選擇
均未變；沒有觀察到 mechanics 遺失。此 replay 沒有重跑外部 vision 或
map analysis。
