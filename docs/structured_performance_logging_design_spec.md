# 結構化效能與請求 Log 設計規格

## 0. 文件狀態與工作基線

- 狀態：Draft，等待規格審查。
- 工作 branch：`feature/structured-performance-logging`
- 整合 branch：`main_v2`
- 本功能 branch 建立時的 `origin/main_v2`：`4e9532f`
- 本文件階段不實作 runtime code；只有規格書會被提交。
- 之後若 `main_v2` 有新 commit，開始實作前必須重新 fetch 並對齊。

## 1. 問題與目標

目前專案各模組雖然已使用 Python `logging`，但缺少一致的 timestamp、請求識別碼、事件名稱與耗時欄位。當一次 Discord 訊息回應很慢時，現有 log 無法可靠回答以下問題：

1. Discord gateway 收到事件本身是否延遲？
2. 是否卡在 conversation lock？
3. 是否卡在 state load/save 或其他本機程式碼？
4. 是否卡在 BM25、embedding 或 RAG index 建立？
5. 是否是 AI API 等待時間？
6. AI 是否進行多輪 tool call／retry？
7. AI 的 input、cached input、output、reasoning token 是否增加了成本？
8. 玩家已收到回應後，背景 maintenance 是否仍在佔用資源？

本功能目標是建立一套可由 timestamp 與 correlation ID 串起來的結構化效能 log，讓管理者能從單次請求與聚合 log 兩個層次定位瓶頸。

## 2. 範圍

### 2.1 包含

- 統一的 UTC timestamp 與單調時鐘 elapsed time。
- 每次 Discord 請求的 `request_id` 與遊戲回合的 `turn_id`。
- 從 Discord 收到訊息到回覆完成的完整 lifecycle。
- lock、state、router、RAG、embedding、AI、tool、DB、Discord send 的分段耗時。
- OpenAI Responses API 可取得的 model、reasoning effort、token、cache 與 retry 資訊。
- 其他 provider 的共同欄位，以及 provider-specific 欄位可選填。
- 背景 post-turn maintenance 的獨立 lifecycle。
- JSON Lines（JSONL）輸出與可讀的 console formatter。
- slow request 與失敗事件的明確標記。
- 測試、文件與 `.env.example` 的觀測設定說明。

### 2.2 不包含

- 不記錄完整 user message、完整 prompt、完整 AI response 或劇本全文。
- 不記錄 API key、authorization header、Discord token 或其他 secret。
- 不在本功能內建立 dashboard、ELK、Grafana、Prometheus 或外部 SaaS 上傳。
- 不改變 AI provider 的選擇、prompt、RAG ranking、tool 行為或遊戲規則。
- 不把 log 當成 audit trail；遊戲 state 與 checkpoint 仍由既有 persistence 機制負責。
- 不在第一版承諾跨 process 的 distributed tracing；`request_id` 只需在目前 bot process 內可串接。

## 3. 設計原則

1. **先能定位，再追求統計**：每個慢請求必須可以由一個 `request_id` 找回完整事件序列。
2. **wall-clock 與 elapsed 分離**：timestamp 用 UTC wall-clock；耗時用 `time.perf_counter()`，避免系統時間調整影響 duration。
3. **不洩漏內容**：只記錄 metadata、長度、token usage 與錯誤類型，不記錄遊戲秘密或 prompt 內容。
4. **不阻塞主流程**：logging handler 不應因為寫檔或格式化而長時間阻塞 Discord event loop。
5. **失敗可降級**：usage metadata 取不到時仍記錄 request completion；觀測失敗不可讓遊戲請求失敗。
6. **低 cardinality event names**：事件名稱固定，細節放在欄位，不把 user text 或動態內容拼進 event name。
7. **相容既有 logging**：保留既有 logger 名稱與 exception stack trace；新增欄位不可破壞 `assertLogs` 測試。

## 4. Runtime flow

```text
Discord gateway event
  │
  ├─ request.started
  │    ├─ message metadata / request_id
  │    ├─ lock.wait.started ── lock.wait.completed
  │    ├─ state.load.started ── state.load.completed
  │    ├─ router.started ── router.completed
  │    │    ├─ rag.search.started ── rag.search.completed
  │    │    ├─ memory.search.started ── memory.search.completed
  │    │    ├─ llm.request.started
  │    │    │    ├─ llm.tool.started ── llm.tool.completed (0..N)
  │    │    │    └─ llm.request.completed (0..N retries/rounds)
  │    │    └─ state.save.started ── state.save.completed
  │    ├─ discord.reply.started ── discord.reply.completed
  │    └─ request.completed
  │
  └─ background maintenance（獨立 maintenance_id，不算入 reply latency）
       ├─ maintenance.started
       ├─ summary.started ── summary.completed
       ├─ embedding.started ── embedding.completed
       ├─ state.save.started ── state.save.completed
       └─ maintenance.completed
```

