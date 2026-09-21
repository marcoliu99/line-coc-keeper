# Async Provider 與外部 I/O 效能優化規格書

## 0. Changeset 與工作分支

- 整合目標：main_v2
- 工作分支：feature/async-provider-performance
- 分支基準：origin/main_v2
- 目前基準 commit：fe02e69b9e534b6939c27f7f3e05e05e153807a4
- 目前 implementation changeset：`fe02e69b9e534b6939c27f7f3e05e05e153807a4..working-tree`
- 前一個 implementation checkpoint：`d1d909b`（`fix: close async provider performance review gaps`）
- 本輪 review-fix checkpoint：待 commit；完成後必須把本行改成實際 commit hash。
- 遠端分支已建立並推送：origin/feature/async-provider-performance
- 本次 changeset 範圍是從 main tree 的 `fe02e69` 接續到 `d1d909b`；下次若 `main_v2` 有新 commit，先記錄新的起點，再繼續讀 patch/更新本段範圍。
- 若 main_v2 在 PR 前有新 commit，必須重新 fetch、對齊並記錄新的 changeset 範圍。
- 實作狀態：已完成 provider、Keeper/Agent async boundary、retry/timeout、RAG gather、Discord operation timeout、prewarm lifecycle、取消 recovery marker、request identity 與本輪 provider lifecycle/RAG fallback/cancellation/shutdown review fixes。ruff、mypy、compileall、pytest 與 pytest-cov 必須在本輪 commit 後重新執行；benchmark 仍屬部署前的效能驗證工作，PR 前仍須重新對齊 `main_v2`。

## 1. 背景與問題

目前三個 provider 的 run_conversation 都是同步函式：

- app/providers/openai_provider.py
- app/providers/anthropic_provider.py
- app/providers/gemini_provider.py

Discord/Agentic pipeline 以 asyncio.to_thread(provider.run_conversation, ...)
呼叫，以免阻塞 asyncio event loop。legacy Keeper path 則由同步的
keeper.run_turn 直接呼叫 provider，再由上層把整段工作搬到 worker thread。

這造成：

1. LLM HTTP 等待被包在 worker thread，event loop 看不到 provider 的原生
   async lifecycle，也不容易正確處理 cancellation、timeout 與 retry。
2. 每個 agent path 都要自行記得 to_thread；漏掉時會直接阻塞 Discord gateway。
3. retry/backoff 使用 time.sleep；async 化後必須改為可取消的 asyncio.sleep。
4. RAG 的 scenario 與 memory 工作目前已透過兩個 task 啟動並有部分並行，
   但等待流程不是明確的 asyncio.gather，也沒有統一的 peer cancellation、
   timeout 與錯誤聚合規則。
5. 實際 profiler/log 顯示主要延遲在外部 I/O，不是 Python CPU：
   - profiler session：489.46 秒、903 samples、CPU time 2.72 秒。
   - llm.turn.completed 平均約 4.79 秒，最高約 10.79 秒。
   - llm.request.completed 最高約 7.23 秒。
   - RAG 約 0.20～0.81 秒，Discord direct output 約 0.31～0.81 秒。
   - DB read 平均約 2.83 ms，lock wait 平均約 0.013 ms。

kqueue/select 是 macOS asyncio event loop 等待 I/O 的正常狀態，不是要修改
的 CPU hot path。本功能的目標是縮短可避免的端到端等待、讓可並行工作真正
並行，並讓外部 I/O 有明確的取消與失敗邊界。

## 2. 目標

### 2.1 主要目標

1. 將三個 provider 的 Keeper conversation API 改為 native async：

       async def run_conversation(...) -> str

2. 移除 production Agent/legacy Keeper path 對
   asyncio.to_thread(provider.run_conversation, ...) 的依賴。
3. 保持 OpenAI、Anthropic、Gemini 的 function-calling 行為、tool 結果、
   response chain、usage logging 與既有 fallback 行為不變。
4. 將 retry/backoff、timeout、cancellation 納入同一套 async contract。
5. 確認 scenario RAG、memory RAG、embedding cache 與 Discord output 的等待
   路徑不會造成不必要的串行等待。
6. 以實際 baseline 為基準，常見 request 的總等待時間目標降低 30～50%。
   這是 benchmark target，不保證單次模型服務端生成時間必然降低；native
   async 主要改善併發與可取消性，token/context 減量與 RAG 並行才是端到端
   降時的主要來源。

### 2.2 成功條件

- 同一個 async event loop 中，provider HTTP 等待不再佔用 worker thread。
- 取消 request 時，provider request、retry sleep、pending tool task 能在
  有限時間內結束，不留下未處理 task。
