# PDF 多欄驗證 — 2026-09-30

[English](pdf_multicolumn_ingestion_validation.md)

## 本地來源保真樣本

使用玩家提供的本地 PDF，未把 PDF 或劇情段落加入 repo。[機器可讀結果](pdf_multicolumn_ingestion_corpus_results.json) 保存檔案 hash、實體頁碼、裁決數量及 spans 檢查。

| 劇本 | 黃金實體頁 | 舊順序 | 新順序 | 全本已接受／未解／其他 | 幾何掃描 |
| --- | --- | --- | --- | --- | --- |
| The Haunting | 14 | Extension → Walter Corbitt → Conclusion → Rewards | Conclusion → Rewards → Extension → Walter Corbitt | 14／1／12 | 0.096 秒 |
| Dead Boarder | 5 | Introduction → Keeper Considerations → Rules Notes → Time Limit | 維持正確 | 18／3／11 | 0.141 秒 |
| The Lightless Beacon | 6 | Introduction → Running the Game | 維持正確 | 15／2／26 | 0.129 秒 |

三個黃金頁與 47 個幾何接受頁的原生單字及數字／骰式 multiset 完整保留，word recall 1.0。Unicode source-unit 範圍可往返、連續且完整涵蓋輸出，頁碼偏移亦相符。這證明來源保真，不代表所有段落位置都經語意驗證。陰宅舊 layout 候選雖通過只檢查遺失的 selector，仍新增了數字 token。

整本只做原生幾何掃描，沒有跑全本 OCR／地圖匯入，不代表整本可發布。未解頁：陰宅 11；Dead Boarder 12、19、20；Beacon 3、13，皆為 `unsupported_spanning_region`。其他頁保留原路徑。

黃金頁 parser 耗時 0.329／0.160／0.196 秒；額外幾何及候選檢查 0.006／0.070／0.004 秒。這只是一次小樣本量測，不是吞吐量結論。

## 真實圖片排序 API

使用 main_v2 的 OpenAI 分析設定（`OPENAI_MODEL=gpt-6-luna`），資料庫與資料／log 路徑隔離。每次送一頁原 PDF 圖片及有限原 block 摘錄，沒有玩家遊戲狀態。adapter 要求完整排列及合理幾何位置，只組合原文字。

| 劇本／頁 | block 數 | 結果 | 呼叫 | 耗時 |
| --- | --- | --- | --- | --- |
| 陰宅 11 | 13 | 接受排序 | 1 | 5.996 秒 |
| Dead Boarder 12 | 13 | 接受排序 | 1 | 4.025 秒 |
| Beacon 13 | 18 | 接受排序 | 1 | 12.707 秒 |

[API 結果及預算](pdf_multicolumn_ingestion_api_results.json)。三次均通過 deterministic 排序驗證，不代表整本或所有疑難版面皆正確。較早一次 sandbox 網路受限沒有得到有效回應，不列為成功 API 證據；失敗沒有被默默接受。

## 尚未量測的範圍

- 複雜表格、掃描雙欄、重建 OCR、逐區擷取不屬於首版。原路徑會標示，不宣稱新增多欄驗證。
- 頁數／請求上限採保守預設；續跑保留累計用量，明確提高設定才能增加額度。
- 一般遊戲回合沒有增加 LLM 呼叫；新增分析只在匯入發生。

Docling 選項與離線模型路徑依其[官方 pipeline 定義](https://github.com/docling-project/docling/blob/main/docling/datamodel/pipeline_options.py)核對。

## 審查處理

### Standards

審查指出一項明文要求：封閉版面值應有型別。版面紀錄加入 Literal／TypedDict；身分檢查集中在草稿擁有者的公開 lease 介面。既有五值擷取契約維持下游相容；選用 worker transport 保留在匯入 adapter，避免另外抽空泛介面。

### Spec

審查發現三項缺口：失敗圖像仍可發布、續跑額度耗盡沒有補足方式、快取少了實際 parser／renderer 身分。失敗圖像改為阻擋；請求累計持久保存，明確提高設定才能補額，頁數按不同頁計算；來源與 parser／renderer 身分改變會使快取失效。額外以匯入認領測試並行新上傳及取消。每次匯入內排序逐頁執行，另記子程序處理耗時；沒有新增 adapter 准入佇列。

最終本地檢查：pytest 1627 通過、2 略過、152 subtests 通過；Ruff 0.16.8、mypy（117 個 app 檔案）及 compileall 通過。

## Docling 實際模型驗證

已使用 Docling 2.131.0、docling-core 2.99.0 與 Python 3.14.1 實際轉換九個來源頁，九頁轉換成功；六個排序候選通過，三個因來源交錯或重疊拒絕，保留後備。這不是整本品質保證。暖機後轉換約 0.27–0.33 秒；首頁 2.71 秒。實際子程序三頁耗時 5.457、4.073、3.812 秒，全部來源詞集合相同，未呼叫分析 API。

視覺核對陰宅第 11 頁發現 Docling 延後左欄註記；adapter 依來源欄距與跨欄區段校正，註記仍置於右欄前。僅排序原生 block，不採用模型改寫文字。依賴固定版本、模型離線載入；先用遊戲環境的 Python 執行 `python scripts/setup_pdf_layout.py`，之後疑難頁預設使用 Docling。模型缺少或逾時仍有後備，不在匯入時下載。

[逐頁結果、模型摘要與子程序量測](pdf_multicolumn_ingestion_docling_results.json)。
