# main_v2 architecture overview and deep review (player experience first)

[繁體中文](main_v2_architecture_review_zh.md)

Baseline: `main_v2` at `d6f13c3` (after #179–#187). Scope: all of `app/` (47,024 lines), 152 test files (2,740 tests), `.github`, `docs/`.

> **Stance: player experience comes first.** A tidy architecture is never a reason to add to a player's wait, remove feedback they can see, or make it possible for a channel to hang. When code can be made easier to maintain **without touching the player path**, do it. When it touches the player path, measure first, change second, and make the before/after visible.

## Implementation status

The numbers in this document (line counts, function lengths, cycle counts) describe the review baseline `d6f13c3`. Since then:

| Roadmap item | Finding | Status | PR |
| --- | --- | --- | --- |
| P0 one summary line per turn (independent of `LOG_ENABLED`), a short code on internal errors | F2, F7 | **merged** | [#195](https://github.com/marcoliu99/line-coc-keeper/pull/195) |
| P0 one boolean parser, configuration guide, README lists the new modules | F8 (part), F15 | **merged** | [#196](https://github.com/marcoliu99/line-coc-keeper/pull/196) |
| P2 split `keeper.py`: prompt construction | F9 | **merged** | [#189](https://github.com/marcoliu99/line-coc-keeper/pull/189) |
| P2 split `keeper.py`: turn commit, memory maintenance | F9 | **merged** | [#192](https://github.com/marcoliu99/line-coc-keeper/pull/192) |
| P2 split `keeper.py`: tool dispatch, `keeper_tools/support`, import gate; delete `keeper.py` | F9 | **merged** | [#193](https://github.com/marcoliu99/line-coc-keeper/pull/193) |
| P3 pin the persistent buttons' `custom_id` | F11 | **merged** | [#190](https://github.com/marcoliu99/line-coc-keeper/pull/190) |
| P3 split `discord_bot.py` (1,862 → 311 lines) | F11 | **merged** | [#194](https://github.com/marcoliu99/line-coc-keeper/pull/194) |
| P1 `reply_pipeline`, `run_turn` in six stages, `TurnScope` + lock-order test + watchdog | F4, F5 | not started | — |
| P3 `handle_system_command` as a table | F10 (cold path) | not started | — |
| P4 latency levers (narration outside the lock, tool surface, retiring legacy combat, provider loop core, synchronous SQLite) | F1, F3, F6, F10, F13 | not started; needs data from #195's summary and one real five-player run first | — |
| P5 cold-path packaging, `GroupState` sub-states | F12, F14 | not started | — |

What the finished items achieved:

- `app/keeper.py` (1,532 lines, fan-out 31) is **deleted**, replaced by `prompt_builder`, `turn_commit`, `memory_maintenance`, `tool_dispatch` and `keeper_tools/support`; the 12-module cycle between `keeper_tools` and `keeper` is gone and `tests/test_architecture_keeper_tools.py` keeps it from coming back. The number of `app/` files carrying an `SLF001` exemption fell from 16 to 7.
- `app/discord_bot.py` went from 1,862 to 311 lines, the rest living in `app/discord_transport/` (`gateway`, `delivery`, `interactions`, `lifecycle`, `controls`, `help_ui`), with `tests/test_architecture_discord_transport.py` checking the layering and importing each module alone. In review, Codex found two real problems in the first version — an import cycle inside the transport package (importing one module alone failed, masked by the normal start-up order) and tests that patched the wrong module binding — both fixed and each now guarded by a test.
- Everything above is a pure move or added output; **no latency improvement is claimed.**

## 0. One-page summary

1. **The architectural backbone is healthy.** Game-state writes have one door (`state_transaction`); checks and combat each have an engine; stage hand-offs have a typed "who may write which key" contract (`TurnPayload`/`CheckStatus`); deterministic last-line safety exists; and 6 "architecture gate" tests reject regressions through the import graph. No rewrite, storage change or framework swap is warranted.
2. **What players feel most is waiting, not code structure.** Measured (Camp Sunny, 500 turns): sequential p50 26 s / p95 59 s; five players speaking together p50 63 s / p95 121 s, longest queue wait 106 s. A turn holds the lock for about 21.7 s: Executor (the model loop that decides mechanics) median 15.7 s, Narrator about 5 s, median 4 and up to 10 model requests. **So the most valuable architecture work is making that time visible and reducing serial model round trips and the time other players are locked out.**
3. **Production cannot see these times by default.** `LOG_ENABLED` defaults to `false`, so `turn.phases` (the phase timeline from #184) emits nothing. That is the first gap to close (§4 F2): cheapest and least risky.
4. **Maintenance debt sits in three hubs**, all splittable mechanically with no player-visible change: `app/keeper.py` (1,532 lines, fan-out 31, a 12-module cycle with `keeper_tools` through lazy imports), `app/discord_bot.py` (1,862 lines mixing event entry, delivery, persistent buttons and Help/sudo/PDF views) and `run_turn` (a 277-line procedural script whose reply post-processing order lives inline).
5. **Several things look like optimisation targets but measurement says leave them**: the single-row JSON state (a 900 KB state loads in 0.5 ms and serialises in 0.8 ms); per-turn dynamic tool filtering (breaks the prompt-cache prefix); pre-emptively moving every synchronous SQLite call into a thread (no measurement behind it). See §7.

Suggested order (§6): **P0 make it visible (always-on one-line turn summary + doc corrections) → P1 split reply post-processing and `run_turn` into ordered stages (behaviour unchanged) → P2 split `keeper.py` → P3 split `discord_bot.py` → P4 latency levers, once there is data (narration outside the lock, tool surface, retiring legacy combat).**

## 1. Principles and the "player-path rules"

### 1.1 Player-experience invariants (no refactor may break these)

| # | Invariant | What guarantees it today |
| --- | --- | --- |
| U1 | The typing indicator shows immediately on receipt | `discord_bot.on_message` enters `_best_effort_typing` before taking any lock |
| U2 | A wait over 10 s is acknowledged and the position refreshed as the queue drains (up to 3 notices) | `router._delayed_queue_notice`, `_QUEUE_ACK_*` |
| U3 | The reply is sent first; maintenance runs afterwards in the background and never blocks the next player | `post_turn.run_post_turn_maintenance_after_output` → `spawn_post_turn_maintenance` |
| U4 | Check/Luck buttons survive a restart; duplicate clicks are rejected rather than queued into a second roll | `DynamicItem` buttons (`timeout=None`), `locks.try_acquire_check`, the `state_actions` ledger |
| U5 | When a model fails, committed changes are kept and the player gets a clear, actionable message — no silence, no re-roll | `turn_fallback` (12 reasons), the narrator failure path, `LLM_TURN_DEADLINE_SECONDS=180` |
| U6 | Players see table language: Chinese tier names, no internal ids, the real party size | `app/presentation.py` (#185) |
| U7 | Sending the same action twice takes effect once | `state_transaction` `action_id`/ledger |
| U8 | Private information reaches only the right person; spoilers are blocked | `spoiler_policy`, `turn_delivery.finalize`, private-message isolation |
| U9 | Scenario retrieval starts while the turn is still queued | `supervisor.prefetch_retrieval` (spec WP3.3) |
| U10 | What can be answered from state does not call a model (an outstanding Luck decision) | `supervisor` `turn.short_circuit` (WP5) |

### 1.2 Player-path rules (for any change to a "hot path" module)

**Hot path** = code a player turn passes through: `discord_bot` (event and button entry), the `commands/router` text path, `locks`, `agents/*`, `services/{turn_*, post_turn, presentation, prompt_config}`, `providers/*`, `keeper` (tool dispatch and commit), `repositories/state_transaction`, `checks/*`, the `combat` engine (during combat), and the search side of `scenario_rag`/`memory_rag`.

1. **Add no serial LLM request.** A new model judgement must merge into an existing request or move to the background.
2. **Do not lengthen lock hold time.** Work that needs no state protection goes outside the lock (see prefetch and maintenance).
3. **Add no synchronous work over 5 ms on the event-loop thread** (SQLite, JSON, regex scans over large text).
4. **Player-visible text must not change as a side effect of a refactor**, unless that change is the goal and is tested.
5. **Every hot-path PR attaches a before/after `turn.phases` summary** (`exclusive_ms` of `queue_wait`/`executor_llm`/`narrator_llm`/`other`). With no data, say so.
6. **Failure must be bounded**: any new retry, extra lookup or wait has a count or time cap and is observable (`turn.fallback` or an event).

**Cold path** (scenario import, PDF, authoring and review, building Help UI, character creation, map building, background maintenance) may be refactored to ordinary architecture standards, but must not hold a lock the hot path uses for longer than one short read/write.

## 2. Current architecture

### 2.1 Layers and hot/cold paths

```text
                            ┌──── cold path (import / authoring / UI construction; not on a player turn) ────┐
                            │ scenario_* (13%)   pdf_* / markitdown (5%)   pregen_extractor   help_*          │
                            │ scene_map   creation   services/scenario_{ingestion,lifecycle}                  │
                            └───────────────▲─────────────────────────────────────────────────────────────────┘
                                            │ read-only, only through scenario_library
Discord ──► discord_bot ──► commands/router ──► handlers/*  (commands: parse + reply)
              │  typing, queue notices, buttons (DynamicItem)
              ▼
        ┌────────────────────────── hot path: one player turn ──────────────────────────┐
        │ locks (conversation → keeper turn → narration)  +  priority gate               │
        │                                                                                │
        │ supervisor.run_turn                                                            │
        │   context_builder ─► (scenario_rag / memory_rag / presentation facts)          │
        │   intent_router (rules, no model)                                              │
        │   executor ─► provider tool loop ─► tool_gateway ─► keeper._execute_tool       │
        │                                          └► keeper_tools/registry (61 tools)   │
        │   turn_fallback (bounded recovery)   turn_resolution / turn_handoff (checks)   │
        │   narrator ─► provider                                                         │
        │   consistent → guard → consistent → obligation_gate → party size              │
        │   → turn_delivery.finalize → presentation.player_text                          │
        │   keeper._commit_turn_result  ─► state_transaction                             │
        └──────────────┬──────────────────────────────────────────┬──────────────────────┘
                       │ rules engines (pure, dice via a port)    │ background (after the reply)
                       ▼                                          ▼
            checks/{service,rules,luck,events}   post_turn → memory maintenance / summary / embedding backfill
            combat engine (legacy | managed)     correction_adjudication / correction_summary
            dice  event_obligations
                       │
                       ▼
        repositories/state_transaction  ──►  group_state  ──►  db (SQLite)   [GroupState: 55 fields, one JSON row]
```

Size (`app/`, 47,024 lines): scenario 13%, discord + commands + help 13%, combat 10%, other services 10%, state + persistence 7%, turn/agents + keeper 7%, keeper_tools 7%, providers 7%, pdf 5%, checks 3%, memory 2%. **The cold path is about three tenths of the code; the hot-path core (agents + keeper + providers + checks + state) a little over two tenths** — which is why the risk/return of refactoring the hot path differs entirely from refactoring the cold path.

### 2.2 Life of a turn (measured)

| Stage | What it does | Locks held | Measured |
| --- | --- | --- | --- |
| Receive | `on_message`: typing, request context | — | — |
| Prefetch | scenario retrieval starts while queued (read-only) | — | ~1 s, absorbed by the queue |
| Queue | wait for the conversation lock (FIFO); notices from 10 s | — | five players: up to 106 s |
| Build context | `build_context`: state, retrieval, memory | conversation + keeper turn | ≈ 1 s |
| Executor | model tool loop (≤5 rounds, ≤4 tools), really mutates state | same | median 15.7 s (p90 35 s) |
| Recovery | fallback turns only: one extra lookup + one re-run | same | only turns that would have become a generic reply |
| Narration hand-off | `to_narration()` (**off by default**) | releases mutation locks, takes the narration lock | — |
| Narrator | no tools; one model request | conversation + keeper (default) | ~5 s |
| Post-processing | consistency → Guard → obligations → party size → `finalize` → `player_text` | same | ≈ 0 (pure rules; an extra request only if Guard repairs) |
| Commit | `_commit_turn_result`, one transaction | same | ≈ 0 |
| Deliver | `reply`, DMs, images; maintenance is spawned afterwards | same | bounded by Discord rate limits/timeouts |

Total lock hold is about **21.7 s** (15 measured Executor turns; a small sample, good for order of magnitude only).

### 2.3 Concurrency model: seven ordering/exclusion mechanisms

| Mechanism | Type | Scope | Purpose |
| --- | --- | --- | --- |
| `mutation_admission` | in-process hold table | conversation | after a tool worker times out or is cancelled, pauses that channel's mutations until the real worker thread settles (so a timed-out thread cannot keep writing) |
| conversation lock | `asyncio.Lock` (with queue counters) | conversation | one player action's mechanics phase at a time (FIFO) |
| keeper priority gate | custom queue | conversation | KP messages go first when a KP Assistant exists |
| keeper turn lock | `asyncio.Lock` | conversation | serialises Keeper/LLM turns |
| narration lock | `asyncio.Lock` | conversation | narration order (**only meaningful when narration-outside-lock is on**) |
| `try_acquire_check` | set | (conversation, user) | rejects a duplicate in-flight check from the same player |
| state `RLock` + `BEGIN IMMEDIATE` | threading / SQLite | conversation / DB | state transactions |

This complexity grew to serve player experience (U2, U4, U7, U9) and is logically sound. The risk is that **the failure mode is a channel stuck until restart** (the `config.py` comment and the spec both say so), and the choreography is held together mainly by docstrings and tests. See F5.

### 2.4 State and persistence

- `GroupState`: a 55-field dataclass serialised as one JSON row in SQLite `group_states`, with `schema_version` and a migration table.
- One write door: `state_transaction.mutate` (lock → `BEGIN IMMEDIATE` → read latest → validate timeline/ledger/revision → mutate → invariants → write state + events + action result → commit). `tests/test_architecture_state_writes.py` rejects other writers.
- **Measured**: for a 900 KB synthetic state (200K-character scenario + 160 log entries), `from_dict` takes 0.5 ms and `to_dict` + `json.dumps` 0.8 ms. **The single-row JSON has no meaningful effect on latency.**
- The log is bounded (`MAX_LOG_TURNS`, trimming at 4×); trimmed content goes into the summary and memory RAG.

### 2.5 Rules engines

`checks/` (injected `DicePort`, one settlement, one event record), `combat` (legacy) / `combat_flow` (managed) / `services/combat_engine` (the single entry; the mode is read once), `event_obligations` + `obligation_gate` (SAN/damage/forced checks the scenario states explicitly, settled in the same turn after narration), `turn_resolution` (deterministic hand-off validation: an Executor claim is not completion). This is the best-quality part of the project: pure rules, injectable dice, architecture-gate tests.

### 2.6 Knowledge layer

`scenario_rag` (BM25 + optional embeddings, bounded adjacent-chunk expansion), `memory_rag` (trimmed history; bounded embedding parts and retry caps), `scenario_library` (the only storage interface for sources and variants). Retrieval is prefetched before the Executor, outside the lock.

### 2.7 Provider layer

Four adapters (Anthropic/Gemini/OpenAI/Codex) each implement their own `run_conversation` tool loop (164–252 lines), sharing `retry`, `turn_budget` (whole-turn deadline), `admission` (OpenAI rate) and `conversation_session`. Anthropic and OpenAI have prompt caching (measured: 77–79% hit from the second in-turn request).

### 2.8 Coupling, quantified

| Metric | Value | Reading |
| --- | --- | --- |
| Module-level (top-level) import cycles | 1 (`commands` ↔ `handlers.uploads`, a package-`__init__` matter) | very clean |
| Function-level lazy imports | 88 | hide the cycles below |
| Cycles including lazy imports | 5: `keeper` ↔ `keeper_tools/*` ↔ `turn_context` (12 modules), `providers/*` (8), the `scenario_library` cluster (5), `router` ↔ `buttons` ↔ `pending_buttons` (3), help (2) | #1, #2 and #4 are on the hot path |
| Top fan-out | `keeper` 31, `discord_bot` 31, `router` 30 | hubs |
| Top fan-in | `models` 54, `observability` 54, `config` 42, `mutation_admission` 24 | reasonable shared base |
| Longest functions | `handle_system_command` 641, `run_turn` 277, `openai_provider.run_conversation` 252, `run_executor` 245, `_handle_text_message_impl` 207 | need splitting |
| Files exempted from `SLF001` (cross-module private access) | 17 | `keeper._*` alone is referenced from 9 files in 35 places |
| Settings | 47 `_env_*` plus several inline parsers | see F8 |
| Architecture-gate tests | 6 (checks, combat, corrections, legacy, scenario_store, state_writes) | a good practice to extend |

## 3. Designs to keep (do not tidy these away)

1. **FIFO conversation lock + queue notices + position updates**: players can see where they stand.
2. **Reply first, maintenance after**: maintenance never blocks the next player.
3. **Prefetched retrieval outside the lock**, with `build_context` re-validating the binding inside it.
4. **One state write door + action ledger**: double clicks, resends and roll continuations are safe.
5. **Deterministic last line** (`finalize`, `player_text`, `turn_fallback`): when a model errs, the player gets a clear message and committed changes stay.
6. **Rules engines independent of transport and models** (held by architecture-gate tests).
7. **Persistent buttons** (`DynamicItem`) and idempotent replies to stale-button replay.
8. **Single-row JSON state** (see the §2.4 measurement).
9. **`intent_router` uses rules, not a model**: one fewer serial request.

## 4. Findings

Severity = impact on players; each item says whether it touches the hot path.

### F1 (high, hot path) The mechanics phase serves one player at a time, with many serial model round trips

- **Evidence**: `router._handle_text_message_impl` → `_conversation_lock_with_notice`; `supervisor.run_turn`; measurements in §2.2. `NARRATION_OUTSIDE_MUTATION_LOCK` defaults to `false` (`config.py:306`, `.env.example:160`); the spec estimates enabling it takes the median hold from ~21.7 s to ~15.7 s. The complexity of `TurnHandoff`, the narration lock and `narrating_turn` is already paid for; the benefit is not collected.
- **Player impact**: with five players speaking together, the last waits for everyone's Executor.
- **Recommendation**: do not just flip the flag. In order: (1) do F2 first; (2) in one real five-player run record `turn.queue` and `turn.phases`; (3) add F5's hold watchdog; (4) enable `NARRATION_OUTSIDE_MUTATION_LOCK` on a test channel and compare `queue_wait` p50/p95 and check for ordering errors; (5) change the default only if that passes. Note `supervisor` keeps the mutation phase when the evidence may state a mechanic (`obligation_candidates`), so the real gain depends on how many turns are like that — look at data.
- **Do not**: run Executor and Narrator in parallel (the Narrator needs the Executor's deterministic result); split the lock so different players run concurrently (it introduces `state_revision` conflicts and turn-order problems with no measurement behind it).

### F2 (high, operations) Latency is invisible by default

- **Evidence**: `LOG_ENABLED` defaults to `false`; `observability.event` returns immediately when it is false; `turn_phases` emits `turn.phases`/`turn.phase` through it. The measurement tool built in #184 therefore leaves nothing under the default configuration.
- **Recommendation**: add an **always-on** one-line turn summary (INFO, no text content): `turn_id`, `wall_ms`, `queue_wait_ms`, `executor_ms`, `narrator_ms`, `requests`, `tools`, `route`, `fallback_reason`. Cost is one string format per turn; it does not need full structured logging (token/byte counters are what `LOG_ENABLED` exists to avoid).
- **Value**: the precondition for every later latency decision (F1, F3, F4, F6).

### F3 (medium-high, hot path) Every Executor request carries 57 tool schemas, and two combat tool families are exposed together

- **Evidence**: under the default configuration and a player speaker, `tools_for_speaker_role` returns 57 tools, 33,043 characters of schema; `combat` (12 tools, 7,554 chars) plus `managed_combat` (21 tools, 7,443 chars) are about 45% of it. `keeper._tools_for_speaker_role` does not filter by state. The Executor static prompt is about 19.7K characters on an empty state.
- **Trade-off**: the tool list is part of the cached prefix; filtering per turn by state would vary the prefix and defeat caching (the spec's WP2 experiments show a stable prefix is what reaches 85–93% hit). Sending fewer tokens is not automatically faster.
- **Recommendation**: (a) no per-turn dynamic filtering; (b) if shrinking, only **two stable modes** (non-combat / combat), swapping the prefix only at mode switches; (c) after legacy combat retires (F13), remove the 12 legacy tools; (d) validate with an experiment of the kind `scripts/experiments/ab_prompt_cache_boundary.py` already is, measuring first-request cache hit and Executor median — **no improvement, no merge**.
- **Unverified**: cache behaviour on the Codex provider path; actual first-request token cost per provider.

### F4 (medium-high, hot path) Reply post-processing is an ordered pipeline over the *whole* text, with the order hidden inside `run_turn`

- **Evidence**: `supervisor.run_turn` is 277 lines, and the order `consistent → guard → consistent → obligation_gate → enforce_party_size → finalize → player_text` is decided by inline code (#181 and #185 each inserted a step). Every step has a "must come before/after X" reason, but it lives only in comments.
- **Player impact**: the pipeline needs the complete text, so replies cannot be streamed; that is what the safety boundary costs, not an oversight. The maintenance risk is that the next person adding a step puts it in the wrong place and a later step edits already-validated text (#185 placed the party-size correction before `finalize` for exactly this reason).
- **Recommendation (behaviour unchanged)**: collect the steps into one **ordered list** in `reply_pipeline.py`: each step is `(name, fn, must_precede=…)`, plus a test asserting the order and that only lossless steps follow `finalize`. Split `run_turn` into `prepare → mechanics → narrate → gate → commit → deliver`, with **the number and order of awaits exactly unchanged**.
- **Product option (your call; not recommended without data)**: on long turns, send the "mechanics result / check button" before the narration so players have something to do sooner. The cost is message ordering and redefining what `finalize` guarantees for mechanics text.

### F5 (medium, hot path) The lock choreography fails by hanging a channel and is held together by documentation

- **Evidence**: the `TurnHandoff` docstring in `locks.py` says an unreleased conversation lock "deadlocks the channel until the process restarts"; `router` has 10 sites of `async with _conversation_lock_with_notice(...)`/priority gate, each repeating the pattern; spec WP3.5 records several lock-order problems found only in review.
- **Existing mitigation**: `LLM_TURN_DEADLINE_SECONDS=180`, `DISCORD_REQUEST_TIMEOUT_SECONDS`, `finally` releases.
- **Recommendation**: (1) wrap "take locks → hand off → release → post-turn hook" in one `TurnScope` that the router uses without touching locks itself; (2) add a lock-order test (conversation → keeper turn → narration; any violation fails); (3) add a **log-only, never auto-release** hold watchdog: holding past a threshold (e.g. deadline + 60 s) emits `lock.held_too_long` with the holder's turn id. Do not force-release: that turns "stuck" into "two turns mutating state at once".

### F6 (medium, hot path, needs measurement) Synchronous SQLite on the event-loop thread

- **Evidence**: `router` calls `load_state` directly; `supervisor` calls the synchronous `keeper._commit_turn_result` directly. CPU cost is small (§2.4); what is unmeasured is the SQLite commit (disk sync) and blocking when a worker thread holds the state `RLock` — which blocks the event loop for **every channel in the process**.
- **Recommendation**: first read p99 of the existing `state.save`/`state.load` spans in a real environment; if >50 ms, move the commit into `asyncio.to_thread` (tools already work that way). **Do not move everything to threads up front**: each hop has a cost and changes exception/cancellation semantics.

### F7 (low-medium, hot path) Generic failure messages carry no reportable identifier

- **Evidence**: `discord_bot` answers an unexpected exception with "發生內部錯誤了，請稍後再試；詳細資訊已記錄到 Bot log。"; `StateRevisionConflict` asks the player to "try again".
- **Recommendation**: append a 4–6 character turn code (from the existing `turn_id`) so the KP can find it in the log; keep `turn_fallback`'s actionable wording style. No flow change.

### F8 (medium, configuration) 47 settings, two boolean parsers, important flags off by default

- **Evidence**: `_env_bool`/`_env_int` coexist with inline `os.environ.get(...).lower() in (...)` (e.g. `SCENARIO_RAG_ENABLED`, `DEBUG_SHOW_INTERNAL_IDS`, `TURN_FALLBACK_RECOVERY_ENABLED`); `SCENARIO_RAG_ENABLED=false` (whole scenario in the prompt) and `true` (retrieval) have entirely different latency profiles, and the latency analysis (`search_scenario` at 79% of tool calls) concerns the latter.
- **Recommendation**: one parser; a "supported configurations" table in `docs/guides/` (recommended production, test channel, and each default); CI runs at least a subset with `SCENARIO_RAG_ENABLED=true` and with `NARRATION_OUTSIDE_MUTATION_LOCK=true` so flags are not tested on only one side for long.

### F9 (medium, maintenance) `keeper.py` is a hub

- **Evidence**: 1,532 lines, fan-out 31; it holds static/dynamic prompt construction (~300 lines of prompt text), state-mutation wrappers (`_mutate_and_save_state`), a check-result cache, turn commit (`_commit_turn_result`), memory maintenance (~300 lines), KP canon trigger parsing, tool dispatch (`_execute_tool`, already down to 33 lines) and the combat-status gate. 9 `keeper_tools/*` files call back into `keeper` through lazy imports, forming a 12-module cycle; `keeper._*` is referenced from 9 files in 35 places (17 files carry `SLF001` exemptions).
- **Recommendation (mechanical moves, behaviour unchanged; implemented, see Implementation status)**: `prompt_builder.py` (prompts), `turn_commit.py` (`_commit_turn_result`, `_commit_kp_ooc_turn_result`, timeline guarantees), `memory_maintenance.py` (maintenance and summaries), `tool_dispatch.py` (`_execute_tool` and role filtering), `keeper_tools/support.py` (what the handlers share). With each group moved, rename the `_` names to public and delete that file's `SLF001` exemption. **No long-lived shims** (a transitional shim gets a deletion date). Hot-path protection: function bodies and call order are untouched; the existing tests plus a new import-graph gate (`keeper_tools` must not import `keeper`) verify it.

### F10 (medium, maintenance) Over-long functions

- **Evidence**: §2.8. `handle_system_command` is 641 lines (an if-chain acting as a dispatch table); the four providers' `run_conversation` (164–252 lines each) repeat the same loop skeleton.
- **Recommendation**: turn `handle_system_command` into a subcommand → handler table (cold path, safe); extract the shared loop core (`iterate_tool_loop`) and leave each provider only request-format conversion and response parsing. **The latter touches the hot path**: use the existing provider contract tests plus one real-call A/B confirming `requests`/`tools`/latency are unchanged.

### F11 (medium, maintenance) `discord_bot.py` mixes four responsibilities

- **Evidence**: 1,862 lines: event entry (`on_message`), delivery (`_make_reply` etc.), persistent buttons (Check/Luck/PDF/Help, ~400 lines), and Help/sudo/PDF `View`/`Modal`/`Select` classes (~500 lines).
- **Recommendation (implemented as `app/discord_transport/{gateway,delivery,interactions,lifecycle,controls,help_ui}.py`, see Implementation status)**: the original proposal was `discord/events.py`, `discord/delivery.py`, `discord/buttons.py`, `discord/views_help.py`, `discord/views_sudo.py`, `discord/views_pdf.py`. **Note**: persistent buttons are matched by `custom_id` regex, so **the formats must not change** (already-posted buttons must keep working, U4). A pure move, plus a test that pins every `custom_id` template.

### F12 (low-medium, maintenance) `GroupState` is a 55-field object

- **Evidence**: `models.py` (1,454 lines); fields span scenario, combat, checks, corrections, maps, commerce and upload staging.
- **Recommendation**: split by **composition** into sub-states (`ScenarioState`, `CombatState` [exists], `CheckState`, `CorrectionState`, `UploadState`) with the `to_dict`/`from_dict` JSON shape **unchanged** (`schema_version` and the migration table already exist). Do it opportunistically while touching a region, not as its own project; **do not change storage for performance** (§2.4).

### F13 (medium, maintenance / hot path) Legacy and managed combat are two implementations side by side

- **Evidence**: `combat.py` 1,765 + `combat_flow.py` 1,598 + `combat_resources.py` 716 lines; 12 + 21 tools; new battles always go managed (`initialize_working_state`), legacy remains only for battles already in progress (`LEGACY_NEEDS_ADMISSION`); `CombatEngine` already confines "read the mode once".
- **Recommendation**: set a retirement plan: (1) use event counts to confirm recent new battles are 100% managed; (2) give in-progress legacy battles a one-time "close out or convert" flow; (3) remove the legacy tools and their prompt text (which also shrinks F3's tool surface). **Do not** just delete: battles in progress in saved games would break.

### F14 (low, maintenance) The scenario/PDF cold path

- **Evidence**: `scenario_*` + `pdf_*` + related services are about 18% of the code; `scenario_library`/`scenario_source_authoring`/`scenario_source_review`/`scenario_templates`/`trusted_scenario_source` form a 5-module cycle (via lazy imports); it is well isolated from the hot path, meeting it only through `scenario_library`'s read interface.
- **Recommendation**: move into an `app/scenario/` package and break the cycle (push shared types and path rules down into the library layer). Low priority, low risk; do not mix with hot-path PRs.

### F15 (medium, documentation) Standards and overview documents have drifted from the code

- **Evidence**: `CODING_STANDARDS.md` still says "`_execute_tool` is about 1,180 lines" (it is 33, dispatching through the registry) and "the provider map is copied into 9 modules" (there is now `providers/registry`; about 3 remnants: `guard`/`context_builder`'s `getattr(config, f"{PROVIDER}_MODEL")` and `discord_bot`'s prewarm table); the `README.md` Architecture section mentions none of the modules added by #179–#187 (`turn_fallback`, `event_obligations`, `obligation_gate`, `turn_phases`, `presentation`, `scenario_adjacency`, `embedding_execution`, `memory_chunking` appear 0 times).
- **Recommendation**: once this document lands, reduce the README Architecture section to a summary pointing here, and state the "why" in the standards without perishable line counts.

### F16 (low, tests) Timing-sensitive tests

- **Evidence**: `tests/test_agentic_pipeline.py:165` asserts `elapsed < 0.15`; it failed once under load during this review (passes alone).
- **Recommendation**: use an injected clock, or assert concurrency by call count/order, not wall time.

## 5. Target architecture

Not a rewrite: make the existing boundaries explicit and guard them with tests.

### 5.1 Target layers (arrows = allowed dependency direction)

```text
 L5  transport   discord_bot (entry) + discord_transport/{gateway,interactions,delivery,lifecycle,controls,help_ui}  ← no game rules
 L4  routing     commands/router (+ TurnScope)  commands/handlers/*  ← parse, permissions, reply
 L3  turn runtime agents/*  (prepare → mechanics → narrate → gate → commit → deliver)
                 reply_pipeline (ordered, pure)   turn_fallback   turn_phases
 L2  rules/domain checks  combat(engine)  dice  event_obligations  turn_resolution  presentation
                 keeper_tools(registry, handlers)  prompt_builder
 L1  state & knowledge  state_transaction → group_state → db      scenario_library(read)  scenario_rag  memory_rag
 L0  foundation  config  observability(+ turn summary)  locks  models/domain  providers(adapters + shared tool-loop core)

 cold path (side)  app/scenario/*  pdf  pregen  help construction  → meets the hot path only via L1's scenario_library, state_transaction
```

Rules: (1) dependencies only point down (L5→L0); (2) L2 must not import L3/L4/L5; (3) `keeper_tools` must not import `keeper` (replaced by `prompt_builder`/`tool_dispatch`); (4) the hot path (L3) must not import the cold path (the library read interface excepted); (5) rules engines must not import providers. Each of the five becomes an import-graph gate test, in the style of `tests/test_architecture_*.py`.

### 5.2 Turn pipeline (internal shape, behaviour unchanged)

```text
run_turn(...) =
  scope = TurnScope(conversation)                # take locks / hand off / release / post-turn hook (the router knows only this)
  ctx   = prepare(state, text, prefetched)       # build_context, intent, grounding reuse
  mech  = mechanics(ctx)                         # executor → recover(≤1 search + ≤1 re-run) → resolve → handoff
  scope.to_narration()  (flag on and no obligation candidates)
  text  = narrate(ctx, mech)                     # narrator
  text  = reply_pipeline.run(text, ctx, mech)    # ordered list: consistent, guard, consistent, obligations, party, finalize, player_text
  commit(ctx, text)                              # single transaction
  deliver(...)                                   # reply first, then background maintenance
```

The number and order of `await`s at each step is identical to today's: this is "split the function", not "change the flow".

### 5.3 Where modules go

| Now | Goes to | Note |
| --- | --- | --- |
| `keeper.py` prompt construction | `prompt_builder.py` | prompt text unchanged |
| `keeper.py` commit / timeline | `turn_commit.py` | |
| `keeper.py` memory maintenance | `memory_maintenance.py` | background; unrelated to the hot path |
| `keeper.py` tool dispatch | `tool_dispatch.py` | same layer as `registry` |
| `discord_bot.py` | `discord_bot.py` (entry and events) + `discord_transport/*` | done; `custom_id` formats unchanged |
| the 641-line function in `commands/handlers/system.py` | subcommand table | cold path |
| `scenario_*`, `pdf_*` | `app/scenario/`, `app/pdf/` | cold path, last |
| `combat.py` + `combat_flow.py` | kept; trimmed once legacy retires | F13 |

## 6. Roadmap (each step has a player-experience gate)

| Phase | Content | Touches hot path? | Gate / acceptance |
| --- | --- | --- | --- |
| **P0** | Always-on one-line turn summary (F2); turn short code in errors (F7); README / standards corrections (F15); supported-configuration table (F8) | output only | unit tests; confirm a line is emitted with `LOG_ENABLED=false`; added cost per turn <0.1 ms |
| **P1** | `reply_pipeline` + `run_turn` into six stages (F4); `TurnScope` + lock-order test + watchdog (F5); timing-sensitive tests onto an injected clock (F16) | yes (structure only) | all 2,740 existing tests pass; new order test; `await` count unchanged (fake-provider call-count assertions); `turn.phases` same before/after |
| **P2** | Split `keeper.py` (F9), deleting `SLF001` exemptions group by group; add the "`keeper_tools` must not import `keeper`" gate | yes (pure move) | same; one group per PR |
| **P3** | Split `discord_bot.py` (F11); turn `handle_system_command` into a table (F10, cold part) | partly (`custom_id` is immutable) | `custom_id` template test; restart-replay button test |
| **P4** | **Only with data**: F1 enable narration outside the lock; F3 stable-mode tool surface; F13 retire legacy combat; F10 provider loop core; F6 per p99 | yes (behaviour/latency change) | compare p50/p95 from P0's summary and `turn.phases`; a five-player real run; any item with no improvement does not merge |
| **P5** | Cold-path packaging and cycle breaking (F14); `GroupState` sub-state composition (F12, opportunistic) | no | ordinary refactoring standards |

P0 and P1 can be completed and verified with no real environment; P4 needs real runs to conclude anything — **until then, claim no latency improvement.**

## 7. Explicit non-goals

1. **No storage change** (no table split, no database swap): single-row JSON has no meaningful latency cost (§2.4).
2. **No per-turn dynamic tool filtering**: it breaks the prompt-cache prefix (F3).
3. **No event bus / CQRS / DI framework**: more indirection and awaits, no benefit to players.
4. **No parallel Executor and Narrator**: the Narrator depends on the Executor's deterministic result.
5. **No generic retry on the player path**: bounded recovery (≤1 lookup + ≤1 re-run) and the turn deadline already exist; more retries only make slow slower.
6. **No automatic force-release of locks** (F5).
7. **No up-front blanket `to_thread`** (F6).
8. **No change to the persistent-button `custom_id` formats** (F11).

## 8. How to verify

- **Latency**: after P0, one summary line per turn; aggregate `queue_wait`/`executor`/`narrator`/`requests` by `turn_id`. Acceptance for five-player real runs follows `docs/validation/camp_sunny_latency_before_after.md` (simultaneous replies under 120 s; p50 and p95 each improved ≥20%, or evidence that the remainder is provider time).
- **Behaviour-preserving refactors**: existing tests + an `await`-count test + public-text snapshots; `ruff`, `mypy app` and `pytest` as in CI.
- **Structure**: new import-graph gates (the five rules in §5.1); the `SLF001` exemption list only shrinks.

## 9. Limits and what is unverified

- **Source of the latency numbers**: the Camp Sunny 500-turn report (as cited in the specs) and one small 15-Executor-turn session; none were re-measured in this review, and Stage 3–5 real runs have not been done.
- **Unverified assumptions**: the real gain from narration outside the lock; the effect of a stable-mode tool surface on the Codex path; p99 of synchronous SQLite commits on a real disk; whether `search_scenario` at 79% of tool calls still holds on the current version (#179–#184 changed retrieval and reuse behaviour).
- **Method**: static reading of the hot path (router, locks, supervisor, the start of executor, post_turn, the discord_bot entry and buttons, state_transaction), an AST import graph over all modules, function lengths, configuration and test statistics, and a serialisation benchmark on a synthetic state. `narrator`, `guard`, the four providers' tool loops, `combat*`, `checks/*`, `scenario_*` and `pdf_*` were reviewed at structure/size level only, not line by line; conclusions about them (F10, F13, F14) are structural judgements.
- This document is a review and proposal; **it changes no code.**