- RAG scenario/memory 搜尋維持並行，任一側失敗仍依既有 fallback 回傳。
- 不改變玩家可見的骰子、Luck、角色、戰鬥、KP Assistant sudo 與劇情狀態語意。
- 以相同測試情境比較，P50/P95 request latency 有可重現的改善報告；
  若只有 concurrency 改善而單請求 latency 未改善，benchmark 必須明確標示。

## 3. 明確不在本次範圍

- 不修改 kqueue、select、asyncio polling interval 或 event loop policy。
- 不更換 LLM provider、模型、embedding model 或 Discord library。
- 不改變遊戲規則、tool 權限、KP Assistant sudo policy、Luck roll ownership
  或角色 lifecycle。
- 不把 Executor、Narrator、Guard 三個有不同責任的 LLM turn 強行合併。
  Executor facts 必須先交給 Narrator；Guard 只在驗證失敗時觸發。
- 不把 analyze_image、analyze_text 等同步抽取 API 一併改成 async，除非
  實作時發現它們與 conversation client lifecycle 不可分離；這些 API 先維持
  synchronous compatibility contract。
- 不在沒有 benchmark 或遊戲流程測試時直接把 KEEPER_REASONING_EFFORT 從
  medium 改成 low、minimal 或其他值。

## 4. 現況流程（migration 前）

### 4.1 Agentic path

    Discord message
          |
          v
    commands/router.py
          |
          v
    agents/supervisor.py
          |
          +-- Executor -- asyncio.to_thread(sync provider.run_conversation)
          |                    +-- sync execute_tool / state mutation
          +-- State reducer
          +-- Narrator -- asyncio.to_thread(sync provider.run_conversation)
          +-- Guard（validation 失敗才觸發）
                               +-- asyncio.to_thread(sync provider.run_conversation)

### 4.2 Legacy Keeper path

    legacy command / assistant
          |
          v
    asyncio.to_thread(keeper.run_turn)
          |
          v
    sync keeper._run_turn_impl
          |
          +-- sync provider.run_conversation
                    +-- sync API request + sync retry sleep

### 4.3 RAG path

context_builder.build_context 目前先建立 scenario task 與 memory task；兩個 task
內部仍以 asyncio.to_thread 執行同步 index/search/embedding code。大部分情境
可以重疊，但等待與錯誤管理是分開寫的：

    create scenario task --+
                            +-- await scenario
    create memory task ----+
                            +-- await memory

實作改為明確的 gather contract，但不得刪除現有 cache 或 BM25 fallback。

### 4.4 實作後流程

    Discord command
          |
          v
    Supervisor / legacy Keeper
          |
          +-- await async provider.run_conversation
          |       +-- await SDK async HTTP request
          |       +-- cancellable async retry/backoff
          |       +-- await tool callback（SDK 順序，逐一執行）
          |
          +-- tool gateway / legacy tool adapter
                  +-- await asyncio.to_thread(sync state mutation)
                  +-- cancellation 時等待 mutation 完成或寫 recovery marker

    Context builder
          +-- scenario task ----+
          +-- memory task ------+-- asyncio.gather(return_exceptions=True)
                                      +-- per-source status/fallback

    Cancellation/recovery
          +-- read-only timeout/cancel -> shielded worker + exception observer
          +-- mutation cancellation -- graceful wait
                                      +-- timeout -> durable GroupState marker

每個 provider client 是目前 event loop 的 lazy singleton；client 與自己的 in-flight
counter 綁在同一個 lifecycle state。Discord bot runner 的巢狀 `try/finally` 會
保證即使 Discord `close()` 失敗，仍先取消/等待 prewarm，再等待 provider in-flight
request 的 grace period 並關閉三個 async client。同步 vision/text extraction API
仍維持既有 compatibility contract，沒有混入 conversation coroutine。

## 5. 設計

### 5.1 Provider async contract

app/providers/__init__.py 更新為純 async interface：

    ToolExecutor = Callable[[str, dict], Awaitable[dict]]

    async def run_conversation(
        static_system: str,
        dynamic_system: str,
        tools: list[dict],
        history: list[dict],
        new_message: str,
        execute_tool: ToolExecutor,
        max_iterations: int,
        ...,
    ) -> str:
        ...

三個 provider 都必須遵守：

- 沒有 API key 時立即回傳既有中文 fallback，不建立 client。
- 每一次 model request 維持 iteration、usage、retry、error observability。
- 沒有 function call 時回傳文字；有 function call 時依原順序執行 tool，
  再送下一輪。
- tool callback 統一是 awaitable callback，不在 provider adapter 內判斷
  sync/async，也不讓 provider 自己建立 worker thread。
