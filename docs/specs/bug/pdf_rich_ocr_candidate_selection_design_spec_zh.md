# 保留低文字 PDF 頁面中有證據支持的豐富 OCR 候選

## 決策與證據

真正回歸在來源選擇，不在 OCR 引擎不足。PR92 後，一般 `select_text()` 的字詞覆蓋率會讓約 101 字的 native/layout 殘片否決《The Lightless Beacon》實體第 31 頁 4,233 字的 MarkItDown 候選。第 32、34、35、37、38、40、41 頁也有同類問題。「極少可用內容的 canonical ＋ review」不如「保留已知來源證據的豐富候選 ＋ review」。本設計只恢復這個狹窄選擇，不回到 PR30「有 OCR 文字就採用」。

**已淘汰的設計：**「新增 mechanics 必須由整頁 Paddle 逐項核對」已被真實八頁實驗否定：Paddle 嚴格比對只確認 3/311，Tesseract 為 0/311。小範圍屬性診斷發現 Paddle 常能看到第一欄屬性值；問題是 MarkItDown 表格鍵與線性 OCR 文字的表示不相容。繼續建立全項跨格式 OCR verifier，會製造另一個難題，而非修正來源選擇。不要加入 Tesseract 投票、OCR ensemble 或第三個引擎。

## 範圍與不處理事項

後續實作只改低文字 graphic 頁的 MarkItDown 候選仲裁及精簡品質證據。一般 native/layout `select_text()` 維持嚴格，正常正文仍用 85% 覆蓋規則。`use_ocr=False`、本機 Paddle OCR、正式 Tesseract 備援、既有 numeric Paddle verification、Paddle Layout、局部修復、額度、map/vision、頁面圖片、索引、預製角色、review 規則、provider 與遊戲流程全部不改。rich candidate 選擇不需要新增本機或外部 OCR 呼叫。

## 弱基線的確定性分類

`baseline < _LOW_TEXT_THRESHOLD`（目前 200 字）是必要條件，**不是充分條件**。使用既有頁面證據和確定性的文字／幾何規則分類選中基線。只有覆蓋率要求主要來自可辨認的版面產物或稀疏碎片，且剩餘可觀察來源能逐項核對，才叫弱基線。短規則句、明確 NPC／地點名稱、綁定 mechanic 即使整頁不到 200 字，仍是真正來源。無法安全分類的碎片保留為必要證據，不能因為「像頁眉」就忽略。

只可從*覆蓋率比較*排除明確產物：獨立頁碼、已知 picture/image markup、空白或純裝飾符號、由結構可確認的垂直碎裂字母。只有同頁幾何或跨頁重複證據確立其功能時，才可排除 running header/footer；不可依劇本標題、出版社特定文字或固定頁碼猜測。品質報告仍保留原始基線與分類理由。缺少幾何／重複證據時，疑似標題仍是必要來源。

必要來源包含：實質語句與完整規則句、明確存在的 NPC／地點名稱、屬性／資源／技能的標籤和值、具有上下文的百分比和數值、完整骰式與修正值、有順序的整組 SAN 斜線式，以及其他可觀察 mechanics。已確立的 native pair 沿用 `numeric_pairs()`／`check_pairs()`；必要時保留重複次數。獨立頁碼不是遊戲數值，但綁在標籤或句子上的數值必須保留。

若選中來源達 200 字，或短基線有候選無法保留的實質證據，維持一般嚴格仲裁。不能只因字少就宣稱基線弱。

## 狹窄的候選仲裁

只在現有低文字 graphic `pending` MarkItDown 路徑執行。先照常執行未修改的 `select_text()`；若原本接受，維持原行為。若短基線因整體字詞覆蓋被拒（包括可獨立證明只是頁碼造成的表面數值遺失），才分類基線，並檢查候選是否保留**必要**而非已忽略的來源。真正數值遺失、pair mismatch 或 mechanics 衝突仍拒絕。

Promotion 必須同時滿足：

1. 基線有明確理由可分類為弱、低於 `_LOW_TEXT_THRESHOLD`；候選達同一門檻。不加入任意的候選／基線倍數。
2. 候選是既有的同頁 MarkItDown 結果，且沒有替代字元或完整 mechanics 明顯損壞等基本錯誤。
3. 保守、確定性的比較能確認每個必要基線語句和綁定數值都被保留。不得用語意相似或整頁裸數字重疊掩飾語句或關係遺失。無法確認就維持舊來源與 review。
4. 已解析的 native `numeric_pairs()` 都在候選中匹配。`STR 60` 變成 `STR 50`、`1d6+2` 變成 `1d6`、`SAN 1/1d6` 變成 `SAN 1d6/1` 都拒絕。基線 `STR 60`，候選含 `STR 60`、`DEX 70`、`LUCK 50`，則保留已知來源。