`request.completed.duration_ms` 是使用者可感知的完整處理時間；background maintenance 必須用自己的 `maintenance_id` 記錄，不能把它誤加到玩家回覆 latency。

### 4.1 觀測 channel 與 fast path

```text
任何 app event／developer logger call
  │
  ├─ Structured performance event？
  │    │
  │    ├─ LOG_ENABLED=false
  │    │    └─ no-op fast path
  │    │         ├─ 不建立 event payload
  │    │         ├─ 不啟動 timer
  │    │         ├─ 不計算 token／bytes／hash
  │    │         └─ 不寫入任何 performance output
  │    │
  │    └─ LOG_ENABLED=true
  │         ├─ 取得 request／turn／maintenance context
  │         ├─ 建立固定 schema event
  │         ├─ 計算 duration／usage／統計欄位
  │         └─ 交給 LOG_LEVEL filter 與 formatter
  │
  └─ Developer text log？
       │
       ├─ LOG_TEXT_ENABLED=false
       │    └─ no-op fast path，不格式化 message
       │
       └─ LOG_TEXT_ENABLED=true
            ├─ 保留 developer 提供的文字 message
            ├─ 加入共用 timestamp／context
            └─ 交給 LOG_LEVEL filter 與 formatter
```

兩個 channel 在 filter／formatter 前可以共用同一個 handler，但 payload 建立前必須先檢查各自的 toggle。這是為了確保關閉 structured performance logging 時，不會仍然付出 timer、metrics、hash 與 JSON 欄位建立成本。

### 4.2 Request、AI 與背景任務的關係

```text
Discord request_id
  │
  ├─ turn_id
  │    ├─ RAG／embedding events
  │    ├─ llm.request events
  │    └─ llm.tool events
  │
  ├─ state／reply events
  │
  └─ request.completed
       │
       └─ detached maintenance_id
            ├─ summary events
            ├─ embedding events
            ├─ state.save event
            └─ maintenance.completed
```

`request_id` 是 Discord ingress 的生命週期；`turn_id` 是 AI／agent turn 的生命週期；`maintenance_id` 是回覆送出後背景工作的生命週期。三者不可共用同一個 ID，也不可把 background maintenance 的耗時加到 request latency。

## 5. Correlation ID

### 5.1 必要識別碼

| 欄位 | 產生時機 | 用途 |
|---|---|---|
| `request_id` | Discord `on_message` 一進入時 | 串起單次 Discord event 的所有同步／非同步事件 |
| `turn_id` | 開始遊戲處理時 | 串起一次 Keeper／agent turn；可與 request 不同 |
| `maintenance_id` | 建立背景 maintenance task 時 | 串起 post-turn summary／embedding／save |
| `conversation_id` | 既有欄位 | 識別遊戲房間，但不應直接暴露敏感 Discord identity |

`request_id`、`turn_id`、`maintenance_id` 使用 UUID 或等價的不可猜測 ID。所有子模組收到 context 後沿用，不自行產生新的 request ID。

### 5.2 身分與隱私

- `conversation_id` 與 `user_id` 是否輸出明文，需由設定控制；預設使用穩定 hash 或已遮罩值。
- 不記錄 display name、訊息原文、劇本原文、角色秘密、DM 內容。
- 例外訊息可能含第三方 SDK 內容，必要時需做 secret redaction；禁止直接把 request payload 放入 exception log。

## 6. Log record schema

每筆 JSONL record 至少包含：

```json
{
  "timestamp": "2026-09-20T12:34:56.123Z",
  "level": "INFO",
  "logger": "app.observability",
  "event": "llm.request.completed",
  "request_id": "req_...",
  "turn_id": "turn_...",
  "conversation_id": "conversation_...",
  "duration_ms": 4280.4,
  "status": "success"
}
```

### 6.1 共通欄位