- 同一個 provider iteration 收到多個 function/tool call 時，必須依 SDK 回傳
  順序逐一執行：

      for tool_name, tool_input in tool_uses:
          result = await execute_tool(tool_name, tool_input)
          messages.append(result)

  不得把有 state side effect 的 tool 交給 gather 或平行 task。
- `asyncio.CancelledError` 不得被一般 exception fallback 吞掉。
- provider 不自行建立新的 event loop。

目前的同步 game/tool implementation 由呼叫端改成 async adapter；需要執行同步
DB 或 CPU work 時，adapter 在邊界處使用 `asyncio.to_thread()`，而 provider
只看見 awaitable contract。這使 tool 的執行順序與 cancellation policy 集中
在 tool gateway，而不是散落在三個 SDK adapter。

### 5.2 各 SDK 的 async adapter

| Provider | async client | conversation API | client cleanup |
|---|---|---|---|
| OpenAI | openai.AsyncOpenAI | await client.responses.create | await client.close()/aclose() |
| Anthropic | anthropic.AsyncAnthropic | await client.messages.create | await client.close()/aclose() |
| Gemini | genai.Client(...).aio | await client.aio.models.generate_content | await client.aio.aclose() |

目前環境已確認 OpenAI 3.16.2、Anthropic 1.7.0、google-genai 2.24.0
均提供對應 async surface。實作不可只把 def 改成 async def，必須使用各
SDK 的 async client。

#### Client lifecycle

每個 provider 採「目前 event loop scoped 的 lazy singleton」，不是每次
`run_conversation()` 新建 client，也不是跨 event loop 共用 client：

- 第一次使用時由 `get_async_client()` 建立。
- 同一個 Discord event loop 復用 connection pool。
- client initialization 以 lifecycle state 保護，避免同一個 request scope 看到
  已被 shutdown/reset 的 client。
- test fixture 可以明確 reset/close，不把上一個 test loop 的 client 帶進下一個。
- bot 啟動時可在 `on_ready` 做一次 client warm-up；`on_ready` 可能因 reconnect
  重複觸發，因此 warm-up 必須是 idempotent。
- 不使用 `on_closed` 作為唯一 hook；discord.py 的 reconnect/disconnect event
  不等於 process shutdown。`discord_bot` 改用 async bot runner，在
  `try/finally` 中呼叫所有 provider 的 `shutdown_async_clients()`，確保
  graceful stop、exception stop 都會 cleanup。
- `app/providers/client_lifecycle.py` 將 client、owner、loop、in-flight counter
  與 closing state 綁在一起；request scope 以一次不可分割的 acquire 登記
  in-flight，避免 get client 與 shutdown 之間出現未登記 request。
- shutdown 會建立 barrier，阻擋新的 acquire；先等待 state 的 in-flight counter
  降為 0，超過 grace 才 close transport，並留下 structured shutdown log。關閉
  過程本身使用 shielded cleanup，呼叫端 cancellation 不會遺漏 transport。
- event loop 切換時先 retire 舊 state、等待其 request grace period，再建立新 loop
  的 client；不跨 loop 共用 `asyncio.Lock`/`asyncio.Condition`。舊 state 的
  counter 不會被新 loop 重置。
- Gemini 若 `genai.Client().aio` 是獨立 facade，會同時關閉 aio facade 與 owner；
  facade 與 owner 是同一物件時只關閉一次。

正常 production 仍只有一個 Discord asyncio event loop；lifecycle 另外以短暫的
thread condition 保護跨 loop 的 state transition，因此測試與 loop replacement
不會把一個 loop 的 asyncio primitive 拿到另一個 loop 使用。若未來支援多 bot
process，每個 process 各自擁有一組 client。

OpenAI unsupported parameter fallback（例如 temperature）仍要保留，但
conversation 版本的 _create_response 必須以 await 呼叫 API 及
asyncio.sleep。同步 vision/text extraction 路徑必須與 async helper 分開，
避免同一 helper 同時回傳 coroutine 與 response。

### 5.3 Async retry、timeout 與 cancellation

在 app/providers/retry.py 增加 async counterpart，不刪除現有 sync helper：

    async def async_call_with_retry(
        fn: Callable[[], Awaitable[T]],
        *,
        provider: str,
        operation: str,
        request_id: str | None = None,
    ) -> T:
        ...

規則：

- 重用現有 retryable exception classifier 與 LLM_MAX_RETRIES。
- backoff 使用 await asyncio.sleep(delay)。
- CancelledError 必須直接傳播，不能 retry。
- timeout 不是可無限 retry 的錯誤；依設定判斷 retry budget，最後以
  status=timeout 完成 observability。
