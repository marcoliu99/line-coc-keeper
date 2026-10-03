# PyMuPDF4LLM 文字順序調查

## 目標與範圍

回答：已有 PyMuPDF4LLM，為什麼多欄順序仍會錯？在最新 main_v2 比較五頁的 PyMuPDF 原始文字、PyMuPDF4LLM 和程式最終選用文字。只有證明程式破壞正確 library 輸出才做最小修正；library 本身排錯就回報限制。

Baseline：`7d2cc8cab76d25aa1f47c47568ad2f9ff05d910b`，已撤回 PR155 多欄匯入改動。Python 3.14；PyMuPDF、PyMuPDF4LLM 均為 1.28.2，layout mode 啟用。requirements 沒有鎖定這個版本，結論限定於記錄環境。

不改 schema、匯入架構、依賴、Paddle、遊戲流程。不執行劇本匯入、重新解析、publication、index 或 production state 寫入。既有 source PDF 唯讀。應用程式比較使用空白 AI credentials、local OCR/AI repair budget=0。套件預設自動 OCR 與使用全新 document 的 `use_ocr=False` 結果分開保存，避免 OCR 對記憶體頁面的修改污染比較。

## 實際流程

```text
PDF -> PyMuPDF4LLM 全部提供頁面的 Markdown chunks
    -> 每頁 PyMuPDF 原生文字/blocks/words
    -> 覆蓋率、數字保留、幾何欄位配對驗證
    -> 採用 layout 或回退 native
    -> 有需要才執行有限局部修補 / AI repair
    -> 低文字量且有圖形才走既有 MarkItDown OCR / vision fallback
    -> unresolved 註記、頁碼標記 -> 完整 source text
```

擷取在 `app/pdf_loader.py`；native 雙欄 heuristic 與候選驗證在 `app/pdf_quality.py`。preview 的原生文字只作相似度預覽。`legacy_commands.py` 把完整輸出傳給 `scenario_library.save_scenario` 原樣儲存；`load_context` 選頁範圍，沒有重新排列段落。source authoring/review 是明確編修或頁面渲染，不會自動用 raw blocks 蓋掉正常擷取。

此 baseline 的 app 與 requirements 沒有 Paddle。已有 OCR 是 pytesseract/Tesseract、MarkItDown OCR adapter，及既有 provider 圖像分析/欄位修補。安裝的 PyMuPDF4LLM 自己預設也可啟用 Tesseract，因此不能宣稱目前完全只處理原生文字。

## 五頁結果

既有 source：`data/scenarios/the-haunting-scenario-trimmed-191b3b2b/source.pdf`，選 PDF 第 1、3、15 頁（印刷頁碼 17、19、31），加上單欄、跨欄標題三欄 synthetic 各一頁。每頁都有渲染圖與三種輸出。

| 頁面 | PyMuPDF 原始文字 | PyMuPDF4LLM / 最終程式文字 |
|---|---|---|
| 第 1 頁：大標題雙欄 | 左欄再右欄，但大標題在尾端 | 預設 OCR 混欄、重複標題；關閉 OCR 可恢復標題→左→右 |
| 第 3 頁：正常雙欄 | 完整左→完整右 | 右欄 LOCATION 3、Handout 4 提前於左欄 Handout 2；關閉 OCR 仍錯 |
| 第 15 頁：技能、法術、圖片 | 完整左→完整右 | 右欄法術提前於左欄 Armor、人物介紹；關閉 OCR 仍錯 |
| Synthetic 單欄 | 正常 | 正常 |
| Synthetic 跨欄標題三欄 | 刻意交錯的 content stream 導致橫列交錯 | 標題在前，正文仍橫列交錯；關閉 OCR 仍錯 |

五頁都選用 `method=layout`，最終輸出與 PyMuPDF4LLM 僅有空白差異。沒有重現 B/C 的覆寫問題，也沒有因為雙欄判斷而跳過某頁。這個版本使用 1-based `page_number`，映射正確；舊 `page` key 未被此版本實際使用，不做推測性相容修正。

分類：**A，並有 E 的版面複雜因素**。套件 `document_layout.parse_document` 先呼叫 `utils.find_reading_order`，再寫 Markdown。追蹤實際分群：

- 第 3 頁：26 個 body boxes 雖屬一條 stripe，仍被切成 `[9, 6, 1, 4, 6]` 五組，打斷左欄。
- 第 15 頁：22 個 body boxes 被分為 `[9, 13]` 兩條橫向 stripe，先讀上方左→右，再回到下方左；第二條把 13 個 boxes 合為一組。
- 三欄 fixture：九個正文項目被偵測成一個跨三欄的大 text box，後續已無獨立欄可恢復。

應用程式的覆蓋率/數字驗證不驗欄序，因此接受「文字完整、順序錯」的結果。這是驗證能力限制，並非後處理破壞正確結果的證據。關閉 OCR 只改善第 1 頁，無法修復其餘問題。本輪不提出 runtime 修正。

## 驗證與交付

永久 evidence：`/Users/marcoliu/workspace/pymupdf4llm-order-audit-20261003/`。

每頁包含 `page_XX_pymupdf.txt`、`page_XX_pymupdf4llm.md`、`page_XX_pymupdf4llm_native_only.md`、`page_XX_current_final.txt`、native candidate、PNG，另有 subset PDF、metadata/quality report、分群 trace、重現 script、獨立 pytest assertions。原 PDF SHA256：`de28127fe4978a32076a4a5099496c0a377b91408b2eb402151bd5151009fcb1`。

觀察介面為 `extract_text` 回傳文字與 library 直接輸出；預期順序依渲染版面建立，不以 import 成功作判準。

- 獨立 evidence assertions：**18 passed、4 failed**，四個欄序失敗如實保留，未標為 xfail。先跑 compare.py 再測保存的輸出；這些放在 repository CI 外，不是已新增到 CI 的 regression tests。
- 既有 PDF suite：**41 passed**。
- 既有 full suite：**1862 passed、1 skipped**，另有 **152 passed subtests**。
- 五頁未見 native 數字、骰子 token 或移除 Markdown 標記後的 native word token 遺失。token 保留不代表段落或語意正確。
- 單欄順序正常；沒有程式修正，因此不宣稱修正前後改善。
- 頁首頁尾仍保留；第 1 頁印刷頁碼與標題重複。第 3 頁左欄段落被插在右欄 Handout 4 下，雖然字沒少，段落歸屬已錯。

## 決策

PyMuPDF4LLM 在本次版面有明顯限制。依使用者的 A 類處理原則，程式保持不變，不增加工具或 fallback 架構。下一輪才決定是否評估既有 Paddle 版面分析，或狹窄的原生文字選項。本輪不執行兩者。未來若改應用程式，需另行審閱範圍並新增真正的 regression tests；隔離 evidence suite 已提供失敗案例。
