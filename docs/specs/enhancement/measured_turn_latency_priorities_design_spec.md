# Measured turn latency priorities

[繁體中文](measured_turn_latency_priorities_design_spec_zh.md)

Status: **WP1, WP2, WP3.2–WP3.5 and WP5 implemented (WP3.5 behind a default-off flag); WP4 proposed**. Base: `main_v2` at `5961f2b`.

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

**Implemented.** `scripts/analyze_turn_latency.py` reproduces §0.2 exactly for the post session and accepts a directory for the pre corpus. Running it over all 59 logs at once shows the queueing problem is worse than the two isolated sessions suggested: n=542, p90 38,103 ms, **p99 105,950 ms, max 131,189 ms**, 177 acquisitions over 1 s. Those aggregate across runs of differing code and include test harness logs, so §0.2's per-session figures remain the comparison baseline; the corpus figure bounds how bad a queue has actually been observed to get.

## 2. WP2 — Put the per-turn block after everything that does not change

### 2.1 What the post session established, and what it did not

Before `edd2fd6` the first request of each turn cached 4.6%, and routing was a plausible explanation: without `prompt_cache_key`, requests land on arbitrary cache nodes.

`prompt_cache_key` shipped in #103 and is now live. Within a turn the hit rate rose to 77.3% and 79.0%. **The first request of a turn fell to 0.0% — 19 requests, 387,171 input tokens, nothing reused.** Routing is not the obstacle; the boundary is structural.

The original version of this work package concluded from that the fix was to move `dynamic_system` out of `instructions`. **That was wrong, and a live A/B refuted it before it was implemented.**

### 2.2 Measured against the real API

`scripts/experiments/ab_prompt_cache_boundary.py` replays recorded player messages against real state and the real tool schema, one request per turn, with the dynamic block made distinct on every turn — real play never repeats one, which is why a turn's first request caches 0.0% in the recorded sessions.

Over 50 turns, 47 of which reported usage and all 47 carrying a distinct dynamic block:

| arm | request shape | cached |
|---|---|---|
| A — today | `instructions = static + dynamic`, `tools`, `[user]` | **2.2%** |
| B — this document's first proposal | `instructions = static`, `tools`, `[dynamic, user]` | **0.0%** |
| C — diagnostic, block dropped | `instructions = static`, `tools`, `[user]` | 83.2% |
| D — adopted | `instructions = static`, `tools`, `[user, dynamic]` | **92.9%** |

Arm B was measured over its own 30-turn run and was worse than doing nothing. Leaving `instructions` is therefore not what matters; what matters is that the block sits after the player's message, so every byte that does not change per turn precedes every byte that does. Arm C shows the developer role itself is not the obstacle.

Uncached input over the 50-turn run falls from 829,642 tokens to 61,194, a 92.6% reduction. The stable cached prefix measures 17,089 tokens against a local estimate of `static 8,560 + tools 9,690`.

**Why arm B collapsed to 0.0% rather than still caching `instructions + tools` is not explained here.** The data says it did; the mechanism was not established, and no explanation is offered in its place.

### 2.3 Change

```text
before  [instructions: static + dynamic][tools][history][user]
                              ^ changes every turn; everything after it is uncacheable

after   [instructions: static][tools][history][user][dynamic]
                                                     ^ moved behind every stable byte
```

Delivered in both input shapes. The chained shape — used when the caller passes `previous_response_id` — sends only the new user message and relies on `instructions` to carry current state, so the block must be appended there too. Later iterations of one turn inherit it through the response chain along with the rest of that turn's items.

`OPENAI_DYNAMIC_PROMPT_AFTER_INPUT` restores the previous composition without touching prompt content.

### 2.4 Out of scope for this work package

Reordering `build_executor_static_prompt` / `build_narrator_static_prompt` from `INSTRUCTION + keeper_static_prompt` to `keeper_static_prompt + INSTRUCTION`, so the Narrator can reuse the 7,131-token block the Executor just warmed, is a separate and smaller change. Narrator-shaped requests cached 5.1% in the post session. It is deferred so the two effects remain attributable, and because it moves a role instruction behind 7,131 tokens of content, which is a behavioural change requiring its own evaluation.

### 2.5 Acceptance