- 保留 unsupported parameter 不消耗 connection retry budget 的行為。

#### Cancellation hierarchy

timeout 的單位必須清楚區分，不能把 Discord API send timeout 當成整個 turn
timeout：

    request/turn cancellation
    +-- provider API attempt: LLM_REQUEST_TIMEOUT_SECONDS
    +-- embedding API attempt: EMBEDDING_REQUEST_TIMEOUT_SECONDS
    +-- tool execution: TOOL_EXECUTION_TIMEOUT_SECONDS
    +-- each Discord send/edit: DISCORD_REQUEST_TIMEOUT_SECONDS

採兩種 cancellation policy：

- `IMMEDIATE`：只適用於純 read-only、沒有外部 side effect 的工作；取消後
  立即傳播，不發送回應。
- `GRACEFUL`：適用於會修改 GroupState、pending check、combat 或 SQLite 的
  tool。取消 provider request 後，已開始的 mutation 必須先完成或進入明確的
  recovery path，再釋放 conversation lock；不能用 `asyncio.wait_for()` 直接
  切斷 `to_thread()`，因為它只停止等待 coroutine，底層 thread 仍可能繼續
  修改 state。

因此 tool adapter 必須回報 `completed`、`timeout`、`cancelled` 或
`partial` 狀態。read-only tool timeout 可以回傳：

    {"ok": False, "error": "timeout", "partial": True}

state-mutating tool 不在 mutation 尚未結束時回傳 partial 成功；若 graceful
shutdown grace period（建議 5 秒）內仍無法完成，記錄 error、保留 recovery
marker，並不送出誤導玩家的正常結果。marker 只保存 tool name、輸入摘要 hash、
marker id、UTC timestamp 與 `recovery_required` 狀態，不保存任意 tool input。
`observability.span()` 對 `asyncio.CancelledError` 另記錄 `<operation>.cancelled`
與 `status=cancelled`，再原樣 re-raise；LLM 被 cancellation 時不發送正常回應；
Discord 層只在確定可以安全說明狀態時送出 timeout/cancelled 提示。

read-only tool 若已進入同步 worker，timeout/cancel 不會假裝停止底層 thread；
呼叫端立即返回 partial/cancelled，但會掛上 background task observer，消費
worker 最終結果或例外，避免未處理 task。真正的 embedding HTTP client 也設定
`timeout=EMBEDDING_REQUEST_TIMEOUT_SECONDS` 與 `max_retries=0`，使外層
`asyncio.to_thread` timeout 不會成為唯一的網路保護。

#### Timeout fallback

各層 fallback 採固定規則，不以模糊的全域 quality 分數判斷：

| Operation | timeout action | retry policy |
|---|---|---|
| embedding query/index batch | 使用 BM25 或既有 index；標記 source fallback | timeout 不 retry |
| LLM request | 只對尚未收到 response 的 transient timeout 依 budget retry | 最多一次 timeout retry，仍受總 retry budget 限制 |
| Discord send/edit/followup | 保留 state 結果，標記 delivery failure | 預設不 retry，避免重複訊息 |
| read-only tool | 回傳 partial timeout error 給模型 | 不重試 tool 本身 |
| state-mutating tool | graceful 完成或 recovery | 不做盲目重試，必須依 event/idempotency 判斷 |

#### Provider exception classification

在 retry layer 統一成 provider-neutral 分類，SDK 只負責把原始 exception
交給 classifier：

    ProviderError = RATE_LIMITED | TIMEOUT | TRANSIENT_SERVER |
                    | AUTH_FAILED | MODEL_NOT_FOUND | INVALID_REQUEST
                    | UNKNOWN

`RATE_LIMITED`、`TIMEOUT`、`TRANSIENT_SERVER` 才可依 retry budget 重試；
`AUTH_FAILED`、`MODEL_NOT_FOUND`、`INVALID_REQUEST` 立即終止。classifier 可
同時使用 SDK exception type、status code、Retry-After 與 exception name，
但不得把一般 4xx 或 unsupported parameter 誤判為 transient。

#### Request identity across retries

retry 不建立新的 logical request id。優先沿用
外層 `request_id`/`turn_id` 仍由 request lifecycle 維持；每個 provider API
iteration 由 provider boundary 建立一次 logical provider request id，並把它傳給
所有 attempts。每個 attempt 只增加 `attempt` 欄位；`llm.turn`、外層 request_id
與該 iteration 的 logical provider request id 維持不變。這樣 log 可以把 retry 視為同一個 logical operation，
又能區分每次實際 HTTP attempt。

