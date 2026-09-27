# Measured turn latency priorities

[繁體中文](measured_turn_latency_priorities_design_spec_zh.md)

Status: **proposed; awaiting design approval**. Base: `main_v2` at `5961f2b`.

## 0. Why this document exists

Three planning documents already describe latency work for this project: `main_v2_detailed_fix_plan.md`, `main_v2_detailed_fix_plan_ux_latency_v2.md` and `main_v2_python_optimization_safety_addendum.md`. Each states explicitly that it did not run the test suite, a real Discord session, or any LLM performance evaluation. Their constraints are sound and this document does not revisit them. What they lack is measurement, so their ordering is unverified.

This document supplies the measurement and reorders the work accordingly. It does not propose a new agent, a new database, an early-exit rule, or any change to the correctness contracts those documents establish.

### 0.1 Evidence base

Two sessions, both from `~/coc_v2_log`.

**Pre**: 17 logs covering 2026-09-25 to 2026-09-27, ending at the incident session — 323 requests with a request id, 156 model requests with usage, 175 lock acquisitions. This is the state of the deployment before `edd2fd6`.

**Post**: `20260927-132223-807874`, 2026-09-27 13:04–13:22 UTC, the first run after `edd2fd6` merged #102, #103 and #104 — 15 executor turns, 77 model requests with usage, 22 lock acquisitions, 1,068 s profiled.

Local CPU figures are direct benchmarks against a real 85 KB persisted state, not profiler attribution, because this project dispatches token estimation through `asyncio.to_thread` and a main-thread profiler does not attribute that work.

### 0.2 What the post-`edd2fd6` session settled

The merged fixes worked on correctness and did not touch latency.

| | Pre | Post |
|---|---|---|
| Executor resolutions ending `incomplete` | 5 of 9 (55%) | **1 of 15 (7%)** |
| `utf8_bytes_fallback` events | 46 of 46 | **0** |
| `llm.tokenizer.unavailable` | — | **0** |
| `complete_for_action` | 0 of 6 | 1 of 2 |
| Executor median / p90 / max | 9.5 s / 17.9 s / 21.2 s | **15.7 s / 35.3 s / 60.6 s** |
| End-to-end p90 | 17.3 s | **36.6 s** |
| Cache hit, first request of a turn | 4.6% | **0.0%** |
| Cache hit, second request | 71.4% | 77.3% |
| Cache hit, third onwards | 64.7% | 79.0% |
| Cache hit, Narrator-shaped requests | 18.3% | **5.1%** |
| Conversation lock p99 | 54,706 ms | 56,948 ms |

Fourteen of fifteen turns now validate, across `no_mechanics` (5), `await_check` (5), `resolved_without_check` (2) and `blocked` (2). `await_check` and `blocked` did not survive validation at all in the pre session. `retrieval_budget_exceeded` fired once, reporting budget exhaustion honestly instead of presenting it as missing scenario evidence.

### 0.3 What the measurements changed about the plan

| Claim under review | Source | Measured | Verdict |
|---|---|---|---|
| Local CPU work belongs in the first batch | addendum P3/P4/P5 | 6–10 ms per turn; over 1,068 s profiled the largest app frame is `get_async_client` at 0.38 s, `run_turn` 0.13 s, `estimate` 0.01 s | Demote |
| `select_history()` repeats `estimate()` and is worth caching | addendum P4 | 0.07 ms for the whole log | Demote |
| `_encoding()` currently uses `lru_cache` | addendum P4 | Replaced in #102 by an explicit cache with a retry deadline | Stale |
| A normal tool turn is `b + 1` Executor requests plus one Narrator request | v2 UX.3 | Median 4, max 10 model requests per turn | Confirmed |
| Static prompt stable, dynamic data placed after it, cache verified by usage | v2 UX.5-D | Not implemented; `prompt_config.py` static assembly untouched in #97 and #99 | Adopt as WP2 |
| Per-conversation gameplay stays serialized; read-only commands bypass the lock | v1 F8.1 | The measured wait is between gameplay turns, which a read-only bypass does not touch | Gap — WP3 |
| — | — | Retrieval now drives latency: `search_scenario` is 34 of 43 tool calls | New — WP4 |

Local CPU per turn, benchmarked directly:

```text
keeper._build_static_prompt        0.01 ms
keeper._build_dynamic_prompt       0.10 ms
input_budget.estimate(static)      0.84 ms
input_budget.estimate(tools)       1.22 ms
input_budget.select_history(log)   0.07 ms
GroupState.from_dict               0.13 ms
state.to_dict() + json.dumps       0.14 ms
                        per turn   6–10 ms
```

## 1. WP1 — Make the baseline reproducible

The post-`edd2fd6` baseline in §0.2 now exists, so WP2 and WP3 are no longer blocked. What is missing is the ability to reproduce it: every figure above was derived ad hoc, which makes before/after comparison for the remaining work packages unrepeatable.