- Offline: the block reaches the model in both input shapes, including the chained one, and sits after the player's message; a fake provider asserts the composed request. **Met.**
- The composition event does not count the block twice now that it rides in input. **Met.**
- The flag restores the previous composition exactly. **Met.**
- Measured: 2.2% to 92.9% over 50 turns with distinct dynamic blocks. **Met.**
- Correctness: current HP/SAN/location present in every request that previously carried them. **Met offline and in a live session.**

### 2.5.1 Live session

`scripts/experiments/live_narration_ab.py` plays the same turns twice through Executor, Narrator, Guard and the spoiler scan against a copy of the live database, restored between arms, with storage paths redirected before `app.config` is imported so live data is never touched.

Narration is intact under the new placement. Both arms reported the carried inventory correctly down to the revolver's six rounds, both held the player to an outstanding Investigate or Listen check before letting a later action proceed, and both kept the character in the basement storeroom rather than inventing a room. Median reply length was 103 characters against 103 over four turns, and 80 against 71 over three.

The real pipeline also confirms what the harness could not, because it chains requests within a turn through `previous_response_id`:

| | overall cached | requests that start a turn |
|---|---|---|
| today | 63.2% | **0.0%** across all five |
| WP2 | **85.8%** | 58–79%, none at zero |

Today's zeroes are exactly the turn-opening Executor request and the Narrator request; under WP2 no request in the run cached nothing.

Latency did not separate: 20.3 s against 18.7 s medians over three turns, and 19.8 s against 13.7 s over four, with the arms diverging in state as play continued — arm A created an Investigate check where arm B created Listen — so their tool counts differ and neither ordering is controlled. Nothing here supports a latency claim in either direction.

### 2.6 What this does not buy

Latency did not improve: arm A's median was 5.44 s against arm D's 5.71 s over the 50-turn run, a difference inside the run-to-run spread already visible between earlier arms. This is a token and cost result.

Whether cache-hit input still counts fully toward the rate limits that constrain this deployment was not established, so no rate-limit benefit is claimed.

The harness measures one request per turn with tools offered but never executed, so it isolates cross-turn reuse and says nothing about the within-turn tool loop, which already cached 77–79%.

### 2.7 Two harness faults found while measuring

Both produced a wrong answer before being caught, and both are now reported by the script itself.

A first run of arms C and D used only the leading recorded turns, which come from a state with no active character. The per-turn perturbation therefore did nothing, the dynamic block was constant, and arm D read as 93% — a number that proved nothing. The script now counts distinct dynamic blocks per arm and says so when there are fewer than two.

A first 50-turn run perturbed HP and SAN cyclically, so the block repeated every 35 turns and a later turn could match an earlier turn's prefix. Arm A read as 88.7% under that fault, against 2.2% once each turn was made distinct. The script now reports distinct blocks against usable requests and flags repeats.

## 3. WP3 — Bound and measure conversation queueing

### 3.1 Measured problem, unchanged by the merged fixes

| conversation lock wait | Pre | Post |
|---|---|---|
| median | 0 ms | 0 ms |
| p90 | — | 15,886 ms |
| p99 | 54,706 ms | 56,948 ms |
| max | 63,343 ms | 56,948 ms |
| over 1 s | 11 of 175 | 6 of 22 |

The lock is held from message receipt through Executor, Narrator, Guard, spoiler scan and log commit — both model round trips included. At post-session medians the hold decomposes as:

```text
build_context ~1 s + Executor 15.7 s + Narrator 5.0 s + Guard/spoiler ~0 + commit ~0
                                                              ~21.7 s
```

`_conversation_lock_with_notice` sends one notice after 10 s and then nothing, so at p99 a player sits for another 45 seconds with no further signal and no idea whether they are first or third in line. The post session made this worse in practice, because the Executor's median rose to 15.7 s and its p90 to 35.3 s, which is exactly the time the next player spends queued.

Neither the existing spec nor the three planning documents targets this. `enhancement-conversation-lock-and-tool-loop-latency.md` deliberately does not narrow the lock. v1 F8.1 keeps per-conversation gameplay serialized and moves only read-only commands out, which does not touch a wait measured between gameplay turns. v2 UX.4 defines a `T_turn_queue` metric and UX.5 asks for player-starvation measurement; nothing implements either.