建議新增或整理的環境變數：

| 變數 | 建議預設 | 用途 |
|---|---:|---|
| LLM_REQUEST_TIMEOUT_SECONDS | 60 | 單次 provider request，非整個 turn |
| EMBEDDING_REQUEST_TIMEOUT_SECONDS | 20 | 單次 embedding request |
| DISCORD_REQUEST_TIMEOUT_SECONDS | 10 | 單次 Discord send/edit/followup |
| TOOL_EXECUTION_TIMEOUT_SECONDS | 30 | read-only tool 的單次執行上限；mutation 另走 graceful policy |
| MAX_TOOL_ITERATIONS | 現有 8 | 將目前 hard-coded guard 變成可調設定 |

第一次 implementation 不直接降低 KEEPER_REASONING_EFFORT 或 MAX_LOG_TURNS
預設值；先以 usage/log 與 benchmark 決定，避免用效能名義破壞劇情上下文
或規則流程。

### 5.4 Keeper 與 Agent async 邊界

legacy Keeper boundary 直接改成 async public API：

    async def run_turn(...) -> tuple[str, list[...], list[...]]:
        ...

並將 _run_turn_impl 及其 provider call 改為 async。production call sites
改成直接 await：

- app/agents/executor.py
- app/agents/narrator.py
- app/agents/guard.py
- app/agents/assistant.py
- app/legacy_commands.py
- app/commands/handlers/system.py
- 其他直接呼叫 keeper.run_turn 的 async command path

不保留同步 `keeper.run_turn()` wrapper。這是明確的 breaking internal API
migration：所有 production caller、測試 fake/provider 與直接 import 都改成
await keeper.run_turn(...)。不在 async function 內用 asyncio.run()，也不保留
一條會重新把 provider 包回 worker thread 的診斷路徑；需要診斷時使用 async
test runner 或 standalone async benchmark。

Keeper state/tool 語意必須維持：

- observability.span("llm.turn") 仍包住完整 turn。
- OpenAI previous_response_id 仍只在正式 player/Keeper history 需要時更新。
- KP Assistant OOC/canonical 升格規則不變。
- execute_turn_tool 的 state mutation 順序不變；多個 function calls 不得因
  async 化而未經設計地平行執行。

### 5.5 Prompt/context 與 tool iteration 優化

先使用現有且可觀測的控制點，不做無法回復的隱式截斷：

1. MAX_LOG_TURNS、MAX_SCENARIO_CHARS、SCENARIO_RAG_TOP_K 維持 env 可調；
   benchmark 對照實際 input token、cached input token、品質與 latency。
2. MAX_TOOL_ITERATIONS 改為 env 設定，但 default 先維持 8。以
   iteration_count、tool_call_count、retry_count 作為調參依據。
3. Tool call 不做無條件平行化：遊戲工具可能依序修改 HP、SAN、pending
   check、combat 或角色狀態。只有明確標為 read-only 且無前置依賴的工作，
   未來才可獨立評估平行化。
4. Executor/Narrator/Guard 不合併。Executor 的機制 facts 必須先進 payload，
   Narrator 才能產生敘事；Guard 已是條件式 repair。
5. KEEPER_REASONING_EFFORT 只做 A/B benchmark，不在 migration commit
   中偷偷變更。若之後選 low 或 minimal，另有設定變更與劇情品質測試。

#### Context budget priority matrix

Token 緊張時不直接對完整 prompt 做任意字串切片，而是依可恢復性與遊戲
正確性採固定優先順序：

| 優先級 | Context source | 建議 budget 參考 | 超量處理 |
|---|---|---:|---|
| 0 | system rules、active state、mechanic facts | 保留 | 不截斷；這些是正確性硬需求 |
| 1 | RAG results | 30% | 先移除低分/較舊結果；需要時可重新查詢 |
| 2 | history / campaign context | 50% | 先保留最近 turn，再依既有 summary/memory RAG 收縮 |
| 3 | scenario text | 20% | 最後才依頁面/章節邊界截斷，不能切半個結構化區塊 |

這些百分比是 context budget 的調整基準，不是把 30/50/20 直接乘在每個
字串上。每次截斷要記錄 source、原始/保留 token 或字元數、原因；若 RAG
結果已不存在，Keeper 仍可透過既有 search tool refetch。scenario 的重要
規則與目前 state 不受這個 optional budget 影響。

### 5.6 RAG、embedding cache 與預熱

#### 保留的現有能力

- Scenario index 有 memory/disk cache，index embedding 不應每回合重建。
- Query embedding 使用 embedding_cache，失敗仍回退 BM25。
- Memory RAG 的 index/cache/fallback 行為不變。
- Combat skip RAG 的既有規則不變。

