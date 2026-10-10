# A hung Codex request is cut at 60 s and resent, not waited out for 120 s

Status: backlog. Written from the two 200-turn Haunting runs of 2026-10-09 (`d6a03bb`, from the opening, armed; `50fa21e`, from the basement); nothing here is implemented yet.

## Problem

Four turns across the two runs took 126–131 s of wall time, each because one Codex request gave no answer until `CODEX_TIMEOUT` (120 s) cut it:

| Run, turn | Before the hang | After 120 s | What the player read |
|---|---|---|---|
| opening, 49 | the first request hung; no tool called | retried once (6 s), then the Executor judged the action blocked | 「這一輪已經行動完畢，等守密人推進到下一位」 |
| opening, 74 | request 1 answered in 8 s and called `plan_enemy_turn`; request 2 hung | not retried | 「系統發生內部錯誤；已提交的變更會保留，請稍後再試」 |
| opening, 127 | request 1 answered in 15 s and called `declare_combat_action` (refused: no ammunition); request 2 hung | not retried | 「處理這個行動的工具失敗了」 |
| basement, 172 | the first request hung; no tool called | retried once (6 s), recovered | a normal check prompt (an invalid turn after the scenario ended) |

A report reviewed alongside these runs claimed the turn's total deadline (`LLM_TURN_DEADLINE_SECONDS`, 180 s) was never applied. It is: `turn_budget.with_turn_deadline` wraps `supervisor.run_turn`, `retry.async_call_with_retry` bounds every Anthropic, Gemini and OpenAI attempt with `asyncio.timeout(turn_budget.remaining())`, and `codex_request_owner.remaining` takes the lesser of the Codex deadline and the turn's. Neither run logged `TurnDeadlineExceeded`, `lock.held_too_long` or `llm.tool.timeout`, and `queue_wait_ms` was 0 throughout (one player, one line at a time). The total deadline is not the problem.

The problem is narrower:

1. **A single Codex request may wait 120 s.** The other providers' per-request limit (`LLM_REQUEST_TIMEOUT_SECONDS`) is 60 s. A normal Codex request answers in 6–15 s; one that has not answered after 60 s does not answer at 120 s either (all four hung for the full limit).
2. **A hang after a tool result is never retried.** `turn_fallback.recoverable` lets the Executor run again only when the first attempt touched no game state, which is right: a second run plans afresh and may call a tool twice. Two of the four hangs (74, 127) came on the request *after* a tool had run, so the turn fell back with nothing to show for the tool's work.
3. **The fallback wording does not say what happened.** 74 read as an internal error and 127 as a tool failure; both were the Keeper not answering.

## Change

### 1. Per-request limit for Codex

`CODEX_TIMEOUT` becomes the limit for one request (one `codex exec` process, or one `turn/start` on the app-server transport), default 60 s. The whole Executor or Narrator conversation stays bounded by the turn deadline through `codex_request_owner.remaining`, as now. `.env.example` and the config comment say so.

Expected effect on the four cases: 49 and 172 wait 60 + 6 s instead of 120 + 6 s; 74 and 127 wait about 70 s instead of 127 s before the resend in §2 applies.

### 2. Resend a hung request on the same transcript

In `codex_provider.run_conversation`, when `transport.request` times out and the turn deadline leaves at least `TURN_RETRY_MIN_REMAINING_SECONDS` (45 s), send the same prompt once more: the same `transcript` (tool results included), the same `current_tools`, the same `response_schema`. Nothing is replayed:

- The exec transport builds every request from the full transcript, so the resend carries the tool results already obtained.
- A decision that asks for a tool already called is refused by `budget.attempted` (`codex_duplicate_tool_attempt`), as today.
- The resend happens inside the one conversation; the Executor-level retry in `supervisor._recover_blocked_turn` keeps its rule and still does not rerun a turn that changed state.

One resend per conversation. A second timeout raises as now. The resend is logged (`codex.request.resent`, with `stage`, `iteration` and the seconds the first attempt waited) so a run's report can count it.

This is what would have saved turns 74 and 127: the tool's result was in the transcript, and a fresh 6–15 s request would have finished the turn.

### 3. Say that the Keeper timed out

A turn whose Executor ended on a provider timeout (`llm.turn.failed` with `status: timeout`), with no resend left, gets one fallback reason, `keeper_timeout`, whose wording is 「守密人這次回應逾時。已做的部分會保留，沒做的不會重複；請再說一次你的行動。」 It replaces `internal_error` and `tool_failure` only when the last provider error was a timeout; a tool that really failed keeps `tool_failure`. `turn_fallback._GUIDANCE` gains the entry, `FALLBACK_REASONS` the name, and `classify` the mapping.

## Not changing

- The 180 s turn deadline and how it is wired: already correct.
- `turn_fallback.recoverable`: a turn that changed state is still never rerun from the top.
- Tool execution: never cancelled by an LLM timeout (`tool_gateway` shields the worker).
- The conversation lock: a slow turn still queues the next one on the same conversation.
- Running tool calls in parallel (`asyncio.TaskGroup`): tools change state in order and their results feed the next request; there is nothing independent to run side by side.

## Verification

- Unit tests in `tests/test_codex_provider*.py` (or a new file): a first request that times out at 60 s is resent once and the second answer is used; a resend that times out raises; no resend when fewer than 45 s of the turn remain; a resent decision naming an already-called tool is refused.
- `tests/test_turn_fallback*.py`: a timeout classifies as `keeper_timeout` with the new wording; a real tool failure after a timeout still reads as `tool_failure`.
- The next 200-turn run: `codex.request.resent` count, the longest turn (expected under 80 s), and no turn whose wall time is within 5 s of 120 s.

## Files

`app/config.py`, `.env.example`, `app/providers/codex_provider.py`, `app/providers/codex_request_owner.py`, `app/services/turn_fallback.py`, `app/domain/models.py` (`FALLBACK_REASONS`), tests as above.