### 3.2 Observation and messaging, safe now

1. Emit a `turn.queue` event carrying the measured wait, the number of waiters ahead, the route and the speaker role. This implements v2's `T_turn_queue` and makes starvation quantifiable per player rather than per conversation.
2. Track waiter depth on `_ObservableConversationLock` and include position in the queued notice, refreshing it at a bounded interval instead of going silent for the rest of a p99 wait.

No lock is narrowed, no turn is parallelised, no state contract changes.

**Implemented.** One correction found while testing: a waiter cannot recount its own position, because a plain counter cannot distinguish turns that arrived before it from turns that arrived after, and the first implementation therefore included the waiter itself. Position is now an entry-time snapshot reduced by a `completed` counter, so each finished turn removes exactly one and a turn arriving behind does not inflate it.

### 3.3 Prerequisite defect: the RAG index caches have no singleflight

`app/scenario_rag.py`'s `_index_cache` is a bare module-level dict with no lock. `get_index()` returns on a memory hit or a disk hit, but on a miss it calls `build_index()` — an embeddings round trip — then writes `scenario_indexes` through `_save_index_to_disk()` (`db.set_json`, `app/scenario_rag.py:677`). `get_record_index()` has the same shape.

Two concurrent misses for one key therefore each pay the embeddings call and each write. For one `group_id` the payload is identical so the last write is harmless, but the API cost doubles. This is already true today between conversations, because the conversation lock only serializes within one conversation; it is listed here because 3.4 widens the window to two turns of the same conversation.

Fix independently of any lock change: per-key singleflight on `get_index` and `get_record_index`, so a second caller waits for the first build rather than starting its own. This is the concrete instance of the addendum's note that `lru_cache`-style caching is thread-safe without being once-only and that expensive work needs an explicit singleflight.

**Implemented.** A `threading.Lock` per cache key, taken only on a miss so the memory and disk paths stay lock-free, with the cache re-checked inside the lock for the caller that waited. Verified with four threads racing one key: one `build_index` call and one write, against four of each without it.

### 3.4 Hoist context building out of the lock, after 3.3

`app/agents/context_builder.py`'s `build_context` performs no state writes of its own. The prefetch reads persisted memory to bind its source version. Its main cost is the scenario and memory RAG round trip, roughly 1 s, and it may run before the lock is acquired, subject to two conditions:

- 3.3 lands first, because `build_context` reaches `get_index` and can therefore trigger the unprotected rebuild path.
- The state snapshot it read is re-read after the lock is acquired, and only retrieval results are candidates for reuse. Chapter-window, source-text and memory-source bindings must still match; mutable state-derived payloads are always rebuilt.

When retrieval finishes during an existing queue wait and its sources remain valid, it removes roughly 1 s, about 5% of the lock hold. An uncontended turn may still wait for retrieval after acquiring the lock. This was initially assessed as a pure read reordering; it is not, and the reassessment is why 3.3 exists.

**Implemented, and narrower than the heading suggests.** `build_context`'s payload carries `state`, `character`, `resolved_check_events` and the correction projection — all derived from mutable state, all stale if built before the lock. Only the retrieval travels: `context_builder.prefetch_retrieval` runs the ordinary code path and keeps its `rag_context`/`memory_context` plus a binding captured before searching. That binding covers the scenario variant, chapter window, source text, timeline, combat state, active character, summary and persisted memory chunks. `build_context` re-checks it under the lock and searches again if any source moved.

`supervisor.prefetch_retrieval` owns the decision, not the router, so the query cannot drift from what `run_turn` feeds `build_context`: a mixed IC/OOC message prefetches on its IC span only. It returns `None` — leaving the search inside the lock, exactly as before — for an OOC route, for a speaker holding a Luck decision that WP5 will answer from state, and for any failure, which is logged and swallowed because a missed prefetch costs a second and never a turn.

One existing test hung rather than failed: `FakeSupervisorRunner` in `test_keeper_priority_integration` pins `run_turn`'s keyword signature, so the new argument raised inside the turn, the blocking event was never set, and the scenario waited forever. The fake now accepts it.

