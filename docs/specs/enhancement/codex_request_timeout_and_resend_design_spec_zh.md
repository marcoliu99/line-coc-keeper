# 掛住的 Codex 請求 60 秒就切掉並重送，不再空等 120 秒

狀態：backlog。依據 2026-10-09 的兩場《鬼屋》200 回合 run 寫成（`d6a03bb` 從開場、持槍；`50fa21e` 從地下室）；內容尚未實作。

## 問題

兩場 run 共有四個回合花了 126～131 秒，每次都是一個 Codex 請求沒有回應，直到 `CODEX_TIMEOUT`（120 秒）把它切掉：

| run、回合 | 掛住前 | 120 秒後 | 玩家看到 |
|---|---|---|---|
| 開場 49 | 第一個請求就掛住，沒呼叫工具 | 重試一次（6 秒），Executor 判定行動被擋 | 「這一輪已經行動完畢，等守密人推進到下一位」 |
| 開場 74 | 第 1 個請求 8 秒回來、呼叫 `plan_enemy_turn`；第 2 個請求掛住 | 不重試 | 「系統發生內部錯誤；已提交的變更會保留，請稍後再試」 |
| 開場 127 | 第 1 個請求 15 秒回來、呼叫 `declare_combat_action`（沒子彈被拒）；第 2 個請求掛住 | 不重試 | 「處理這個行動的工具失敗了」 |
| 地下室 172 | 第一個請求就掛住，沒呼叫工具 | 重試一次（6 秒），成功 | 正常的檢定提示（劇本結束後的無效回合） |

與這兩場 run 一起審閱的一份報告說回合總期限（`LLM_TURN_DEADLINE_SECONDS`，180 秒）從未套用。實際上有：`turn_budget.with_turn_deadline` 包著 `supervisor.run_turn`；`retry.async_call_with_retry` 以 `asyncio.timeout(turn_budget.remaining())` 限制 Anthropic、Gemini、OpenAI 的每次嘗試；`codex_request_owner.remaining` 取 Codex 期限與回合期限的較小值。兩場 run 都沒有 `TurnDeadlineExceeded`、`lock.held_too_long`、`llm.tool.timeout`，`queue_wait_ms` 全程為 0（單人、一句一句送）。總期限不是問題。

真正的問題比較窄：

1. **單一 Codex 請求可以等 120 秒。** 其他 provider 的單次上限（`LLM_REQUEST_TIMEOUT_SECONDS`）是 60 秒。正常的 Codex 請求 6～15 秒就回來；60 秒沒回的，120 秒也不會回（四次都是等滿上限）。
2. **工具結果之後的掛住從不重試。** `turn_fallback.recoverable` 只在第一次嘗試沒碰過遊戲狀態時才讓 Executor 重跑，這是對的：重跑會重新規劃、可能把工具呼叫兩次。但四次掛住有兩次（74、127）發生在工具執行完**之後**的那個請求，回合就這樣失敗，工具做的事白費。
3. **失敗訊息沒說出發生了什麼。** 74 寫成內部錯誤、127 寫成工具失敗，兩者其實都是守密人沒回應。

## 修改

### 1. Codex 改為單次請求上限

`CODEX_TIMEOUT` 改為一個請求（一個 `codex exec` 程序，或 app-server 傳輸的一次 `turn/start`）的上限，預設 60 秒。整個 Executor 或 Narrator 對話仍由回合期限經 `codex_request_owner.remaining` 管住，和現在一樣。`.env.example` 與設定註解照改。

對四個案例的預期效果：49 和 172 從 120+6 秒變成 60+6 秒；74 和 127 在 §2 的重送生效前，從 127 秒變成約 70 秒。

### 2. 用同一份對話紀錄重送掛住的請求

在 `codex_provider.run_conversation` 裡，`transport.request` 逾時、且回合期限至少還剩 `TURN_RETRY_MIN_REMAINING_SECONDS`（45 秒）時，把同一個 prompt 再送一次：同一份 `transcript`（含工具結果）、同一組 `current_tools`、同一個 `response_schema`。不會重播任何東西：

- exec 傳輸每個請求都用完整對話紀錄組 prompt，重送時已取得的工具結果都在裡面。
- 模型若再要求已呼叫過的工具，`budget.attempted` 會拒絕（`codex_duplicate_tool_attempt`），和現在一樣。
- 重送發生在同一個對話內；`supervisor._recover_blocked_turn` 的 Executor 層重試規則不變，改過狀態的回合仍不會從頭重跑。

每個對話只重送一次。第二次逾時照現在的方式拋出。重送會記錄事件（`codex.request.resent`，含 `stage`、`iteration` 與第一次等了幾秒），讓 run 報告能計數。

這正是能救回 74 和 127 的做法：工具結果已經在對話紀錄裡，再一次 6～15 秒的請求就能把回合做完。

### 3. 明說守密人逾時

Executor 以 provider 逾時結束（`llm.turn.failed` 且 `status: timeout`）、也沒有重送可用的回合，給一個新的 fallback 原因 `keeper_timeout`，文字為「守密人這次回應逾時。已做的部分會保留，沒做的不會重複；請再說一次你的行動。」只在最後一個 provider 錯誤是逾時時取代 `internal_error` 與 `tool_failure`；工具真的失敗的仍是 `tool_failure`。`turn_fallback._GUIDANCE` 加入這一項，`FALLBACK_REASONS` 加入名稱，`classify` 加入對應。

## 不改的部分

- 180 秒回合期限及其接法：已經正確。
- `turn_fallback.recoverable`：改過狀態的回合仍不從頭重跑。
- 工具執行：LLM 逾時從不取消工具（`tool_gateway` 以 shield 保護 worker）。
- conversation lock：慢回合仍讓同一對話的下一回合排隊。
- 以 `asyncio.TaskGroup` 平行跑工具：工具依序改狀態、結果餵給下一個請求，沒有可以並行的獨立工作。

## 驗證

- `tests/test_codex_provider*.py`（或新檔）的單元測試：第一個請求 60 秒逾時後重送一次並採用第二次的回答；重送再逾時則拋出；回合剩不到 45 秒不重送；重送後的決定若指名已呼叫過的工具會被拒。
- `tests/test_turn_fallback*.py`：逾時分類為 `keeper_timeout` 並使用新文字；逾時之後真正的工具失敗仍是 `tool_failure`。
- 下一場 200 回合 run：`codex.request.resent` 的次數、最長回合（預期低於 80 秒）、沒有任何回合的 wall time 落在 120 秒 ±5 秒內。

## 檔案

`app/config.py`、`.env.example`、`app/providers/codex_provider.py`、`app/providers/codex_request_owner.py`、`app/services/turn_fallback.py`、`app/domain/models.py`（`FALLBACK_REASONS`）、上述測試。