#### 本次調整

context_builder.build_context 改為明確的 gather 流程：

    scenario task -----+
                       +-- asyncio.gather(..., return_exceptions=True)
    memory task -------+
                       |
                       +-- 以每個 source 的 status 組出 context
                       +-- 單側失敗：保留另一側結果
                       +-- 雙側失敗/timeout：空 RAG context，LLM 照常執行
                       +-- cancellation：取消尚未完成的 peer task

這不是宣稱現況完全串行；現況已用 create_task 做部分重疊。本次補的是可
測試且明確的同步點、timeout、取消和 exception isolation。每個 RAG search
另外回報 `query_embedding_status`：`success`、`fallback`、`not_used` 或
`empty`。BM25 query fallback 仍保留給明確 search tool，但 proactive
context_builder 只接受 semantic query 成功的結果；query embedding failure
不會把 BM25 結果誤注入 LLM context。

RAG 的 `asyncio.to_thread()` worker 在 timeout/cancellation 時以
`asyncio.shield()` 保護，不假裝停止底層同步工作；呼叫端立即得到 timeout 或
取消，但 worker 由 background-task observer 消費最終結果/例外，避免 late
exception 或 unobserved task warning。parent cancellation 從 gather 結果重新
傳播，不會被 `return_exceptions=True` 轉成普通的 optional source error。

不採用任意的 `quality < 0.5` threshold：BM25、cosine、empty result 與
fallback result 沒有可跨 source 比較的共同品質尺度。每個 source 回報
`success`、`empty`、`fallback`、`timeout`、`error` 或 `cancelled`；只有
`success` 且有結果時才注入該 source，其他 status 只進 observability。
雙側都不可用時不向玩家假稱有檢索結果，也不因 RAG optional failure 直接
丟棄整個 LLM turn。

Embedding API 目前仍透過同步 OpenAI embedding client 放在 `asyncio.to_thread`；
client 本身設定 request timeout/no SDK retry，讓同步 HTTP worker 具有真實網路
上限。BM25
與 JSON/index 操作仍可留在 asyncio.to_thread，避免同步 CPU/DB 阻塞 Discord
event loop。Scenario import/reparse 完成後可建立 background prewarm task，
讓第一個玩家 turn 不必承擔 scenario index embedding cold start；prewarm
失敗不得阻擋 scenario usable 狀態，下一次 query 仍可 lazy fallback。

Prewarm policy：

- `SCENARIO_RAG_PREWARM_ENABLED=true` 時才啟用，預設沿用目前 lazy behavior，
  以避免部署升級時突然增加 embedding API 費用。
- `SCENARIO_RAG_PREWARM_MAX_CONCURRENT=1`，以 semaphore 限制同時 index build。
- priority 為 low：玩家 request 優先；同一 conversation 正在處理 request 時，
  prewarm 可以延後或取消，不能搶占 conversation lock。
- bot shutdown 取消尚未開始的 prewarm，等待已開始的 embedding batch 進入
  timeout/recovery；prewarm wrapper 與實際 `to_thread` worker 分開追蹤，並在
  shutdown grace period 內等待 worker；不新增 psutil 依賴，也不以不可靠的固定 OOM MB threshold
  作為 correctness gate。

### 5.7 Discord output 與使用者體感

目前 button interaction 已先透過 _edit_interaction_view 移除 view，再執行
較慢的 check/Luck/Keeper flow，已具備 immediate acknowledgement 效果。

本次不對一般 discord.Message 假造 slash-command defer：

- interaction callback：維持先 acknowledge/edit，再做長工作；新 interaction
  path 統一使用 response.defer 或等價的 immediate edit。
- 一般 message path：不強制每次先送「處理中」，避免增加 message 數量與噪音；
  若未來需要 processing message，必須採可 edit 的 message handle 並納入 metrics。
- 所有 Discord send/edit/followup 以 DISCORD_REQUEST_TIMEOUT_SECONDS 包住；
  timeout 記錄 status=timeout 或 discord.reply.failed，不讓一個 background
  button posting 失敗中斷其他 pending button。
- 這個 timeout 只限制單次 Discord API operation，不取消正在進行的 LLM turn；
  若 LLM 已完成但 Discord 發送 timeout，state result 仍保留，delivery failure
  必須可由 log/metrics 辨識，不能重新執行整個遊戲 turn。
- 保留 reply_message_count 與 reply_edit_count 分離，避免 duplicate keyword
  error 再發生。
