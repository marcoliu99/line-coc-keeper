# Turn latency: pending buttons and scenario search

## Problem and goal

Three Discord runtime logs from 2026-09-25 show two avoidable sources of
player-visible delay. In the `high` reasoning run, the median completed router
span was 40.25 seconds (95th percentile 99.81 seconds). Requests that created
checks sometimes delivered the text reply and then waited for the conversation
lock again before posting the check or Luck button. Among 95 completed router
requests, 18 spent more than 10 seconds after the router completed; the longest
spent 200.56 seconds, almost entirely in subsequent lock waits. In one of
those turns, Executor made three successive `search_scenario` calls about the
same basement stairs; each retrieval took about 0.24 seconds, while the model
round trips between them took several seconds each.

The logs are:

- `/Users/marcoliu/coc_v2_log/20260925-141719-883841_runtime_profile-async.log`
  (`medium`, 89 completed router requests, median 18.54 seconds);
- `/Users/marcoliu/coc_v2_log/20260925-150958-612104_runtime_profile-async.log`
  (`high`, 95 completed router requests, median 40.25 seconds);
- `/Users/marcoliu/coc_v2_log/20260925-151704-122091_runtime_profile-async.log`
  (`max`, only five completed router requests, including a Narrator timeout
  and retry lasting 112.12 seconds).

The paired pyinstrument HTML reports spend over 99% of process wall time in
the event-loop selector. They profile the whole bot lifetime, including idle
time, so request spans in the structured logs are the latency evidence. SQLite
loads and saves usually take milliseconds; median scenario retrieval is about
0.25 seconds. `request.completed` includes work after the text reply, so the
router span and the later button-posting tail must be measured separately.

The goal for this change is to deliver a newly created check/Luck button
promptly after its turn's narration, while preserving ordered game state. A
small trial did not validate the proposed scenario-search change, so search
behavior remains unchanged in this spec. Its latency and relevance findings
are recorded below for a separate design.

## Small-scale verification (2026-09-26)

These were isolated trials using a copy of the scenario database. The model
trials used the configured OpenAI model (`gpt-6-luna`, `medium` reasoning) and
real scenario retrieval, but only `search_scenario` and a stubbed
`skill_check`; no game state was changed. One warm-up call per A/B trial was
excluded. Each arm had three scored runs, too few to estimate production
latency or judge a complete Keeper turn. Results are saved under
`/private/tmp/coc_*_small_trial_results.json` for this review.

| Trial | Current path | Candidate | What it establishes |
| --- | --- | --- | --- |
| Mocked conversation-lock contention; next turn holds lock for 250 ms, n=3 each | `_post_check_buttons` median 252.11 ms | Previously claimed direct send median 0.05 ms | The second lock acquisition creates the expected wait. Discord network time and full claim integration were mocked. |
| Real-model search flow; n=3 each | Median 11.96 s; median 5 API calls; 10 search rounds total; 1 `skill_check` call total | Batch first search and cap at two rounds: median 13.78 s; median 5 API calls; 6 search rounds total; 5 `skill_check` calls total | Fewer tool rounds did not reduce model calls or elapsed time. Expected one pending check per run was not reliable; candidate check counts were 0, 2, and 3. |
| Real-model proactive RAG, with one fixed DEX check; n=3 each | Raw Chinese-query context: Executor median 6.32 s, 3 API calls, 1 explicit search per run, 3/3 one check | English-aligned context: Executor median 4.83 s, 2 API calls, 0 explicit searches, 3/3 one check | Better initial evidence can save an Executor search round in this narrow setup. These Executor times exclude query rewrite and retrieval. |
| Chinese query against scenario text; n=3 each | Original English scenario: RAG plus Executor median 7.26 s, 3 API calls, 1 explicit search per run, 3/3 one check | Page 10 translated into Chinese, other 26 pages unchanged: median 4.22 s, 2 API calls, 0 explicit searches, 3/3 one check | The translated basement rule entered the top five RAG results in all three runs. This is a one-page, one-action trial, not a full translated scenario or full Discord turn. |

The raw Chinese action `我也走下地下室` returned five chunks, none containing
the basement stairs, Push, or fall terms needed for this ruling. Adding the
previous Chinese narration still missed them. Three model-generated English
search phrases found those terms in the top five chunks in all three runs;
rewrite median was 3.35 seconds and retrieval median 0.24 seconds. Adding
those median components to the 4.83-second aligned Executor median gives an
illustrative 8.42 seconds versus 6.32 seconds for raw context. This is an
unpaired, tiny-sample estimate, not a measured end-to-end A/B difference.
None of the three English results included the `1d6` damage term, so English
alignment alone does not prove complete scenario coverage.

