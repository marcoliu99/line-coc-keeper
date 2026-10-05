# Turn phase timeline and retrieval amplification

[繁體中文](turn_latency_instrumentation_design_spec_zh.md) | [Docs index](../../README.md)

## Status and goal

Category: `enhancement`. Status: **implemented (instrumentation and reuse; latency not measured)**. Source: finding CS-006 of the 5-player / 500-turn Camp Sunny validation (Workstream E). Stacked on the Workstream B and C changes (`fix/camp-sunny-event-obligations`).

Five-player burst replies took up to 137.8 s (p95 120.8 s) and the router queue waited up to 106.5 s; the run made 935 scenario searches for 500 normal turns (1.87 per turn). The spec asks to profile first and only then optimise. This change adds the profile, and makes only the reductions that are safe without one.

## Contract

1. **A phase timeline per turn** (`app/services/turn_phases.py`). Each turn (its timeline begins when the player's wait began: the queue, or the retrieval that ran before the turn reached the supervisor), each roll continuation and each memory-maintenance pass reports one `turn.phases` event and a `turn.phase` event (DEBUG) per span, with `turn_id`, `player_id`, `campaign_id`, start, end and duration. Phases: `queue_wait`, `initial_retrieval`, `executor_llm`, `tool_execution`, `recovery_retrieval`, `continuation_processing`, `narrator_llm`, `memory_search`, `memory_write`, `embedding`, `other`. Spans are recorded where the work happens (the router's lock wait, the prefetch that ran while the turn queued, the Executor and Narrator model calls, tool execution, each embedding batch, the recovery search, the memory commit) and follow the turn into worker threads.
2. **No double counting.** Phases overlap (a tool runs inside the Executor's model call; the prefetch runs while the turn queues). A summary therefore reports, per phase, its own merged span (`total_ms`) and the time only it accounts for (`exclusive_ms`; inner work wins over the outer wait that contains it), plus `wall_ms`, `other` and `overlap_ms`. The exclusive times add up to the wall clock.
3. **The continuation of a roll reuses its action's evidence** (`context_builder.remember_grounding` / `reusable_grounding`, switch `RETRIEVAL_REUSE_FOR_FOLLOWUPS`). A successful scenario search of an action turn is kept per conversation, timeline and player (an unsuccessful search drops that player's earlier entry, so a continuation never inherits an earlier, different action's evidence) and handed to the continuation instead of a second search of the same scene, unless it is older than `RETRIEVAL_REUSE_TTL_SECONDS` (900), any other turn in the conversation has searched since, or anything the search depended on has changed (scenario, chapter window, summary, memory, timeline, combat state, character). A roll and its continuation go from two proactive scenario searches to one; `rag.followup_grounding` reports whether it was reused.
4. **A bounded search loop.** The Executor may call the scenario search tool at most `SCENARIO_SEARCH_MAX_PER_TURN` (5) times in a turn; the allowance is the turn's, shared by an Executor retry and the recovery search, and the next call is refused with `scenario_search_limit_reached` and logged as `executor.scenario_search.limit_exceeded`.

## Not changed

The evidence the proactive search gathers is already shared by the Executor and Narrator, and the prefetch already runs before the lock; both stay. The narrow-lock goal (E5) is not advanced: without a profile showing what the lock holds the time for, narrowing a serialization that protects state commits would be a guess. A per-turn cache of identical search queries (E2) is not added: a repeated query is answered with the fragments already delivered, so replaying the first answer would resend what the turn already has.

## Enforcement

`tests/test_turn_phases.py`: the accounting (nesting, gaps, merged spans, clipping, unknown phases), recording with and without a timeline, from worker threads and concurrently, a reporting failure that never fails a turn, the seeded queue wait and prefetch, the continuation label, the router recording its queue wait, every reuse condition (failed search, other turn, other player, changed scenario/summary/memory, expiry, bound), the supervisor remembering and reusing, the switch, and the search limit.

## Not covered

**No latency was measured.** The acceptance targets (no burst reply above 120 s; p50 and p95 improved by 20% against 26.0 s and 120.8 s) need the five-player real-runtime run, which could not be run here (no Codex login, no Discord). What exists now is the instrument that run must use and two reductions in work; the first real run should read `turn.phases` before anything else is tuned.