- public message、interaction follow-up 與 direct DM/image output 都使用
  `discord.reply` latency span；DM 仍沿用相同的成功後 metrics accounting，
  不會把 fetch user latency 誤算成已送出訊息。

## 6. 資料與介面不變性

本次新增 `GroupState.tool_recovery_markers` 欄位，但不變更 SQLite table/schema；
既有 JSON snapshot 讀取時預設為空 list，並沿用既有 GroupState serialization。
新增設定只透過 .env/config 讀取，並在文件中說明預設值。

Provider function-call 結果仍使用 json.dumps(..., ensure_ascii=False)；
OpenAI response chain 仍以 previous_response_id/callback contract 連接。
async 化不得將 coroutine、SDK response object 或 traceback 寫入 state.log。

## 7. Observability 需求

每次外部操作至少要能辨識：

- request_id、conversation_id、turn_id
- provider、model、agent、iteration、logical provider request id、attempt
- duration_ms、status、timeout_ms
- retry attempt、backoff、error type
- input/output/cached/reasoning token（provider 能提供時）
- RAG rag_kind、cache state、candidate/result count
- Discord reply/edit/chunk/bytes

新增 async boundary 不得重複產生 turn span；retry attempt 要沿用外層
request_id/turn_id，只增加 attempt，不建立新的 logical request：

- Agentic pipeline 一個 agent 一個 llm.turn。
- legacy run_turn 一個 Keeper turn 一個 llm.turn。
- provider 每一個 API iteration 一個 llm.request。
- retry 不把同一個 logical request 誤記成新 turn。

## 8. 測試計畫

### 8.1 Provider contract

- 三個 run_conversation 都是 coroutine function。
- fake async client 的文字、一次/多次 tool call 結果與 iteration 順序和舊版一致。
- provider 只接受 awaitable execute_tool；tool gateway 對同步 DB/CPU adapter
  的 offload 與 state-mutating 順序有獨立測試。
- unsupported parameter fallback 維持原行為。
- async transient retry 使用 asyncio.sleep，不呼叫 time.sleep。
- timeout 產生正確 structured event；cancellation 不 retry、不吞例外。
- client singleton 在同一 event loop 復用；不同 loop/test 不共用；cleanup 在
  成功、API error、timeout、cancelled 四條路徑都執行；shutdown barrier 會等待
  active request，loop switch 會 close 舊 client，Gemini 會 close aio/owner。
- ProviderError classification 對 rate limit、timeout、5xx、auth、model
  missing、一般 4xx 具有明確 retry/non-retry 結果。

### 8.2 Agent/legacy flow

- Executor、Narrator、Guard、KP Assistant、legacy Keeper 都直接 await async
  provider boundary。
- llm.turn / llm.request lifecycle 完整且不重複。
- OpenAI previous response id、KP canonical/OOC persistence regression tests。
- 既有 state mutation、tool order、Luck、combat、sudo 測試全部通過。
- keeper.run_turn 只提供 async API；所有既有 sync test 改為 async test runner，
  不新增 asyncio.run compatibility wrapper。

### 8.3 RAG/embedding

- scenario/memory task 同時開始；總耗時接近較慢的一側，而非兩者相加。
- 任一側 RAG 失敗時，另一側結果仍保留，BM25 fallback 不變。
- cancellation 會取消尚未完成的 RAG task。
- query embedding cache 命中時不再呼叫 API。
- scenario index memory/disk cache、prewarm 成功/失敗、lazy rebuild 有測試。
- embedding client 將 timeout/no-retry 參數傳給 SDK；prewarm worker shutdown
  有 bounded grace 與 late exception observer 測試。
- active combat 仍不啟動 proactive RAG。
- source status 為 empty/fallback/timeout/error/cancelled 時，不注入虛假的
  RAG context；雙側失敗仍能繼續正常 LLM turn。
- query embedding failure 產生 `query_embedding_status=fallback` 且 proactive
  scenario/memory context 保持空白；BM25 仍可供明確 search tool 使用。
- RAG timeout 後底層 worker 可完成且被 observer 消費；parent build cancellation
  會 re-raise，不會被 gather 的 `return_exceptions=True` 吞掉。
- context budget overflow 依 rules/state、RAG、history、scenario 的矩陣測試，
  不在任意字元位置切斷 JSON 或結構化 state。

### 8.4 Discord

- button interaction 先 acknowledge，再執行慢流程。
- send/edit/followup timeout 記錄正確 status 並釋放 lock/task。
- direct output 的 reply_message_count、reply_edit_count、chunk/bytes 不重複或漏記。
- direct DM 與 DM image 具有獨立 latency span 且成功後才計入 output metrics。
- Discord unavailable 的既有測試維持 skip，不造成 import error。