### 3.5 Deferred: release the lock before narration

The single lock currently protects three separate things:

| | must serialize | ordinary-turn Narrator needs it |
|---|---|---|
| mutation ordering | yes | **no** — the Narrator holds `tools=[]` |
| reply ordering | yes | yes |
| turn isolation | yes | yes |

The Narrator needs the second and third, not the first. The split that follows is:

```text
mutation lock    receipt -> build_context -> Executor -> state commit -> release
posting ticket   taken in arrival order on entry; awaited before posting
```

Narration then overlaps the next player's Executor while message order is preserved by the ticket. Together with 3.4 this would take the median hold from ~21.7 s to ~15.7 s, and p90 from ~47 s to ~35 s.

#### What re-reading the merged work changed

This section previously deferred on the grounds that the failure case — turn A's Narrator failing after turn B's Executor has already resolved against A's committed state — would be bounded by #97's mutation admission and #99's delivery envelopes. **Both have merged, and neither bounds it.**

`mutation_admission`'s holds are placed by `detach`, called only from `tool_gateway` when a tool worker times out or is cancelled. A Narrator failure leaves no hold: the Executor's workers have already settled. The machinery covers an abandoned tool worker, not a failed narration.

The failure interleaving is also not new. Today B's Executor already resolves against A's committed state, because A committed during its own Executor phase; B simply starts later. A Narrator failure already returns fallback text over state that was committed and described to nobody. Releasing earlier moves when B starts, not what B sees.

#### Two blockers that are real

**A tool-enabled Narrator mutates.** `narrator.py:44` sets `tool_enabled` for `resolved_check_followup` and `opening_fallback`, and #99 added arrival commits inside that loop. Early release is therefore only sound for an ordinary `player_action` turn whose Narrator holds `tools=[]`.

**The log commit follows narration.** `_commit_turn_result` runs after the Narrator and appends this turn's user message and reply to `state.log`, which is the history later prompts read. If B commits before A, the history is out of order. The posting ticket must therefore cover commit *and* post, not post alone.

#### Why this is not implemented here

The conversation lock is taken in `router.py` around `_handle_ordinary_text_message_locked`, which spans `run_turn` and the post-turn maintenance that posts the reply. Releasing before narration means releasing from inside `run_turn`, which has eleven return paths, and making that release idempotent against the router's own `async with`. The existing code already carries a warning about this exact hazard: a conversation lock leaked by an exception escaping the cleanup "permanently leaks a lock that *was* successfully acquired and deadlocking every future command in that conversation until the process restarts."

That is a channel-wide deadlock as the failure mode, against a measured gain of the Narrator's median 5.0 s out of a ~21.7 s hold.

#### Implemented behind `NARRATION_OUTSIDE_MUTATION_LOCK`, default off

The posting ticket in the original sketch is unnecessary. Both locks are FIFO and the mutation lock already serializes the Executors, so a turn reaches narration in the order it reached mutation and a plain second lock preserves message order:

```text
mutation lock (FIFO) -> Executor -> release -> narration lock (FIFO) -> Narrator, commit, post
                                       ^ the next player's Executor starts here
```

`locks.TurnHandoff` owns which locks a turn still holds, and the router's context managers yield one and `close()` it in their `finally`. `close()` releases exactly what is still held, so the eleven return paths inside `run_turn` need no per-path handling: a turn that never handed off is released as before, and one that did releases narration instead. `to_narration()` is idempotent.

`run_turn` hands off in one place, after the reducer, and only for `turn_kind == "player_action"`. `resolved_check_followup` and `opening_fallback` keep the mutation lock to the end because `narrator.py:44` gives them a restricted tool set and #99 commits arrivals inside it.

The flag defaults off because the gain is seconds and the failure mode is a channel that stops until restart. What is verified is the lock accounting — every test asserts which locks are free afterwards, across handing off or not, closing twice, handing off twice, and an exception after handoff — plus that the next turn's Executor overlaps this turn's narration without reordering the posts. What is not verified is behaviour under real concurrent load.

One hazard worth recording: with the narration lock leaked, the ordering test **hung rather than failed**, because the next turn waited forever. It now waits with a bound, so a leak fails in seconds. The same shape bit twice elsewhere — `FakeSupervisorRunner` pinned `run_turn`'s keyword signature, so a new argument raised inside the turn, the blocking event was never set, and the suite hung; it now tolerates added arguments.