| 欄位 | 型別 | 說明 |
|---|---|---|
| `timestamp` | string | UTC ISO-8601，含毫秒與 `Z` |
| `level` | string | `DEBUG`／`INFO`／`WARNING`／`ERROR` |
| `logger` | string | Python logger name |
| `event` | string | 固定事件名稱 |
| `request_id` | string | 請求 correlation ID；背景任務可為 null |
| `turn_id` | string | 回合 correlation ID；非遊戲事件可為 null |
| `maintenance_id` | string | 背景 maintenance correlation ID；非 maintenance 可為 null |
| `conversation_id` | string | 遮罩後的 conversation identifier |
| `duration_ms` | number | 完成事件的 elapsed duration |
| `status` | string | `started`／`success`／`error`／`timeout`／`skipped` |
| `error_type` | string | 失敗時的 exception class 或受控錯誤分類 |
| `error_message` | string | 清理後的短錯誤訊息；不得包含 prompt／secret |
| `slow` | boolean | 超過 `LOG_SLOW_REQUEST_MS` 時標記 |

### 6.2 Discord 欄位

- `platform`: 固定為 `discord`。
- `channel_type`: `guild`／`dm`／`unknown`。
- `message_kind`: `command`／`attachment`／`plain_text`／`button`／`unknown`。
- `command_name`: 只記 `/coc` 的 command 名稱，不記 arguments 原文。
- `attachment_count`、`attachment_bytes`。
- `reply_message_count`、`reply_bytes`、`reply_chunk_count`。

### 6.3 AI 欄位

- `provider`: `openai`／`anthropic`／`gemini`。
- `model`。
- `reasoning_effort`：若 provider 支援且有設定才輸出。
- `api_operation`: `responses.create`／其他 provider operation。
- `iteration`、`tool_call_count`、`retry_count`。
- `input_tokens`、`cached_input_tokens`、`output_tokens`、`reasoning_tokens`：API 有回傳才填入。
- `cache_hit_rate`：只在 input token 與 cached token 都可取得時計算，不自行猜測。
- `previous_response_id_used`：只記 boolean，不記完整 ID。

AI request 的 duration 只代表 provider request 的 wall time；不應把 Discord reply 或 DB save 算進去。

### 6.4 RAG／embedding 欄位

- `rag_kind`: `scenario`／`memory`。
- `embedding_model`。
- `embedding_weight`。
- `top_k`。
- `index_cache`: `memory`／`disk`／`rebuilt`／`miss`。
- `candidate_count`、`result_count`、`has_embeddings`。
- `embedding_batch_count`、`embedding_input_count`。
- 不記錄 query 原文；最多記錄 query 字元數與 token 數（若可取得）。

## 7. 事件目錄

### 7.1 Request lifecycle

- `request.started`
- `request.completed`
- `request.failed`
- `lock.wait.started`
- `lock.wait.completed`
- `router.started`
- `router.completed`

### 7.2 State／DB

- `state.load.started`／`state.load.completed`
- `state.save.started`／`state.save.completed`
- `checkpoint.started`／`checkpoint.completed`

DB event 必須記錄 operation、table／logical store、是否命中 cache 與 row／blob size，但不記錄 state JSON 內容。

### 7.3 RAG／embedding

- `rag.index.started`／`rag.index.completed`
- `rag.search.started`／`rag.search.completed`
- `embedding.batch.started`／`embedding.batch.completed`
- `memory.search.started`／`memory.search.completed`

index rebuild 與 query-time search 必須分開，避免把第一次建立劇本 index 的成本誤認成每回合搜尋成本。

### 7.4 AI／tool

- `llm.turn.started`／`llm.turn.completed`
- `llm.request.started`／`llm.request.completed`
- `llm.retry`
- `llm.tool.started`／`llm.tool.completed`
- `llm.failed`

每個 provider adapter 都應輸出共同 schema；provider 不支援的 usage 欄位為 null，不得用估算值冒充實際 usage。

### 7.5 Discord output

- `discord.reply.started`
- `discord.reply.chunk.completed`
- `discord.reply.completed`
- `discord.reply.failed`

如果一次回覆被切成多則 Discord message，總事件記錄總數與總 bytes；只有在需要定位單一 chunk 失敗時才記錄 chunk index。

### 7.6 Background maintenance

- `maintenance.started`
- `maintenance.summary.completed`
- `maintenance.embedding.completed`
- `maintenance.completed`
- `maintenance.failed`

Maintenance log 必須含 `trigger`（例如 `post_turn`、`manual`）與 `detached=true`，明確表示它不在玩家 reply critical path。