候選**新增**的屬性、技能、文字及其他數值，不必取得 Paddle 獨立投票。弱基線原本沒有這些內容，不能認證它們；但「無法認證」也不是衝突。Promotion 意味 `source_preserved=true`，**不意味** `candidate_fully_verified=true`；review 可以繼續存在。若長候選漏掉短基線的實質來源，仍拒絕。若基線完全沒有必要項目，至少要有同頁來源、明確弱基線分類、候選字數門檻及基本損壞檢查；記錄缺乏獨立內容驗證，並保留 review。

接受後選用既有 MarkItDown 候選、設 `method=markitdown`；保留 native/layout/MarkItDown 候選、頁面圖片、歷史 warning，以及既有 `pending`／vision／map 流程。不改 `_page_requires_review()`，不自動清 warning。「較有用的 canonical ＋ review=YES」是預期結果。拒絕時維持原來源與 review。

## 品質 metadata 與相容性

新增或調整精簡 `rich_candidate_selection`：`attempted`、`status`、`reason`、`baseline_strength`、基線／候選字數、必要／保留來源項目數、數值／mechanics 衝突數、`source_preserved`、`candidate_extra_content_verified=false`。至少區分 `accepted`、`not_low_text_source`、`strong_baseline`、`candidate_too_short`、`source_content_loss`、`numeric_loss`、`pair_mismatch`、`mechanic_loss`、`insufficient_source_evidence`。不存完整來源語句、重複 OCR 全文，也不宣稱新增 mechanics 已驗證。舊品質報告缺少欄位仍可讀；不改 DB、API、publication 或遊戲 schema。

原本的 `rich_ocr_validation` 與強制 Paddle 路徑已被**本設計取代**；rich candidate 專用 verifier dependency 已移除，原有獨立 Paddle/Tesseract 路徑維持不變。

## 實作獲批准後的測試與真實驗證

合成案例：(1) 有證據的獨立頁碼／running header／垂直碎字未出現在豐富角色卡候選仍接受；(2) 基線 `STR 60`、候選 `STR 60 / DEX 70 / LUCK 50` 不需 Paddle 即接受；(3) `STR 60` 變 `STR 50` 拒絕；(4) `Damage 1d6+2` 變 `1d6` 拒絕；(5) `SAN 1/1d6` 變 `1d6/1` 拒絕；(6) 候選漏掉短基線的實質 Keeper 句子拒絕；(7) 大於 200 字的正文維持原嚴格覆蓋；(8) 漏掉 `STR 60` 的長篇不相關文字拒絕；(9) 缺乏產物證據的短標題仍是必要來源；(10) 接受後保留 review history，且不標示新增內容已驗證；(11) 原有 Paddle/Tesseract 備援、numeric verification、圖片與 map trigger 不變。除了明確測候選過短的案例，合成候選另加中性表單內容達到 200 字門檻，讓各測試真正走到目標 gate。Fixture 僅用合成文字。

私有 43 頁《The Lightless Beacon》重播前先核對 SHA-256 `14437ca4eca3b6c05b22403bdad30bdb8668f1837e0a2d0ee42e49c160d8aa07`。對第 31、32、34、35、37、38、40、41 頁回報去原文的舊／新 method、字數、候選長度、基線強度、必要／保留項目數、衝突、決策及 review。另核對第 3、6、13、16–19、25、27–30 頁。不強求八頁全部升級：若 p31 仍被拒，要指明具體基線項目及它為何必要或可忽略，不能再加 OCR verifier。比較圖片、scene maps、MarkItDown／vision 呼叫、解析時間，以及 rich-candidate Paddle verifier 呼叫（預期 **0**）。若升級後自然退出 `pending`，沿用原流程；不要額外寫效能捷徑。實作後執行完整 pytest、ruff、mypy、compileall、diff-check。

## 待審核的取捨

弱基線是不完整證據，不是完整 ground truth。同頁較豐富的候選可以提升可用性，卻不能證明每個新增數值正確；保留現有 review warning 可讓不確定性可見。硬邊界是**不得與已觀察到的必要來源矛盾**，包括數值、骰式、SAN、pair 與實質正文。產物分類或來源保留有疑義時，不升級。

## 後續修正：直排字形與 mechanics 語法安全

只有字母屬於同一狹窄文字 block、該 block 沒有其他無關行、字型與尺寸相容、x 座標對齊、y 座標單調且字形間距受限時，才把直排字母重建為語意來源。不得跨 block、欄或 caption／body 區域拼接。只有同一 PDF text span 在至少三個不同頁面重複出現、位於外側邊界、使用與正文隔離且明顯較大的展示字型，且來源字形座標能綁到該 span 時，才可判為裝飾。書名、頁碼、具體字詞或特定字型名稱都不能單獨決定結果。證據不足時保留為必要／不明來源，拒絕 promotion。

先抽取完整 mechanics 語法，再做一般字詞標點正規化。骰式、獨立加減修正、有順序的 SAN 損失及百分比在來源比較時保留運算符。大小寫與空白可以正規化；`1d6+2` 與 `1d6-2`、`+2` 與 `-2`、`+10%` 與 `-10%`、`1/1d6` 與 `0/1d6` 絕不等價。一般敘述的標點仍沿用既有比較規則。Promotion 仍保留 review warning，也不會把候選新增內容宣稱為已驗證。
