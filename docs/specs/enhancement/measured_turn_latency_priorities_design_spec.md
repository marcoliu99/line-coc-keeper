# Measured turn latency priorities

[繁體中文](measured_turn_latency_priorities_design_spec_zh.md)

Status: **proposed; awaiting design approval**. Base: `main_v2` at `5961f2b`.

## 0. Why this document exists

Three planning documents already describe latency work for this project: `main_v2_detailed_fix_plan.md`, `main_v2_detailed_fix_plan_ux_latency_v2.md` and `main_v2_python_optimization_safety_addendum.md`. Each states explicitly that it did not run the test suite, a real Discord session, or any LLM performance evaluation. Their constraints are sound and this document does not revisit them. What they lack is measurement, so their ordering is unverified.

This document supplies the measurement and reorders the work accordingly. It covers three work packages. It does not propose a new agent, a new database, an early-exit rule, or any change to the correctness contracts those documents establish.

### 0.1 Evidence base

All figures below come from 17 runtime logs in `~/coc_v2_log` covering 2026-09-25 to 2026-09-27: 323 requests carrying a request id, 156 model requests carrying usage, 175 conversation-lock acquisitions. Local CPU figures are direct benchmarks against a real 85 KB persisted state, not profiler attribution. Nothing here was measured after `edd2fd6`, so every number is a pre-deployment baseline.

### 0.2 What the measurements changed

| Claim under review | Source | Measured | Verdict |
|---|---|---|---|
| Local CPU work (result assembly, token counting, snapshot building) belongs in the first batch | addendum P3/P4/P5 | 6–10 ms per turn against a 16,000 ms turn — 0.04% | Demote |
| `select_history()` repeats `estimate()` and is worth caching | addendum P4 | 0.07 ms for the whole log | Demote |
| `_encoding()` currently uses `lru_cache` | addendum P4 | Replaced in #102 by an explicit cache with a retry deadline | Stale |
| A normal tool turn is `b + 1` Executor requests plus one Narrator request | v2 UX.3 | 3–5 model requests per turn observed | Confirmed |
| Static prompt stable, dynamic data placed after it, cache verified by usage | v2 UX.5-D | Not implemented; `prompt_config.py` static assembly is untouched in #97 and #99 | Adopt as WP2 |
| Per-conversation gameplay stays serialized; read-only commands bypass the lock | v1 F8.1 | Agreed, but the measured wait is between gameplay turns, which a read-only bypass does not touch | Gap — WP3 |

Local CPU per turn, benchmarked directly (worker-thread work included, which a main-thread profiler does not attribute):

```text
keeper._build_static_prompt        0.01 ms
keeper._build_dynamic_prompt       0.10 ms
input_budget.estimate(static)      0.84 ms
input_budget.estimate(tools)       1.22 ms
input_budget.select_history(log)   0.07 ms
GroupState.from_dict               0.13 ms
state.to_dict() + json.dumps       0.14 ms
                        per turn   6–10 ms   (0.04% of a 16 s turn)
```

## 1. WP1 — Establish a measured baseline before changing anything

`edd2fd6` merged #102, #103 and #104, and no bot process has run since. Every cache figure in this document predates `prompt_cache_key`. Changing prompt structure before that baseline exists would confound the two effects and make neither attributable.

Deliverable: `scripts/analyze_turn_latency.py`, read-only over a runtime log directory, reporting exactly the metrics this document's decisions rest on:

```text
cache hit rate by request position within a turn   (baseline: 4.6% / 71.4% / 64.7%)
cache hit rate by stage                            (baseline: executor 51.6%, narrator 18.3%)
conversation lock wait percentiles                 (baseline: p99 54,706 ms, max 63,343 ms)
model requests and input tokens per turn           (baseline: 3–5 requests, 87k–113k tokens)
per-agent duration                                 (baseline: executor 9.5 s, narrator 6.2 s median)
scenario projection completeness                   (baseline: complete_for_action 0/6)
```

No runtime change: `observability.usage_fields` already records `cached_input_tokens`, and `lock.wait` spans already carry durations. This work package adds a reader, not instrumentation.

Acceptance: the script reproduces every baseline number in this document from the existing logs, and is run once against a post-`edd2fd6` session before WP2 begins.

## 2. WP2 — Move dynamic data past the cache boundary

### 2.1 Measured problem

Cached prefix length is bounded by the first byte that changes between requests. `app/providers/openai_provider.py` composes `instructions = f"{static_system}\n\n{dynamic_system}"`, and `dynamic_system` carries HP/SAN, location and retrieval context, so it differs on every turn.

Within one turn, requests after the first cache a median of 19,810 tokens, which exceeds `static + dynamic + tools` (7,972 + 1,284 + 9,527 = 18,783). Caching therefore reaches past the tool definitions into the input items, which places `instructions` at or near the front of the cached prefix.

Across turns the match must break where `dynamic_system` begins, so nothing after it — including the 9,527-token tool schema — can be reused. The structural ceiling for cross-turn caching is 7,972 tokens, and the observed first-request hit rate is 4.6% over 42 requests and 814,162 input tokens.

### 2.2 Change

Send `instructions` as `static_system` alone. Carry `dynamic_system` as the first input item instead.

```text
before  [static 7,972 + dynamic 1,284][tools 9,527][input]
                          ^ changes every turn; everything after is uncacheable

after   [static 7,972][tools 9,527][dynamic 1,284][user]
                                    ^ boundary moves here
```