The Chinese-page trial kept the same Chinese action and model prompt in both
arms. The original English scenario's proactive top five never contained all
four target markers (stairs, Push, fall, `1D6`); the Chinese-localized page
was ranked first and supplied all four in all three runs. The warmed RAG
search median was 0.03 seconds in both arms. The translated scenario index
took 2.33 seconds to build with embeddings once; that build cost is excluded
from the steady-state turn figures. The translation was a manually cleaned
rendering of the basement page, so an additional OCR-cleanup control replaced
that page with an equivalently cleaned English rendering. With the same
Chinese query, its top five still omitted the basement page and all four
markers. Retrieval results were deterministic on the repeated identical
query; the three model turns per arm are the latency samples. The fixed DEX
instruction and stubbed check test tool-loop cost, not spontaneous rules
selection, final narration, or Discord delivery. Results are recorded in
`/private/tmp/coc_chinese_scenario_small_trial_results.json`; the temporary
trial script and cleaned-English control are also under `/private/tmp`.

**Decision from the trial:** keep the button fix. Do not add the two-round
search cap, batch search schema, or a per-turn model rewrite as a latency
optimization now. The batch/cap trial did not pass its latency or one-check
gate; the rewrite's extra call can outweigh the saved Executor call. A
separate search design needs an outcome-aware replay that checks retrieval
evidence, tool calls, final ruling, and complete turn latency.
One-time translation or a bilingual scene index now has a concrete
single-scene signal worth testing separately; it is not part of this button
latency implementation. Translation accuracy, term consistency, index build
cost, and performance across other scenes remain open.

## Scope

1. Claim new pending check/Luck button intents before the current request
   releases its already-held conversation lock, then send the Discord message
   immediately after release without joining the conversation-lock queue a
   second time. Cover ordinary text, `/coc check`, `/coc luck`, and both
   existing button callbacks. Keep a fallback for routes without one outer
   conversation lock and for partial failures.
2. Record the latency from the end of turn processing to button send start
   and completion. Record a turn-level count of Executor scenario-search
   invocations using existing structured logging conventions, without query
   or scenario text. This makes later search experiments measurable without
   changing the search policy in this change.

## Non-goals

- No concurrent Keeper turns in one conversation and no removal of the outer
  conversation lock. Turn order, tool dependencies, and narration history
  remain serialized.
- No model or reasoning-effort tiering in code. The logs favor the existing
  `medium` default, but the `medium`/`high` traffic was not a controlled A/B
  run and the `max` sample is too small for a quality decision. The earlier
  `none`/`low` tiering experiment was reverted after production regressions.
- No global increase of API concurrency, shorter timeout, or reduced retry
  budget based on these logs. The admission semaphore showed negligible
  waiting; 429s and the one timeout remain separate operational concerns.
- No new scenario index, database table, state schema migration, or change to
  the scenario's access-control/spoiler policy.
- No general batching or parallel execution of state-mutating tools.
- No search batch schema, hard search-round cap, or per-turn LLM rewrite of
  player queries. The small trial did not show a safe latency win.

## Data structures and contracts

`GroupState.pending_checks` and `pending_luck_decisions` keep their existing
dict entries and `_buttons_posted` marker. The marker remains the durable
cross-request claim, excluded from legacy button identity hashes. A private,
turn-local `PendingButtonIntent` carries the kind, owner, copied entry,
timeline/identity, and optional public marker from the successful claim to
the Discord send. It is never written to the story log or database. Claims
for one completed turn should be collected and saved in one short state
transaction while the caller already owns the conversation lock.

No new Executor search state or tool schema is needed. The existing
`search_scenario` interface and read-only result contract remain intact. A
per-turn integer search count is sufficient for the new aggregate event.

## Pending-button flow

1. The Discord entry point captures its existing pre-turn snapshots. The
   router or button callback runs the turn under its existing conversation
   lock. A small completion hook runs in `finally` **inside** that lock,
   after any state mutation or reply attempt, and claims currently pending
   entries that are new or changed and not already marked `_buttons_posted`.
   It does not await Discord I/O. The hook must be wired to the lock-owning
   branches; it must not reacquire the non-reentrant lock. A failed claim is
   logged and left for the outer recovery pass rather than masking the
   original turn exception.
2. The handler releases the conversation lock and sends the claimed intents
   directly. A queued next turn may begin concurrently, but the button send
   no longer queues behind that turn's slow LLM call. Current identity checks
   still reject a button whose underlying decision was resolved or replaced
   before the player clicks it.