With the KP priority gate in play the gate is still held across narration, so a conversation that has a KP assistant does not get the overlap. That is left as is.

### 3.6 Acceptance

- `turn.queue` appears for every queued turn with a wait, a depth and a route, and never for an uncontended acquire.
- The notice reports position and refreshes at a bounded interval; a wait resolving before the first notice still sends nothing.
- Notice delivery failure still cannot affect lock release, which the existing implementation guards and this must not regress.
- Concurrent misses for one index key produce one `build_index` call and one write, verified without a live embeddings backend.
- Hoisted context building re-reads state under the lock, and a test shows a mutation landing in the gap is observed rather than overwritten.
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

## 5. WP5 — Answer a held Luck decision without a model request

### 5.1 Measured problem

A 20-turn live session shows what an outstanding Luck decision costs. The decision itself is cheap: `/coc check` and `/coc luck` are routed to `handle_check_command` and `handle_luck_decision`, which resolve deterministically and never reach the Executor. What costs is every *other* message the player sends while the decision waits. Each is an ordinary turn, and nothing between the message and the Executor looks at `state.pending_luck_decisions` — the earliest reference is `supervisor.py:93`, by which point the turn is already running.

One such turn, timestamped:

```text
15:12:15.478  turn starts
15:12:15.714  Executor starts
15:12:17.819  model response #1   in=22,940     billed
15:12:17.820  tool clear_pending_check
15:12:21.017  model response #2   in=23,390     billed
15:12:21.018  executor.resolution = await_luck  <- the verdict lands here
15:12:25.265  model response #3   in=13,670     billed after the verdict
```

The verdict is not a precondition the pipeline checks; it is what `turn_resolution.validate_resolution()` concludes *from* the Executor's output, so the Executor must run first. The Narrator then runs anyway, and `prompt_config.enforce_mechanic_check_consistency` discards what it wrote and substitutes the deterministic text from `_pending_luck_fallback`, which was available before the turn began.

Across the session, 16 of 20 turns resolved `await_luck`, each costing 2–4 model requests, 36,000–86,000 input tokens and 10–18 seconds, and holding the conversation lock throughout.

Note also that the model called `clear_pending_check` in that turn: it was trying to cancel the outstanding check, which `luck_takes_precedence` correctly refused. Blocking earlier removes the attempt as well as the cost.

### 5.2 Why Luck and not a pending check

A Luck decision is a closed state for its own holder: the dice are already rolled, so until they decide, nothing they say can change that outcome, and answering deterministically removes no judgement the model could have made.

**It is not closed for the table.** An earlier draft of this section claimed `turn_resolution.py:148` encodes that nothing may precede a Luck decision. Re-reading it, `luck_takes_precedence` keys on the **waited-for** party, not the speaker, so a player who holds a decision may still legitimately defer to another player's outstanding check. An existing test, `test_referenced_check_not_overridden_by_other_players_luck`, covers exactly that and failed against the first implementation. The gate is therefore skipped whenever anyone else holds a pending check or decision.

A pending check is not closed. It has not been rolled, and a player may legitimately withdraw it — `turn_resolution` has a whole `cancelled` path that verifies `clear_pending_check` actually ran and that no other state moved. **Blocking messages on a pending check would break cancellation**, so this work package does not touch it.

### 5.3 Scope

Before the Executor, for the acting player only:

| situation | behaviour |
|---|---|
| that player holds a pending Luck decision, nobody else is waiting, and they send a new gameplay action | answer from `pending_luck_reply`, zero model requests |
| anyone else holds a pending check or decision | unchanged; deferring to another player must stay possible |
| `/coc luck hard` / `skip`, `/coc check` | unchanged; already deterministic |
| status, sheet, help | unchanged; these never reached the Executor |
| another player's turn | unchanged; the decision is per-user |
| KP or sudo | unchanged in this work package |

### 5.4 The router decides what is blocked, not this work package

This was raised as a decision needing sign-off, on the assumption that blocking in-character speech would cost play. Two things closed it.