## 8. 輸出與設定

### 8.1 Formatter

支援兩種輸出：

1. `json`：正式環境與機器查詢使用，每行一個 JSON object。
2. `text`：本機開發使用，仍必須包含 timestamp、level、event、request_id、duration。

預設輸出到 stderr／既有 process handler；若設定 `LOG_FILE`，使用 rotating file handler，避免無限長大。檔案 rotation、保留數量與最大大小由設定控制。

### 8.2 建議設定

```env
LOG_ENABLED=true
LOG_TEXT_ENABLED=true
LOG_LEVEL=INFO
LOG_FORMAT=json
LOG_FILE=logs/coc-bot.jsonl
LOG_SLOW_REQUEST_MS=3000
LOG_SLOW_OPERATION_MS=1000
LOG_HASH_IDENTIFIERS=true
LOG_INCLUDE_USAGE=true
```

本系統提供兩種可以同時使用的 log channel：

1. **Structured performance channel**：由 `observe.event()`／`observe.span()` 產生，記錄固定 schema、duration、token、cache 與效能欄位。
2. **Developer text channel**：由標準 Python logger 的 `logger.debug()`／`logger.info()`／`logger.warning()`／`logger.error()` 產生，讓其他 developer 記錄一般文字診斷訊息。

兩個 channel 共用 timestamp、logger name、request context、handler 與 `LOG_LEVEL`，但由不同 toggle 控制。這樣 `LOG_ENABLED=false` 不會阻止 developer 暫時開啟文字 debug。

`LOG_ENABLED` 是 structured performance channel 的總開關，`LOG_TEXT_ENABLED` 是 developer text channel 的總開關，`LOG_LEVEL` 是兩者啟用後的共同 level filter。這三者必須分開，因為降低 level 仍可能有 context 建立、timer、欄位計算與 formatter overhead。

#### `LOG_ENABLED` 行為

| 設定 | 行為 |
|---|---|
| `true` | 建立 correlation context、計時、輸出符合 `LOG_LEVEL` 的 structured events |
| `false` | structured 觀測 API 走 fast path：不建立 event dict、不計算 token／bytes／chunk 統計、不執行 hash／redaction；不影響 developer text channel |

`LOG_TEXT_ENABLED` 行為：

| 設定 | 行為 |
|---|---|
| `true` | 保留標準 logger 的文字 debug／info／warning／error，並套用 `LOG_LEVEL` |
| `false` | developer text channel 走 no-op fast path；不格式化訊息、不建立 file output handler |

`LOG_ENABLED=false` 時，不能因為 structured logging 而改變遊戲流程或增加額外 async task。`LOG_TEXT_ENABLED=false` 時，developer 的 debug 文字也不得被格式化或寫出。必要的 exception logging 是否保留由既有錯誤處理負責，但不得為了效能觀測再次建立完整的觀測 payload。

若要完全關閉兩種 log channel：

```env
LOG_ENABLED=false
LOG_TEXT_ENABLED=false
```

若 developer 只需要文字 debug，不需要效能 timing：

```env
LOG_ENABLED=false
LOG_TEXT_ENABLED=true
LOG_LEVEL=DEBUG
```

若需要完整效能觀測，也需要保留文字 debug：

```env
LOG_ENABLED=true
LOG_TEXT_ENABLED=true
LOG_LEVEL=INFO
```

`LOG_LEVEL` 是控制 log 資料量的主要開關，必須由環境變數讀取，並套用到所有 app logger：

| Level | 保留內容 | 適用情境 |
|---|---|---|
| `DEBUG` | 所有 request、lock、cache、tool、chunk 細節 | 短時間深度排查 |
| `INFO` | 完整 request／AI／RAG／DB／Discord timing 摘要 | 效能觀測建議值 |
| `WARNING` | slow request、fallback、retry、cache 異常與錯誤 | 正式環境低噪音模式 |
| `ERROR` | 失敗與 exception | 只關注錯誤 |

效能 log 的事件 level 必須固定，不可因為資料量需求而由呼叫端隨意改變：

- 正常 lifecycle 的 started／completed timing 使用 `INFO`。
- 超過 `LOG_SLOW_REQUEST_MS` 或 `LOG_SLOW_OPERATION_MS` 的事件額外標記 `slow=true`，但仍保留原本的事件資料。
- fallback、provider retry、cache 異常使用 `WARNING`。
- 未處理失敗使用 `ERROR`。

