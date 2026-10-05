# 回合階段計時與檢索放大

[English](turn_latency_instrumentation_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與目標

分類：`enhancement`。狀態：**已實作（計時與沿用；延遲未量測）**。來源：Camp Sunny 五人 500 回合驗證的 CS-006（Workstream E）。疊在 Workstream B、C 的變更之上（`fix/camp-sunny-event-obligations`）。

五人同時發言時回覆最長 137.8 秒（p95 120.8 秒），router 佇列最長等了 106.5 秒；500 個一般回合共做了 935 次劇本搜尋（平均 1.87 次）。規格要求先量測再最佳化。這次變更加上量測工具，並且只做不需要量測也安全的減量。

## 契約

1. **每回合一條階段時間軸**（`app/services/turn_phases.py`）。每個回合、每次擲骰後的接續、每次記憶維護都會回報一個 `turn.phases` 事件，以及每段一個 `turn.phase` 事件（DEBUG），帶 `turn_id`、`player_id`、`campaign_id`、開始、結束與時長。階段：`queue_wait`、`initial_retrieval`、`executor_llm`、`tool_execution`、`recovery_retrieval`、`continuation_processing`、`narrator_llm`、`memory_search`、`memory_write`、`embedding`、`other`。各段在工作實際發生的地方記錄（router 的鎖等待、回合排隊時就已跑完的預取、Executor 與 Narrator 的模型呼叫、工具執行、每個 embedding 批次、復原搜尋、記憶提交），並會跟著回合進入 worker thread。
2. **不重複計算。** 階段會重疊（工具在 Executor 的模型呼叫裡執行；預取在回合排隊時就在跑）。所以摘要對每個階段回報它自己合併後的區間（`total_ms`）與只有它能解釋的時間（`exclusive_ms`；內層的工作優先於包住它的外層等待），另有 `wall_ms`、`other` 與 `overlap_ms`。各階段的獨占時間加起來等於牆鐘時間。
3. **擲骰後的接續沿用它那個行動的證據**（`context_builder.remember_grounding`／`reusable_grounding`，開關 `RETRIEVAL_REUSE_FOR_FOLLOWUPS`）。行動回合成功的劇本搜尋，依對話、時間線與玩家保存，交給接續使用，不再為同一個場景再搜一次；除非超過 `RETRIEVAL_REUSE_TTL_SECONDS`（900 秒）、之後這個對話裡有別的回合搜尋過、或搜尋所依據的任何東西改變（劇本、章節範圍、摘要、記憶、時間線、戰鬥狀態、角色）。一次擲骰加接續從兩次主動劇本搜尋降為一次；`rag.followup_grounding` 回報是否沿用。
4. **有界的搜尋迴圈。** Executor 在一個回合最多呼叫劇本搜尋工具 `SCENARIO_SEARCH_MAX_PER_TURN`（5）次；再呼叫就會被以 `scenario_search_limit_reached` 拒絕，並記錄 `executor.scenario_search.limit_exceeded`。

## 未改動

主動搜尋取得的證據本來就由 Executor 與 Narrator 共用，預取也本來就在拿鎖之前執行；兩者都保留。縮小鎖範圍（E5）沒有推進：沒有量測顯示鎖到底占了哪些時間，就去縮小一個保護狀態提交的序列化，只是猜測。每回合相同查詢的快取（E2）也沒有加：重複的查詢會得到「已提供過的片段」，把第一次的答案重放，會把該回合已經有的內容再送一次。

## 執行方式

`tests/test_turn_phases.py`：計時（巢狀、空檔、合併區間、裁切、未知階段）、有無時間軸時的記錄、從 worker thread 與並行記錄、回報失敗不會讓回合失敗、預先帶入的排隊與預取、接續標籤、router 記錄排隊時間、每個沿用條件（失敗的搜尋、其他回合、其他玩家、劇本／摘要／記憶改變、過期、容量上限）、supervisor 記住與沿用、開關，以及搜尋上限。

## 未涵蓋

**沒有量測任何延遲。** 驗收目標（沒有超過 120 秒的同時回覆；p50 與 p95 相對 26.0 秒與 120.8 秒改善 20%）需要五人的真實執行，本環境無法執行（沒有 Codex 登入、沒有 Discord）。現在有的是那次執行必須使用的量測工具，以及兩項減量；第一次真實執行應先讀 `turn.phases`，再決定要調什麼。