Cross-turn cacheable ceiling rises from 7,972 to 17,499 tokens.

### 2.3 Constraint that makes this non-trivial

`run_conversation` has two input shapes. Without `previous_response_id` it sends the selected history plus the user message. With `previous_response_id` it sends only `[{"role": "user", "content": new_message}]` and relies on `instructions` to carry current state.

Moving `dynamic_system` out of `instructions` removes that guarantee. The chained branch must be changed so the dynamic block is delivered as an input item on the first request of a turn and inherited through the chain, and `_commit_turn_result` already invalidates the response chain between turns, so a new turn re-sends it. An implementation that moves the block without covering the chained branch would leave the model reading stale HP/SAN — a correctness failure, not a performance regression.

### 2.4 Out of scope for this work package

Reordering `build_executor_static_prompt` / `build_narrator_static_prompt` from `INSTRUCTION + keeper_static_prompt` to `keeper_static_prompt + INSTRUCTION`, so the Narrator can reuse the 7,131-token block the Executor just warmed, is a separate and smaller change (Narrator baseline 18.3%). It is deliberately deferred so the two effects remain attributable, and because it moves a role instruction behind 7,131 tokens of content, which is a behavioural change requiring its own evaluation.

### 2.5 Acceptance

- Offline: the dynamic block reaches the model in both input shapes, including every request of a chained turn; a fake provider asserts the composed request rather than asserting a helper's return value.
- Measured: WP1's script run against a post-change session, read against the post-`edd2fd6` baseline, not against the figures in this document.
- Correctness: current HP/SAN/location must be present in every request that previously carried them. A cache improvement that loses state is a failure.
- Reversible: a single flag restores the previous composition without a redeploy of prompt content.

## 3. WP3 — Bound and measure conversation queueing

### 3.1 Measured problem

The conversation lock is held from message receipt through Executor, Narrator, Guard, spoiler scan and log commit — both model round trips included.

| conversation lock wait | value |
|---|---|
| median | 0 ms |
| p99 | 54,706 ms |
| max | 63,343 ms |
| over 1 s | 11 of 175 (6.3%) |
| over 10 s | 8 |

A player waited 63 seconds before their turn began, then a further ~16 seconds for it to run. `_conversation_lock_with_notice` sends one notice after 10 s and then nothing, so at p99 a player sits for another 45 seconds with no further signal and no idea whether they are first or third in line.

Neither the existing spec nor the three planning documents targets this. `enhancement-conversation-lock-and-tool-loop-latency.md` deliberately does not narrow the lock. v1 F8.1 keeps per-conversation gameplay serialized and moves only read-only commands out, which does not touch a wait measured between gameplay turns. v2 UX.4 defines a `T_turn_queue` metric and UX.5 asks for player-starvation measurement; nothing implements either.

### 3.2 What this work package does

1. Emit a `turn.queue` event carrying the measured wait, the number of waiters ahead, the route and the speaker role. This implements v2's `T_turn_queue` and makes starvation quantifiable per player rather than per conversation.
2. Track waiter depth on `_ObservableConversationLock` and include position in the queued notice, then refresh it at a bounded interval instead of going silent for the rest of a p99 wait.

Both are observation and messaging only. No lock is narrowed, no turn is parallelised, no state contract changes.

### 3.3 What this work package does not do, and why

Releasing the conversation lock after the Executor commits — so the Narrator's median 6.2 s runs outside it — is the obvious structural win, and this document does not propose it yet. For an ordinary turn the Narrator holds `tools=[]`, so no mutation would escape the lock, and `_commit_turn_result` already reloads under the state lock and rejects a stale timeline. The blocking risk is ordering, not mutation: the next player's Executor would resolve against state whose narration has not yet been posted, so a player could read an outcome that references an event they have not been told about.

That risk is exactly what #97's mutation admission and #99's delivery envelopes exist to bound. This work package therefore records the proposal and its preconditions, and defers the change until those land and WP1's queue metric can show whether it helps.

### 3.4 Acceptance

- `turn.queue` appears for every queued turn with a wait, a depth and a route, and never for an uncontended acquire.
- The notice reports position and refreshes at a bounded interval; a wait that resolves before the first notice still sends nothing.
- Notice delivery failure still cannot affect lock release, which the existing implementation already guards and this must not regress.
- WP1's script reports queue-wait percentiles split by route and by speaker role.

## 4. Sequencing

```text
WP1  measure          -> no runtime change; gives the post-edd2fd6 baseline
WP2  cache boundary   -> measure again before and after; flag-reversible
WP3  queue visibility -> independent of WP2; lock narrowing stays deferred
```

WP2 and WP3 touch different files and may be reviewed separately. Neither may be merged without WP1's baseline, because both are justified by numbers that predate `edd2fd6`.

## 5. Limits

- Every baseline figure predates the deployment of #102, #103 and #104. They bound the problem; they do not predict the improvement.
- The sample is one deployment over three days: 35 executor turns, 156 usage-bearing requests, 175 lock acquisitions. Percentiles at p99 rest on a handful of observations.
- WP2 rests on an inference about where the provider's cached prefix begins, drawn from cached-token totals exceeding `instructions + tools`. It is consistent with the data and not confirmed against provider documentation, which is why WP2 is flag-reversible and measured rather than assumed.
- This document does not review rule correctness in `combat.py`, `dice.py` or `luck.py`, the PDF ingestion path, or the Discord UI layer.
