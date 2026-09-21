# Async Provider 與外部 I/O 效能優化規格書

## 0. Changeset 與工作分支

- 整合目標：main_v2
- 工作分支：feature/async-provider-performance
- 分支基準：origin/main_v2
- 目前基準 commit：fe02e69b9e534b6939c27f7f3e05e05e153807a4
- 遠端分支已建立並推送：origin/feature/async-provider-performance
- 若 main_v2 在 PR 前有新 commit，必須重新 fetch、對齊並記錄新的 changeset 範圍。
- 本文件目前是規格審閱版本；規格獲確認前不修改 runtime implementation。

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

## 4. 現況流程

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

## 5. 設計

### 5.1 Provider async contract

app/providers/__init__.py 更新為 async interface：

    ToolExecutor = Callable[
        [str, dict],
        dict | Awaitable[dict],
    ]

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
- tool callback 同時支援目前的同步 callback 與未來 async callback：
  - synchronous callback 以 asyncio.to_thread 執行，避免同步 DB/embedding
    阻塞 event loop。
  - awaitable callback 直接 await。
  - asyncio.CancelledError 不得被一般 exception fallback 吞掉。
- provider 不自行建立新的 event loop。

### 5.2 各 SDK 的 async adapter

| Provider | async client | conversation API | client cleanup |
|---|---|---|---|
| OpenAI | openai.AsyncOpenAI | await client.responses.create | await client.close()/aclose() |
| Anthropic | anthropic.AsyncAnthropic | await client.messages.create | await client.close()/aclose() |
| Gemini | genai.Client(...).aio | await client.aio.models.generate_content | await client.aio.aclose() |

目前環境已確認 OpenAI 3.16.2、Anthropic 1.7.0、google-genai 2.24.0
均提供對應 async surface。實作不可只把 def 改成 async def，必須使用各
SDK 的 async client。

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
    ) -> T:
        ...

規則：

- 重用現有 retryable exception classifier 與 LLM_MAX_RETRIES。
- backoff 使用 await asyncio.sleep(delay)。
- CancelledError 必須直接傳播，不能 retry。
- timeout 不是可無限 retry 的錯誤；依設定判斷 retry budget，最後以
  status=timeout 完成 observability。
- 保留 unsupported parameter 不消耗 connection retry budget 的行為。

建議新增或整理的環境變數：

| 變數 | 建議預設 | 用途 |
|---|---:|---|
| LLM_REQUEST_TIMEOUT_SECONDS | 60 | 單次 provider request，非整個 turn |
| EMBEDDING_REQUEST_TIMEOUT_SECONDS | 20 | 單次 embedding request |
| DISCORD_REQUEST_TIMEOUT_SECONDS | 10 | 單次 Discord send/edit/followup |
| MAX_TOOL_ITERATIONS | 現有 8 | 將目前 hard-coded guard 變成可調設定 |

第一次 implementation 不直接降低 KEEPER_REASONING_EFFORT 或 MAX_LOG_TURNS
預設值；先以 usage/log 與 benchmark 決定，避免用效能名義破壞劇情上下文
或規則流程。

### 5.4 Keeper 與 Agent async 邊界

新增 async 版本的 legacy Keeper boundary：

    async def run_turn_async(...) -> tuple[str, list[...], list[...]]:
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

keeper.run_turn 是否保留為同步 compatibility wrapper，取決於測試與外部
import contract。若保留，wrapper 只能在沒有 running event loop 的同步 caller
使用 asyncio.run(run_turn_async(...))；async caller 不得透過它重新建立 loop。
這個 wrapper 不得被 production Discord path 使用。

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
                       +-- 成功：組出兩份 context
                       +-- 單側失敗：記錄 fallback，另一側照常使用
                       +-- cancellation：取消尚未完成的 peer task

這不是宣稱現況完全串行；現況已用 create_task 做部分重疊。本次補的是可
測試且明確的同步點、timeout、取消和 exception isolation。

Embedding API 若在 async context 使用，提供 async embedding boundary；BM25
與 JSON/index 操作仍可留在 asyncio.to_thread，避免同步 CPU/DB 阻塞 Discord
event loop。Scenario import/reparse 完成後可建立 background prewarm task，
讓第一個玩家 turn 不必承擔 scenario index embedding cold start；prewarm
失敗不得阻擋 scenario usable 狀態，下一次 query 仍可 lazy fallback。

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
- 保留 reply_message_count 與 reply_edit_count 分離，避免 duplicate keyword
  error 再發生。