因此 `LOG_LEVEL=WARNING` 會隱藏正常成功請求的 timing；若要觀察完整 latency，必須使用 `LOG_LEVEL=INFO`。若只想降低資料量但仍保留慢請求，使用 `WARNING` 搭配合理的 slow threshold。

設定解析必須有安全預設值；不合法的 level、duration 或 boolean 不得讓 bot 啟動失敗，應 fallback 到 `INFO` 或對應的安全預設值。

`LOG_ENABLED` 的預設值建議為 `false`，以確保沒有明確開啟觀測時不增加 production latency。`LOG_TEXT_ENABLED` 的預設值建議為 `true`，保留既有 Python logger 的錯誤與基本診斷能力；若正式環境要求完全靜默，再明確設為 `false`。

需要效能分析時明確設定：

```env
LOG_ENABLED=true
LOG_TEXT_ENABLED=true
LOG_LEVEL=INFO
```

### 8.3 Log level policy

- `DEBUG`：單次 tool／lock／cache 詳細資料，本機診斷使用。
- `INFO`：request、AI、RAG、maintenance 的開始／完成摘要。
- `WARNING`：slow、fallback、cache miss 異常、provider unsupported parameter。
- `ERROR`：請求失敗、回覆失敗、不可恢復 persistence error。

Developer 文字 log 與 structured performance event 都必須通過同一個 `LOG_LEVEL` filter，但內容格式不同：

```python
logger.debug("scenario cache key=%s", cache_key)
```

只產生一般文字診斷訊息；

```python
with observe.span("rag.search", rag_kind="scenario"):
    ...
```

產生固定欄位的 structured timing event。兩者可以在同一個 request 中同時出現，並使用相同的 `request_id`／`turn_id`。

### 8.4 INFO／WARNING 事件與資料量定義

`INFO` 的目標是保留一份足以計算正常 latency baseline 的摘要，不記錄每個 token、每個 chunk 或完整 payload。`WARNING` 的目標是讓低噪音 production log 仍能找出需要處理的異常，因此必須攜帶定位問題所需的數值。

#### INFO：正常效能摘要

| Event | 必要欄位 | 說明 |
|---|---|---|
| `request.started` | `request_id`, `conversation_id`, `message_kind`, `command_name`, `attachment_count` | 收到請求；不含訊息原文 |
| `request.completed` | `request_id`, `duration_ms`, `status`, `reply_message_count`, `reply_bytes` | 玩家可感知的總耗時 |
| `lock.wait.completed` | `request_id`, `duration_ms`, `lock_name`, `acquired` | 正常等待時間；長等待另升級為 WARNING |
| `state.load.completed` | `request_id`, `duration_ms`, `operation`, `state_size_bytes`, `cache_hit` | state 載入耗時與大小 |
| `state.save.completed` | `request_id`, `duration_ms`, `operation`, `state_size_bytes` | state 儲存耗時與大小 |
| `rag.search.completed` | `turn_id`, `duration_ms`, `rag_kind`, `top_k`, `candidate_count`, `result_count`, `index_cache`, `has_embeddings` | Scenario／Memory RAG 摘要 |
| `embedding.batch.completed` | `turn_id` 或 `maintenance_id`, `duration_ms`, `embedding_model`, `batch_size`, `batch_index`, `batch_count` | embedding API 批次耗時；不含文字內容 |
| `llm.turn.completed` | `turn_id`, `duration_ms`, `provider`, `model`, `reasoning_effort`, `iteration_count`, `tool_call_count`, `retry_count` | 一次完整 AI turn 摘要 |
| `llm.request.completed` | `turn_id`, `duration_ms`, `provider`, `model`, `iteration`, `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_tokens` | 單次 provider request；usage 不可取得時填 null |
| `llm.tool.completed` | `turn_id`, `duration_ms`, `tool_name`, `status` | 只記 tool name 與耗時，不記 arguments／result 原文 |
| `discord.reply.completed` | `request_id`, `duration_ms`, `reply_message_count`, `reply_bytes`, `reply_chunk_count` | Discord 發送回覆耗時 |
| `maintenance.completed` | `maintenance_id`, `duration_ms`, `trigger`, `summary_updated`, `embedding_updated`, `state_saved` | 背景 maintenance 總結 |

`INFO` 不應每次輸出以下高頻細節：