First, the classifier it was reasoned about no longer exists. #99 replaced it with `route_request`, which returns a `RouteDecision` and adds `PLAYER_OOC` alongside `PURE_ROLEPLAY` and `GAMEPLAY_ACTION`. The gate now reuses the decision the Supervisor already computes rather than a helper of its own, and only fires on `GAMEPLAY_ACTION`:

```text
我往樓梯走過去        GAMEPLAY_ACTION   answered from state
好                    PURE_ROLEPLAY     ordinary turn
（等我想一下）        GAMEPLAY_ACTION   answered from state — bare parentheses are IC
(ooc: 等我想一下)     PLAYER_OOC        ordinary turn, on #99's OOC path
為什麼要擲骰          PLAYER_OOC        ordinary turn — a rules question gets an answer
我的角色卡有什麼技能  PLAYER_OOC        ordinary turn
```

That is a better outcome than the version this document first specified: a player asking why they must roll now receives an answer instead of a Luck prompt.

Second, on the old classifier the question was close to moot anyway — across 17 recorded logs and 206 classifications, `PURE_ROLEPLAY` occurred zero times. Those logs predate #99, so they say nothing about how often `PLAYER_OOC` will fire; that is worth re-measuring with WP1's script once #99 has run in play.

### 5.5 Acceptance

Implemented. `tests/test_pending_luck_short_circuit.py` fails every stage downstream of the gate, so a test passes only if none of them ran — not even `build_context`, which would otherwise spend a retrieval round trip before the turn is refused.

- A held decision plus a gameplay action from its owner is answered with no model request and no context build. **Met.**
- A different player acting, a pending check with no decision, a resolved-check follow-up, a KP assistant turn, and pure roleplay all still run the ordinary pipeline. **Met.**
- Anyone else holding a pending check or decision keeps the full pipeline. **Met**, and covered by the pre-existing multi-player test that refuted the first implementation.
- `turn.short_circuit` is emitted once, with a reason. **Met.**
- `/coc luck` and `/coc check` are unaffected: neither reached the Executor before this change.

Mutation-checked: removing the gate fails 2 tests, removing the multi-player narrowing fails 2 including the pre-existing one, and removing the classifier condition fails the pure-roleplay test.

One existing test had to change. `test_supervisor_passes_existing_pending_luck_and_suppresses_new_roll` set up a pre-existing decision, which is now answered from state; its assertions still passed while `run_narrator` was never entered at all. It now creates the decision mid-turn, which is the path that still reaches the Narrator and the one worth covering.

### 5.6 Limits

The 16-turn run overstates the frequency: the harness resolved pending checks but not Luck decisions, so it simulated a player who never answers. Real play answers in one to three turns. The per-turn cost is the same either way, and the lock is held for all of it.

This measures one session on one scenario. No claim is made about how often a Luck decision is outstanding in general play.

## 6. Sequencing

```text
WP1   reproducible baseline   -> no runtime change
WP2   cache boundary          -> evidence strongest; flag-reversible
WP3.3 index singleflight      -> independent defect; no lock change; do before 3.4
WP3.2 queue observability     -> independent of everything else
WP3.4 hoist context building  -> after 3.3
WP3.5 release before narration-> after #97 and #99
WP4   retrieval round trips   -> investigation only until the split is measured
WP5   held Luck decision      -> removes requests outright; needs the §5.4 decision
```

WP2, WP3 and WP4 touch different files and may be reviewed separately. Within WP3, 3.2 and 3.3 are independent of each other and of WP2; 3.4 depends only on 3.3; 3.5 depends on work outside this document.

## 7. Limits

- The post session is 15 executor turns, 77 usage-bearing requests and 22 lock acquisitions over 18 minutes of one game. Percentiles at p99 rest on a handful of observations, and `complete_for_action` at 1 of 2 is too small to call a trend.
- The two sessions differ in content as well as code. The latency rise is consistent with the tool-call composition, but a same-scenario comparison was not run.
- WP2 rests on an inference about where the provider's cached prefix begins, drawn from cached-token totals exceeding `instructions + tools`. It is consistent with the data and not confirmed against provider documentation, which is why WP2 is flag-reversible and measured rather than assumed.
- This document does not review rule correctness in `combat.py`, `dice.py` or `luck.py`, the PDF ingestion path, or the Discord UI layer.