3. On send failure or cancellation, release only this intent's claim, using
   the existing identity comparison and claim-release helper. Cleanup must
   survive task cancellation. The outer `finally` recovery pass handles
   unclaimed entries and routes without the completion hook; it must skip
   already claimed intents, so it cannot duplicate posts or add a fresh
   lock wait to the normal path. A process crash between claim and send is
   an existing limitation; the implementation must not enlarge that window
   with other awaited work between the claim and send.
4. Preserve the same view labels, custom IDs, owner checks, and direct-send
   behavior for skill, choice, and Luck buttons. Keep a fresh state read
   between check and Luck recovery passes where a Discord await can allow
   another request to change the latter, as the current code requires.

This is a change to *when* the durable claim is made, not permission to run
two game turns at once or to send buttons while holding a network operation
inside the conversation lock.

## Executor search follow-up

`context_builder.build_context` and Executor continue to use the existing
proactive context and `search_scenario` tool. The real-model trials exposed a
retrieval-language mismatch in the basement-stairs example, but the tested
batch/cap candidate did not make the final check sequence more reliable or
faster. The earlier RAG-reuse spec's “no hard per-turn search cap” decision
therefore remains in force. Prompt wording alone is also insufficient: the
existing reuse prompt was present during the logged three-search incident.

The search count event should distinguish turns with zero, one, and multiple
explicit searches. A later search spec can use these counts and controlled
replays to evaluate one-time scenario translation, bilingual indexing, or
another no-extra-model-call alignment method. It must check that necessary
evidence and the final ruling remain correct before using a search-round
limit.

## Integration and observability

- Extend the current `app/discord_bot.py` and `app/commands/router.py`
  handoff rather than replacing locks or creating a second independent
  posting path. Reuse `_buttons_posted`, button identity helpers, and the
  stranded-claim release logic from the duplicate-button fix.
- Count explicit `search_scenario` calls at the existing Executor tool
  execution boundary. Do not change `app/agents/tool_gateway.py`,
  `app/keeper.py`, `scenario_rag.search`, or result formatting for this spec.
- Add structured events for `pending_button.claimed`,
  `pending_button.send.completed/failed`, and one
  `executor.scenario_search.summary` per turn. Record elapsed time, kind,
  count, and status as applicable; do not add player text, query text, page
  content, owner IDs, or Discord message bodies to structured events.
  Existing `LOG_TEXT_ENABLED` query logging remains separate.
- Measure final narration delivery and button delivery separately. A lower
  `request.completed` duration alone is not evidence that players got their
  buttons sooner.

## Verification plan

1. Async contention regression: queue a second slow turn before the first
   releases the conversation lock. Assert the first turn's new check and Luck
   button sends start without waiting for that second LLM turn, while game
   state processing stays serialized. Cover ordinary text, `/coc check`,
   `/coc luck`, and both button callbacks.
2. Preserve one post per decision across overlapping requests, a replacement
   decision for the same owner, legacy IDs, choice buttons, send failure,
   cancellation, and exception paths that saved a pending entry before the
   later turn failed. Verify no lock leak or stranded `_buttons_posted` flag.
3. Search instrumentation tests: assert the per-turn event reports zero,
   one, and multiple explicit `search_scenario` calls correctly, including
   tool errors, without exposing query or scenario text. Verify that the
   existing tool result and access-control behavior stays unchanged.
4. Run relevant Discord/router, Executor/RAG, provider, and state tests,
   then the full project suite and existing static checks. In a subsequent
   live log, compare button-send tail against the baseline above; report
   sample sizes and queue load. Use the search count to size a separate
   outcome-aware search experiment, not to claim a speedup from this change.

## Review decisions and tradeoffs

- **Search decision:** retain the current Executor search policy. The
  batch/two-round candidate reduced explicit searches but showed no speedup
  and produced missing or duplicate `skill_check` calls in the isolated
  replay. Translating the relevant scenario page once improved retrieval and
  reduced median isolated turn time by 3.04 seconds in the basement trial.
  It still needs broader correctness and latency testing before changing the
  scenario pipeline. A per-turn model rewrite was slower in the narrow
  one-search comparison once its estimated cost was included.
- **Button ordering:** send outside the conversation lock to avoid holding
  game state serialization during Discord network I/O. The claim is made
  before release; a button may become stale before delivery, as it can
  today, and identity checks remain authoritative at click time.
- **Crash recovery:** the existing claim-then-send crash gap remains. A
  durable outbox/lease would solve it but adds a schema and delivery worker;
  this latency change keeps the current failure rollback and does not add a
  persistent queue.
