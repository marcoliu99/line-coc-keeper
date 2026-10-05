# 有界的長期記憶 embedding

[English](memory_embedding_bounds_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與目標

分類：`bug`。狀態：**已實作**。來源：Camp Sunny 五人 500 回合驗證的 CS-005（Workstream D）。基於 `main_v2` 的 `aa79f22`。

執行結束時八個長期記憶 chunk 全都沒有 embedding，而 BM25 讓檢索持續可用，沒有人注意到。其中一個 chunk 約 18,400 字元（約 19,000 token）。當時沒有擷取 provider 的拒絕內容，所以「過長」是可能原因，並非已證實的唯一原因。程式碼裡確定的一點是：每次裁切 log 都剛好變成一個 chunk、一個 embedding 輸入，不論多長；而 embedding 失敗後既沒有診斷，也沒有第二次嘗試。

## 契約

1. **送出前先有界。** 一次裁切的內容依序打包成多個 part，每個 part 不超過 `MEMORY_EMBEDDING_MAX_TOKENS`（預設 6000；預設模型約可接受 8k），以 embedding 模型的 tokenizer 計量而非字元數（`app/memory_chunking.py`）。比一個 part 還長的訊息先在句尾切，再依量測長度切。放得下的裁切仍是一個 chunk，儲存方式與以前完全相同。
2. **語意保留。** 同一次裁切的各 part 以有序子項儲存、掛在同一個 parent 下（`parent_id`、`part_index`、`part_count`），與 log 裁切在同一個交易內，各有自己的向量、時間線與 revision，以及各自所含訊息的來源資訊。重播提交會找到 parent 而不再儲存。搜尋時，同一則記憶的各 part 會合併成一筆結果，依序排列，分數取最佳 part，不會被當成彼此獨立的事實；合併後的結果帶有被併入的每個 part 的更正（`superseded_by`）與訊息來源。
3. **失敗會被診斷，但不引用原文。** `rag.embedding_fallback` 現在帶有 provider、操作、輸入數量與大小（bytes，是 token 的上界）、狀態類別與代碼、provider 的錯誤代碼與類型、重試是否可能有用，以及選用的 fallback。provider 的訊息文字（可能回顯憑證）不會被記錄。同樣的診斷也存在該 chunk 上。
4. **只剩詞彙檢索不是穩定狀態。** 每次搜尋遇到缺向量的 chunk 都記錄 `memory.embedding_gap`。每次維護先給最多 `MEMORY_EMBEDDING_BACKFILL_LIMIT`（4）個這類 chunk 再一次機會，每個最多 `MEMORY_EMBEDDING_MAX_ATTEMPTS`（3）次，被重試也無法改變的拒絕（408／409／429 以外的 4xx，或額度用盡）之後不再嘗試。沒有設定 embedding 金鑰是設定問題而不是拒絕：不算一次嘗試，所以金鑰設定好之前存下的記憶，在金鑰設好之後會被補上向量。在 part 出現之前就存下的、太長無法 embedding 的 chunk，會在這次重試中就地切開。工作在狀態鎖之外進行，結果在鎖內套用到當時的 chunk，更正註記與附加記憶也取同一把鎖，所以彼此不會蓋掉對方。
5. **BM25 保留。** 沒有向量的 chunk 仍可用關鍵字找到，之後成功一次就恢復向量檢索。

## 保持不變的契約

放得下的 chunk 的儲存形狀、搜尋介面、裁切的冪等性，以及 BM25／cosine 的混合都沒有改。既有的記憶不需遷移：缺口由重試補上。

## 執行方式

`tests/test_memory_embedding_bounds.py`：打包（順序、不遺失、來源資訊、句尾切、以 token 而非字元計量）；超過上限的內容不會送給 provider；part 可追溯且重播安全；合併後的搜尋結果；各種 provider 狀態分類且不洩漏訊息；之後成功一次就恢復向量檢索；拒絕不重試、可重試的失敗最多重試到上限；每輪上限；切開舊的 chunk；不蓋掉同時發生的變更；維護不會存下單一無界 chunk。

## 未涵蓋

沒有發出真實的 embedding 呼叫，所以真實 provider 是否因長度拒絕了那個 19k token 的 chunk 仍未確認；不論如何，這個上限都排除了該原因。把一則訊息切到兩個 part，可能讓 `mark_superseded_receipt` 找不到跨在切點上的更正片段。舊 chunk 在重試時被切開，各子項會拿到整個 chunk 的來源清單。