Deliverable: `scripts/analyze_turn_latency.py`, read-only over a runtime log directory, reporting exactly the metrics this document's decisions rest on — cache hit rate by request position and by stage, conversation lock wait percentiles, model requests and input tokens per turn, per-agent duration, tool-call composition, executor resolution dispositions, and scenario projection completeness.

No runtime change: `observability.usage_fields` already records `cached_input_tokens` and `lock.wait` spans already carry durations. This adds a reader, not instrumentation.

Acceptance: the script reproduces every figure in §0.2 from the two sessions named in §0.1, for both, without hand-editing.

## 2. WP2 — Move dynamic data past the cache boundary

### 2.1 The post session upgraded this from hypothesis to finding

Before `edd2fd6` the first request of each turn cached 4.6%, and routing was a plausible explanation: without `prompt_cache_key`, requests land on arbitrary cache nodes and only `previous_response_id` keeps a turn's own chain together.

`prompt_cache_key` shipped in #103 and is now live. Within a turn the hit rate rose to 77.3% and 79.0%. **The first request of a turn fell to 0.0% — 19 requests, 387,171 input tokens, nothing reused.** Routing is therefore not the obstacle. The boundary is structural.

`app/providers/openai_provider.py` composes `instructions = f"{static_system}\n\n{dynamic_system}"`, and `dynamic_system` carries HP/SAN, location and retrieval context, so it differs on every turn. The prefix match must break where it begins, and nothing after it — including the 9,527-token tool schema — can be reused across turns.

That the cached prefix begins with `instructions` is supported by the pre session, where requests after the first cached a median of 19,810 tokens, exceeding `static + dynamic + tools` (7,972 + 1,284 + 9,527 = 18,783) and therefore reaching past the tool definitions into the input items.

### 2.2 Change

Send `instructions` as `static_system` alone. Carry `dynamic_system` as the first input item.

```text
before  [static 7,972 + dynamic 1,284][tools 9,527][input]
                          ^ changes every turn; everything after is uncacheable

after   [static 7,972][tools 9,527][dynamic 1,284][user]
                                    ^ boundary moves here
```

Cross-turn cacheable ceiling rises from 7,972 to 17,499 tokens.

### 2.3 Constraint that makes this non-trivial

`run_conversation` has two input shapes. Without `previous_response_id` it sends the selected history plus the user message. With `previous_response_id` it sends only `[{"role": "user", "content": new_message}]` and relies on `instructions` to carry current state.

Moving `dynamic_system` out of `instructions` removes that guarantee. The chained branch must deliver the dynamic block as an input item on the first request of a turn and inherit it through the chain; `_commit_turn_result` already invalidates the response chain between turns, so a new turn re-sends it. **An implementation that moves the block without covering the chained branch leaves the model reading stale HP/SAN — a correctness failure, not a performance regression.**

### 2.4 Out of scope for this work package

Reordering `build_executor_static_prompt` / `build_narrator_static_prompt` from `INSTRUCTION + keeper_static_prompt` to `keeper_static_prompt + INSTRUCTION`, so the Narrator can reuse the 7,131-token block the Executor just warmed, is a separate and smaller change. It became more attractive in the post session — Narrator-shaped requests fell from 18.3% to 5.1% — but it is deferred so the two effects remain attributable, and because it moves a role instruction behind 7,131 tokens of content, which is a behavioural change requiring its own evaluation.

### 2.5 Acceptance

- Offline: the dynamic block reaches the model in both input shapes, including every request of a chained turn; a fake provider asserts the composed request, not a helper's return value.
- Measured: WP1's script against a post-change session, read against §0.2's post column.
- Correctness: current HP/SAN/location present in every request that previously carried them. A cache improvement that loses state is a failure.
- Reversible: a single flag restores the previous composition.

## 3. WP3 — Bound and measure conversation queueing

### 3.1 Measured problem, unchanged by the merged fixes

| conversation lock wait | Pre | Post |
|---|---|---|
| median | 0 ms | 0 ms |
| p90 | — | 15,886 ms |
| p99 | 54,706 ms | 56,948 ms |
| max | 63,343 ms | 56,948 ms |
| over 1 s | 11 of 175 | 6 of 22 |

The lock is held from message receipt through Executor, Narrator, Guard, spoiler scan and log commit — both model round trips included. `_conversation_lock_with_notice` sends one notice after 10 s and then nothing, so at p99 a player sits for another 45 seconds with no further signal and no idea whether they are first or third in line. The post session made this worse in practice, because the Executor's median rose to 15.7 s and its p90 to 35.3 s, which is exactly the time the next player spends queued.

