# 對話 session 與 embedding 執行

[English](conversation_session_embedding_execution_design_spec.md)

狀態：已實作。基準：`main_v2` 的 `07d55a7`。

## 重新核對

provider registry 已統一查詢，但 Assistant 自行管理 OpenAI 延續與時間線重置；Executor 與 Narrator 各自協商動態工具、階段及決策情境。各 SDK 的工具迴圈確有差異，保留在 adapter。劇本與記憶 RAG 仍重複建立／關閉 embedding client、排序結果及偵測不完整批次。跨 RAG 查詢共用快取已存在，予以保留。

## 介面

`app/providers/conversation_session.py` 在呼叫時選 provider，提供模型與能力、只產生支援的階段參數，並管理 OpenAI 延續識別、重置與回呼。agent 保留遊戲工具篩選、回饋、提示及 Executor 不強制收尾的政策。更正情境與時間線不符時重置延續；其他 adapter 不收到 OpenAI 專用參數。

`app/embedding_execution.py` 以單一同步 client 執行完整且依輸入排序的分批 embedding，設定逾時、不讓 SDK 自行重試，並在每條路徑關閉。缺 key、呼叫失敗、索引無效／重複／越界、批次不完整時回傳 `None`，由各 RAG 模組套用既有詞面備援。排名、相關性門檻、章節政策及雙語證據留在 RAG；共用查詢快取繼續避免重複查詢 embedding。

## 測試

用假 provider 驗證能力參數篩選、時間線與更正重置。測排序結果、失敗／不完整批次、關閉失敗、共用查詢重用與詞面備援。執行完整 CI 檢查。