### 8.5 Static/quality/benchmark

- ruff check .
- pytest -q
- pytest --cov=app --cov-report=term-missing
- git diff --check
- 同一組 fixture 執行 sync baseline/async implementation benchmark，至少記錄
  P50、P95、provider request、RAG、Discord output、CPU time 與 worker thread 數。

### 8.6 Review regression

- GroupState recovery marker 可 round-trip，舊 snapshot 不會缺欄位。
- scene digest 在同一秒建立多筆時不會因短 UUID 片段碰撞。
- async retry 的多個 attempt 共用同一 logical request id，只遞增 attempt。

## 9. Rollout 與回復策略

第一個 implementation commit 以 async provider contract 與測試為主，第二個
commit 才調整 prompt/RAG/timeout 等效能設定，避免一次變更無法判斷收益來源。

預設設定先保持：

- KEEPER_REASONING_EFFORT=medium
- MAX_TOOL_ITERATIONS=8
- 既有 MAX_LOG_TURNS、SCENARIO_RAG_TOP_K 與 embedding weight

若 async provider 在特定 SDK/provider 發生問題，可暫時使用尚存的同步
compatibility wrapper 診斷；production async path 不得永久退回
asyncio.to_thread(provider.run_conversation) 而不記錄原因。

## 10. 審閱決策與目前採用方案

以下是依本次 review 補齊並已落地的設計決策；前三項是 implementation gate：

### 高優先級 gate

1. Tool contract：採「方案 A」，provider 只接受 awaitable ToolExecutor，
   同一 iteration 的多個 tool call 嚴格依 SDK 回傳順序逐一 await；不平行執行
   state-mutating tools。
2. Client lifecycle：採 event-loop scoped lazy singleton + lifecycle state/短暫
   thread condition；request acquire 與 in-flight counter 原子化，shutdown barrier
   阻擋新 request 並 bounded drain；`on_ready` warm-up 必須 idempotent；用 bot
   runner 的巢狀 `try/finally` 做 shutdown，即使 Discord close 失敗也會清理
   prewarm/provider，不把 reconnect 的 `on_disconnect` 或不存在/不適合的
   `on_closed` 當 shutdown hook。
3. Cancellation：provider HTTP、retry sleep、read-only tool 可 immediate cancel；
   state-mutating tool 採 graceful policy，先完成 mutation 或 recovery，再釋放
   lock。`wait_for(to_thread(...))` 不可直接切斷 mutation。

### 中優先級決策

4. Timeout fallback：embedding timeout 立即 BM25-only、不 retry；LLM timeout
   最多一次 transient retry；Discord operation timeout 預設不 retry，避免重複訊息。
5. RAG failure isolation：不採任意 quality score threshold，改用每個 source 的
   deterministic status 與 query embedding status；一側成功就使用，一側失敗不
   丟另一側，BM25 query fallback 不進 proactive context，雙側失敗則空 RAG
   context 但照常執行 LLM，並記錄 degraded event。
6. Request identity：retry 使用同一個外層 request_id/turn_id/logical provider
   request id，只增加 attempt 欄位，不為每次 retry 建立新 logical id。
7. Provider error classification：rate limit、timeout、5xx 才依 bounded budget
   retry；auth/model-not-found/一般 4xx 立即終止。

### 低優先級決策

8. Context budget：以 system rules/active state 為硬保留；optional budget 參考
   為 RAG 30%、history 50%、scenario 20%，超量時依可恢復性處理，不任意切字串。
9. Sync wrapper：採完全移除方案；keeper.run_turn 直接成為 async API，production
   與測試不保留同步 wrapper。
10. Prewarm：可選開關，預設維持 lazy；啟用後 concurrency=1、低優先級、可取消，
    不新增 psutil/OOM threshold correctness dependency。

### 本輪 review-fix 落地項目

11. Provider lifecycle：補上跨 loop state isolation、shutdown barrier、active
    request drain、shielded cleanup、Gemini owner/facade 雙重 close。
12. RAG：補上 query embedding status contract；proactive context 不接受 query
    embedding failure 的 BM25 結果；timeout/cancel 的 to_thread worker 交由
    observer 管理，parent cancellation 重新傳播。
13. Observability/shutdown：`CancelledError` 產生明確 cancelled span；bot
    runner 使用 nested finally 保證 cleanup chain。

仍維持兩項不在本次預設變更的調參決策：

- KEEPER_REASONING_EFFORT 仍為 medium，先做 A/B benchmark。
- MAX_TOOL_ITERATIONS 變成 env knob，但 default 仍為 8；是否調低另以流程測試
  與 benchmark 決定。