## 8. PR #109 review corrections

Goal: preserve current-turn evidence and message admission order while retaining prompt caching and retrieval outside the conversation lock. This patch changes no persisted schema, combat rules, or purchase policy.

1. **Response-chain fallback.** Build the initial request input and invalid-chain retry from one helper. Both include the current dynamic developer block exactly once when `OPENAI_DYNAMIC_PROMPT_AFTER_INPUT` is enabled. The token reservation must count the rebuilt input exactly once.
2. **Prefetch validity.** Bind scenario results to the active chapter window and scenario text revision. Bind memory results to a revision that changes when memory maintenance commits. A changed binding discards the prefetch and performs the ordinary retrieval under the lock. Tests advance a chapter and append memory while a turn waits.
3. **Admission order.** Register an ordinary turn in the conversation's ordering queue before awaiting its prefetch. Retrieval may run while it waits, but a later completed prefetch cannot pass an earlier message. The configured KP Assistant retains priority over waiting players. Cancellation removes its ticket.
4. **Queue position.** Count waiters at both the KP priority gate and the conversation lock, without double-counting a turn that has crossed from the gate to the lock. The notice and `turn.queue` use the same snapshot and update as turns finish.

Acceptance: targeted regression tests for invalid response chains, chapter and memory changes, intentionally delayed prefetches with FIFO/KP priority, cancellation, and gate queue counts; then the isolated full suite, Ruff, mypy, and `git diff --check`. No additional model request or synchronous review stage is introduced.## 9. Re-measured with a five-investigator party

Everything above was measured against the database's current groups, which hold **one** character each. The recorded sessions had three to five speakers — Mick 53 turns, Marco 52, Ken 39, 馬可先生 31 — so the per-turn figures were a solo game and the queue figures were not.

`scripts/experiments/make_party_state.py` seats a five-investigator party in a sandbox copy. `live_narration_ab.py --round-robin` rotates the speaker, as a table plays.

### 9.1 Lock waits scale with the party, as expected

| speakers in the log | lock wait p99 | max |
| --- | --- | --- |
| 3 (Ken/Marco/Mick) | 131,178 ms | 131,189 ms |
| 3 (Ken/Mick/馬可先生) | 48,734 ms | 55,085 ms |
| 2 | 29,513 ms | 29,513 ms |

The p99 this document reported is not an outlier; it is what a table of three already produces.

### 9.2 WP2 is stronger with a party, not weaker

| | solo | five |
| --- | --- | --- |
| dynamic block | 1,343 | **2,125** |
| cacheable prefix (static + tools) | 17,477 | 18,066 |
| arm A cached | 2.2% | **0.0%** |
| arm D cached | 92.9% | **86.0%** |

The party's vitals grow the block that sits at the cache boundary by 58% while the stable prefix barely moves, so today's composition loses everything rather than most of it.

### 9.3 WP5 fires, and costs nothing when it does

Turn 16, Nora holding her own decision: **0 requests, 0 tokens, 0.0 s**, against 5.5 s for the equivalent turn under today's code. One occurrence in twenty turns is not a frequency estimate.

### 9.4 Cost by disposition, twenty party turns

| disposition | turns | requests/turn | input/turn |
| --- | --- | --- | --- |
| `incomplete` | 5 | 6.0 | 155,107 |
| `blocked` | 5 | 3.8 | 87,937 |
| `await_check` | 3 | 4.7 | 105,597 |
| `deferred` | 3 | 2.7 | 54,779 |
| `no_mechanics` | 2 | 2.0 | 36,848 |
| `await_luck` | 1 | 1.0 | 24,233 |
| short-circuit (WP5) | 1 | **0.0** | **0** |

Refusals — `deferred`, `blocked`, and most `incomplete` — are 13 of 20 turns and the bulk of the tokens. That is the shape of a multiplayer game: one player mid-decision refuses the rest of the table, and each refusal costs a full pipeline.

### 9.5 A deterministic cross-player refusal does not survive the data

The obvious extension of WP5 is: while any player holds a Luck decision, answer everyone else from state too. Of ten turns by other players while a decision was outstanding, **nine were refused anyway** — three `deferred`, three `blocked`, three `incomplete`.