Neither the existing spec nor the three planning documents targets this. `enhancement-conversation-lock-and-tool-loop-latency.md` deliberately does not narrow the lock. v1 F8.1 keeps per-conversation gameplay serialized and moves only read-only commands out, which does not touch a wait measured between gameplay turns. v2 UX.4 defines a `T_turn_queue` metric and UX.5 asks for player-starvation measurement; nothing implements either.

### 3.2 What this work package does

1. Emit a `turn.queue` event carrying the measured wait, the number of waiters ahead, the route and the speaker role. This implements v2's `T_turn_queue` and makes starvation quantifiable per player rather than per conversation.
2. Track waiter depth on `_ObservableConversationLock` and include position in the queued notice, refreshing it at a bounded interval instead of going silent for the rest of a p99 wait.

Observation and messaging only. No lock is narrowed, no turn is parallelised, no state contract changes.

### 3.3 What this work package does not do, and why

Releasing the conversation lock after the Executor commits — so the Narrator's median 5.0 s runs outside it — is the obvious structural win, and this document does not propose it yet. For an ordinary turn the Narrator holds `tools=[]`, so no mutation would escape the lock, and `_commit_turn_result` already reloads under the state lock and rejects a stale timeline. The blocking risk is ordering, not mutation: the next player's Executor would resolve against state whose narration has not yet been posted, so a player could read an outcome referencing an event they have not been told about.

That risk is what #97's mutation admission and #99's delivery envelopes exist to bound. This work package records the proposal and its preconditions and defers the change until those land and WP1's queue metric can show whether it helps.

### 3.4 Acceptance

- `turn.queue` appears for every queued turn with a wait, a depth and a route, and never for an uncontended acquire.
- The notice reports position and refreshes at a bounded interval; a wait resolving before the first notice still sends nothing.
- Notice delivery failure still cannot affect lock release, which the existing implementation guards and this must not regress.
- WP1's script reports queue-wait percentiles split by route and by speaker role.

## 4. WP4 — Account for the retrieval round trips the fixes added

### 4.1 Measured problem

The post session restored correctness by searching more. `search_scenario` is **34 of 43 tool calls (79%)**; the rest are `skill_check` 4, `get_character_sheet` 3, `purchase_items` 1, `npc_skill_check` 1.

```text
executor tool_call_count per turn:  0:4  1:2  2:3  3:3  5:1  7:1  8:1
turns over 30 s:  60.6 s with 8 tool calls
                  35.3 s with 7 tool calls
```

Each additional tool round is another model request carrying the whole prefix. Per turn the session reached a median of 4 requests and a maximum of 10, and a maximum of 263,484 input tokens for a single turn. `MAX_TOOL_ITERATIONS` is 12 in the deployment, so a turn can still grow further.

This is a real trade-off, not a defect: the ranked-candidate work and the focused follow-up guidance are what moved `incomplete` from 55% to 7%. But it is now the dominant latency term, and it was not present in any of the three planning documents, which predate these merges.

### 4.2 Scope

This work package is **investigation before design**. It must first establish, from WP1's tool-call composition output, whether the extra searches are:

- the same evidence fetched repeatedly because a follow-up query was reworded, which `79514ec`'s delivered-fragment tracking was meant to prevent and which would be a defect;
- genuinely new evidence the earlier behaviour skipped, which is the intended correctness gain and must not be removed;
- budget-driven retries, visible as `retrieval_budget_exceeded`, which WP2 partially addresses by freeing prefix budget.

No change is proposed here until that split is measured. Reducing searches without knowing which bucket they fall into would trade back the correctness this session just demonstrated.

### 4.3 Acceptance

- WP1's script reports, per turn, the search queries issued, the record ids returned, and the overlap between successive searches within one turn.
- The three buckets above are quantified over at least one further session before any change is specified.

## 5. Sequencing

```text
WP1  reproducible baseline  -> no runtime change
WP2  cache boundary         -> evidence strongest; flag-reversible
WP3  queue visibility       -> independent of WP2; lock narrowing stays deferred
WP4  retrieval round trips  -> investigation only until the split is measured
```

WP2, WP3 and WP4 touch different files and may be reviewed separately.

## 6. Limits

- The post session is 15 executor turns, 77 usage-bearing requests and 22 lock acquisitions over 18 minutes of one game. Percentiles at p99 rest on a handful of observations, and `complete_for_action` at 1 of 2 is too small to call a trend.
- The two sessions differ in content as well as code. The latency rise is consistent with the tool-call composition, but a same-scenario comparison was not run.
- WP2 rests on an inference about where the provider's cached prefix begins, drawn from cached-token totals exceeding `instructions + tools`. It is consistent with the data and not confirmed against provider documentation, which is why WP2 is flag-reversible and measured rather than assumed.
- This document does not review rule correctness in `combat.py`, `dice.py` or `luck.py`, the PDF ingestion path, or the Discord UI layer.