- 每一個 BM25 token 或候選 chunk 的分數。
- 完整 prompt、response、tool arguments、tool result。
- 每個 Discord message chunk 的完整內容。
- 每個 DB row／SQLite JSON blob 的內容。

#### WARNING：需要注意的異常或退化

| Condition／Event | 必要欄位 | 觸發規則 |
|---|---|---|
| `request.completed` with `slow=true` | 共用 request 欄位、`duration_ms`, `slow_threshold_ms`, `slow_stage` | 總耗時 >= `LOG_SLOW_REQUEST_MS`；同一筆完成事件 level 改為 WARNING |
| `lock.wait.completed` with `slow=true` | `request_id`, `duration_ms`, `lock_name`, `lock_threshold_ms` | lock 等待 >= `LOG_SLOW_OPERATION_MS` |
| `llm.request.completed` with `slow=true` | `turn_id`, `provider`, `model`, `duration_ms`, `iteration`, `tool_call_count` | 單次 provider request >= operation threshold |
| `llm.retry` | `turn_id`, `provider`, `model`, `retry_count`, `removed_parameter`, `error_type` | provider 拒絕 optional parameter 或發生可重試錯誤 |
| `llm.fallback` | `turn_id`, `provider`, `fallback_kind`, `reason` | previous response、usage 或 provider capability fallback；不得含完整錯誤 payload |
| `rag.embedding_fallback` | `turn_id`, `rag_kind`, `embedding_model`, `fallback="bm25"`, `error_type` | embedding API 失敗，改用 BM25 |
| `rag.index_rebuilt` | `conversation_id`, `rag_kind`, `duration_ms`, `chunk_count`, `reason` | cache miss、內容 hash 改變或磁碟 index 無法使用而重建 |
| `maintenance.slow` | `maintenance_id`, `trigger`, `duration_ms`, `slow_threshold_ms`, `stage` | background maintenance 超過 operation threshold |
| `config.invalid` | `setting`, `received_kind`, `fallback_value` | `LOG_LEVEL`、threshold 或 boolean 設定不合法並採 fallback |
| `request.completed` with controlled timeout | 共用 request 欄位、`timeout_ms`, `stage` | 有明確 timeout 且系統仍能安全回覆 |

以下情況不只記 WARNING，應記 `ERROR` 並保留 exception stack trace：

- Discord 回覆失敗。
- state／DB 儲存失敗且沒有安全 fallback。
- 請求處理未捕捉例外。
- logging handler 自身無法寫入時，應至少 fallback 到 stderr。

同一個事件不可同時輸出一筆 `INFO` 與一筆內容相同的 `WARNING`。正常完成是 `INFO`；若符合 slow 條件，該完成事件直接使用 `WARNING`，並保留 `slow=true` 與 threshold 欄位。這樣 `LOG_LEVEL=WARNING` 仍能看到慢請求，而 `LOG_LEVEL=INFO` 會看到全部正常與異常完成事件。

## 9. 成本與效能觀測

每次 OpenAI request 若 usage 可取得，應記錄：

```text
uncached_input_tokens
cached_input_tokens
output_tokens
reasoning_tokens
estimated_cost_usd（若可用且定價設定明確）
```

第一版可以只記錄實際 token usage，不在應用程式內硬編碼價格；成本計算可由外部查詢或後續報表完成。若要計算 `cache_hit_rate`，公式為：

```text
cached_input_tokens / input_tokens
```

不得把整個 request 的 hit rate 與單一 prompt prefix 的 hit rate 混為一談。

不同 `reasoning.effort` 的比較必須保持以下條件一致：

- 相同 model。
- 相同 prompt／tool schema。
- 相同 RAG top-k 與 embedding weight。
- 相同 cache policy。
- 固定一段時間使用同一 effort，避免設定切換造成 cache miss。

## 10. 敏感資料與 redaction

下列內容禁止輸出：

- API keys、Discord bot token、Authorization header。
- 完整玩家訊息與完整 AI prompt／response。
- Scenario text、角色秘密、DM 內容、骰點上下文原文。
- 完整 `previous_response_id`、Discord user ID（除非明確關閉 hash）。

允許輸出：

- 字元數、token 數、bytes、page count、chunk count。
- command name、provider、model、reasoning effort。
- exception type 與經過清理的錯誤摘要。

Redaction 必須在 formatter／observability boundary 進行一次；呼叫端不能假設所有傳入欄位都已安全。

## 11. 實作分層（規格，不在本階段修改）