The tenth was not. Turn 12, marco: "我檢查一下自己身上還有什麼東西" resolved `no_mechanics` and was answered properly. A deterministic gate would have refused a legitimate inventory question.

Whether a turn can proceed while someone else is mid-decision is the Executor's judgement, not a state fact, and the same state produced `deferred`, `blocked`, `incomplete` and `no_mechanics` across these twenty turns. **Not implemented.** The refusal cost is real and is the largest single waste this document has measured, but it cannot be recovered by reading state.

### 9.6 An open question this raised about WP2

Arm A produced no `deferred` turns; arm D produced three. The arms also diverged in state as play continued — arm A's first turn cancelled a check where arm D's created one — so the cause is not isolated, and this is one scenario.

It is nonetheless the instruction-following risk §2.6 said was unverified: the block moved from `instructions`, where it took precedence, to a `developer` message after the player's line. **A controlled comparison from an identical state, with the same pending items, is owed before WP2 ships.** Cache and delivery were verified; judgement was not.

### 8.7 The controlled comparison, first attempt: inconclusive by design fault

`scripts/experiments/controlled_disposition_ab.py` restores the database to one snapshot before every turn, so both compositions see identical state, identical pending items and the same message. Four cases, three repeats each, twenty-four turns.

```text
case              today                       WP2
clean             incomplete 1, blocked 2     incomplete 2, blocked 1
other_has_check   incomplete 2, blocked 1     blocked 3
other_has_luck    incomplete 1, blocked 2     incomplete 1, blocked 2
self_has_check    incomplete 1, blocked 2     incomplete 2, blocked 1
```

**It shows nothing, for two reasons, both mine.**

The probe message named a desk. The current scene is a basement storeroom with board walls, so every one of the twenty-four turns was correctly refused and the run had no way to detect a difference in how permissive either composition is. The script now carries two probes, one the scene supports and one it does not.

The verdict compared `Counter` equality, which at three samples reports a one-of-three ratio shift as a difference. Every cell produced the same two dispositions under both compositions; only the proportions moved. The script now reports whether the *set* of dispositions differs, prints counts without calling them a finding, and says plainly that this sample size cannot separate a composition from the model's own variance.

One result does survive: **no `deferred` appeared in either arm, in any case.** The 0-against-3 signal from §8.6 did not reproduce from identical state, which points at the arms' state divergence rather than the prompt composition. That removes the evidence for the concern; it does not clear WP2, which still owes a comparison with a probe that can succeed.

### 8.8 The controlled comparison, repaired: WP2 is the more consistent of the two

Two probes, two cases, four repeats each, thirty-two turns, the database restored to one snapshot before every turn.

| probe | case | today | WP2 |
| --- | --- | --- | --- |
| supportable | clean | `await_check` 2, `incomplete` 1, `no_mechanics` 1 | **`await_check` 4** |
| supportable | other holds Luck | `await_check` 2, `incomplete` 1, `no_mechanics` 1 | **`await_check` 4** |
| unsupported | clean | `blocked` 3, `incomplete` 1 | `blocked` 2, `incomplete` 2 |
| unsupported | other holds Luck | `blocked` 2, `incomplete` 2 | `blocked` 3, `incomplete` 1 |

On an action the scene supports — examining the board wall the scenario describes — the moved block produced the mechanically correct outcome, an Investigate check, on four of four attempts in both cases. Today's composition managed two of four. The other two were worse outcomes, not different ones: once `no_mechanics`, narrating the wall without offering a roll, and once `incomplete` reporting "marco 尚未擲骰的檢定已取消" — a check created and then cancelled.

On an action the scene does not support, both compositions refuse, with the same two dispositions and a one-of-four ratio difference that this sample cannot read.

**This is the comparison §2.6 said was owed, and it does not find the regression §8.6 suspected.** Nothing here shows the moved block weakening the model's judgement; on the one cell with room to differ it was steadier. Four samples per cell is small, and the claim is only that a degradation did not appear where one was looked for.

Taken with §8.7, the `deferred` divergence has no support left: it did not reproduce from identical state, and under a probe that can succeed the moved block is if anything more decisive.