## 6. 資料與介面不變性

本次不新增遊戲 state 欄位、不變更 SQLite schema。新增設定只透過 .env/config
讀取，並在文件中說明預設值。

Provider function-call 結果仍使用 json.dumps(..., ensure_ascii=False)；
OpenAI response chain 仍以 previous_response_id/callback contract 連接。
async 化不得將 coroutine、SDK response object 或 traceback 寫入 state.log。

## 7. Observability 需求

每次外部操作至少要能辨識：

- request_id、conversation_id、turn_id
- provider、model、agent、iteration
- duration_ms、status、timeout_ms
- retry attempt、backoff、error type
- input/output/cached/reasoning token（provider 能提供時）
- RAG rag_kind、cache state、candidate/result count
- Discord reply/edit/chunk/bytes

新增 async boundary 不得重複產生 turn span：

- Agentic pipeline 一個 agent 一個 llm.turn。
- legacy run_turn_async 一個 Keeper turn 一個 llm.turn。
- provider 每一個 API iteration 一個 llm.request。
- retry 不把同一個 logical request 誤記成新 turn。

## 8. 測試計畫

### 8.1 Provider contract

- 三個 run_conversation 都是 coroutine function。
- fake async client 的文字、一次/多次 tool call 結果與 iteration 順序和舊版一致。
- sync execute_tool 會被安全 offload；async execute_tool 會被 await。
- unsupported parameter fallback 維持原行為。
- async transient retry 使用 asyncio.sleep，不呼叫 time.sleep。
- timeout 產生正確 structured event；cancellation 不 retry、不吞例外。
- client cleanup 在成功、API error、timeout、cancelled 四條路徑都執行。

### 8.2 Agent/legacy flow

- Executor、Narrator、Guard、KP Assistant、legacy Keeper 都直接 await async
  provider boundary。
- llm.turn / llm.request lifecycle 完整且不重複。
- OpenAI previous response id、KP canonical/OOC persistence regression tests。
- 既有 state mutation、tool order、Luck、combat、sudo 測試全部通過。
- 若保留 sync keeper.run_turn wrapper，補 no-running-loop compatibility test，
  並確保 running-loop caller 不會錯誤重入。

### 8.3 RAG/embedding

- scenario/memory task 同時開始；總耗時接近較慢的一側，而非兩者相加。
- 任一側 RAG 失敗時，另一側結果仍保留，BM25 fallback 不變。
- cancellation 會取消尚未完成的 RAG task。
- query embedding cache 命中時不再呼叫 API。
- scenario index memory/disk cache、prewarm 成功/失敗、lazy rebuild 有測試。
- active combat 仍不啟動 proactive RAG。

### 8.4 Discord

- button interaction 先 acknowledge，再執行慢流程。
- send/edit/followup timeout 記錄正確 status 並釋放 lock/task。
- direct output 的 reply_message_count、reply_edit_count、chunk/bytes 不重複或漏記。
- Discord unavailable 的既有測試維持 skip，不造成 import error。

### 8.5 Static/quality/benchmark

- ruff check .
- pytest -q
- pytest --cov=app --cov-report=term-missing
- git diff --check
- 同一組 fixture 執行 sync baseline/async implementation benchmark，至少記錄
  P50、P95、provider request、RAG、Discord output、CPU time 與 worker thread 數。

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

## 10. 待審閱決策

1. 是否同意 provider、legacy Keeper boundary、retry、RAG gather、Discord
   timeout 一次納入同一個 feature，或先拆成兩個 PR？
2. LLM 60 秒、embedding 20 秒、Discord 10 秒的 timeout 是否符合部署環境？
3. 是否只把 MAX_TOOL_ITERATIONS 變成 env knob、維持 default 8，還是經
   benchmark 後另調低 default？
4. 是否允許 scenario import/reparse 建立 background embedding prewarm？這會
   把部分成本從第一個玩家 turn 移到上傳/背景 maintenance，但會增加
   background task 與 shutdown cleanup 複雜度。
5. KEEPER_REASONING_EFFORT 是否只做報告，還是本次另提供 A/B 設定範例？
   本 spec 預設不改 production default。