預計新增：

- `app/observability.py`：context、event API、timer、ID、redaction、usage normalization。
- `app/logging_config.py`：formatter、handler、rotation、環境設定。

預計接入：

- `app/discord_bot.py`：Discord ingress、reply、attachment 與 background task boundary。
- `app/commands/router.py`：route／command lifecycle。
- `app/agents/*.py`：agent turn、executor／narrator timing。
- `app/providers/*.py`：provider request、retry、usage、tool round timing。
- `app/scenario_rag.py`、`app/memory_rag.py`：index／search／embedding timing。
- `app/repositories/group_state.py`、`app/db.py`：state／DB operation timing。

不應把所有 timing 都塞進 `discord_bot.py`；AI、RAG、DB 的 native boundary 必須由各自模組記錄，才能分辨實際耗時。

## 12. 測試計畫

### 12.1 Unit tests

- timestamp 是 UTC ISO-8601 且包含毫秒。
- duration 使用 monotonic clock，格式化不會因 wall-clock 倒退而出現負值。
- `request_id`／`turn_id`／`maintenance_id` 在 nested context 中正確傳遞。
- context 結束後不污染下一個 request。
- JSON formatter 對 `None`、exception、非 ASCII 字串安全。
- secret、prompt、message content 不會出現在輸出。
- slow threshold 正確標記。
- usage 缺欄位時仍可正常輸出 completion event。
- 不合法 logging config 使用 fallback，不阻止啟動。

### 12.2 Integration tests

- 一次 Discord message 能產生完整 request lifecycle 並共用同一個 `request_id`。
- AI provider request 與 tool call 能共用 `turn_id`。
- RAG query failure 仍記錄失敗耗時並 fallback，不造成額外例外。
- DB error／Discord send error 會產生 error event 與 stack trace。
- detached maintenance 的 duration 不會計入 `request.completed.duration_ms`。
- OpenAI usage（含 cached／reasoning token）能被 normalization 成共同 schema。

### 12.3 驗收查詢

至少要能從 JSONL 以 `request_id` 重建以下資訊：

```text
總耗時
lock 等待
程式內部耗時
RAG／embedding 耗時
AI 等待耗時
AI tool 耗時
DB save 耗時
Discord 發送耗時
```

## 13. 可觀測性成功標準

本功能完成後，管理者對任一慢請求應能在不讀完整 prompt 的情況下回答：

1. 慢在 Discord、lock、程式、RAG、AI、DB，還是回覆發送？
2. AI 是否 retry 或多次 tool call？
3. Prompt cache 是否命中？命中多少 token？
4. 使用哪個 model／provider／reasoning effort？
5. 回應送出前或送出後是否還有 background maintenance？
6. 該問題是單次異常，還是同一 conversation／operation 的持續性問題？

## 14. 待確認決策

以下項目在開始實作前需要確認；若沒有特別指定，實作時採用括號內建議：

1. Log 輸出是否預設 JSONL？（建議：正式環境 JSONL，本機可用 text。）
2. `conversation_id`／`user_id` 是否預設 hash？（建議：是。）
3. 是否要記錄 estimated cost？（建議：第一版只記 token，避免價格硬編碼；後續再加報表。）
4. 是否保留檔案 log？（建議：預設 stderr，只有設定 `LOG_FILE` 才寫 rotating file。）
5. 是否要加入 request sampling？（建議：第一版不 sampling，先完整記錄；流量增大後再加。）
6. `LOG_ENABLED` 的預設值與 production policy。（建議：預設 `false`；效能分析期間才開啟。）
7. `LOG_TEXT_ENABLED` 的預設值與 production policy。（建議：預設 `true`，保留既有 error／diagnostic log；需要完全靜默時才關閉。）
8. `LOG_LEVEL` 的預設值與 production policy。（建議：啟用時預設 `INFO`；穩定運行後可改 `WARNING`，需要完整效能分析時再切回 `INFO`。）
9. 是否允許管理者以 debug 設定短暫記錄 hash 後的 user／conversation ID？（建議：預設關閉。）

## 15. 實作後的流程限制

- 本文件通過審查前不得修改 runtime code。
- 使用者明確確認規格後才能開始實作。
- 開始實作前重新 `git fetch origin main_v2`，並完成 `origin/main_v2` ancestor gate。
- 開 PR 前再次對齊 `main_v2`、執行完整測試、`git diff --check` 與 conflict index 檢查。
