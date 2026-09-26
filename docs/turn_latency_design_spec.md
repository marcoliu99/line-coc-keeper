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

The goal is to deliver a newly created check/Luck button promptly after its
turn's narration and to remove repeated same-scene scenario searches that
consume extra model iterations, while preserving ordered game state and
scenario correctness.

## Scope

1. Claim new pending check/Luck button intents before the current request
   releases its already-held conversation lock, then send the Discord message
   immediately after release without joining the conversation-lock queue a
   second time. Cover ordinary text, `/coc check`, `/coc luck`, and both
   existing button callbacks. Keep a fallback for routes without one outer
   conversation lock and for partial failures.
2. Offer Executor a bounded, one-round way to retrieve several distinct
   facts about the current scene. Add a turn-local policy that prevents a
   succession of equivalent `search_scenario` queries; allow one explicit
   follow-up for a distinct missing fact. Keep the existing proactive RAG
   context and its reuse policy.
3. Record the latency from the end of turn processing to button send start
   and completion, and count Executor scenario-search invocations and
   duplicate/follow-up decisions without logging new scenario text.

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

## Data structures and contracts

`GroupState.pending_checks` and `pending_luck_decisions` keep their existing
dict entries and `_buttons_posted` marker. The marker remains the durable
cross-request claim, excluded from legacy button identity hashes. A private,
turn-local `PendingButtonIntent` carries the kind, owner, copied entry,
timeline/identity, and optional public marker from the successful claim to
the Discord send. It is never written to the story log or database. Claims
for one completed turn should be collected and saved in one short state
transaction while the caller already owns the conversation lock.

Executor keeps a turn-local `ScenarioSearchState`: the already supplied
`rag_context`, normalized queries attempted this turn, result identities,
and the count of explicit search rounds. This is discarded at turn end and
must not leak scenario passages across users or timelines. Existing
`search_scenario` callers remain compatible with its required `query` string;
Executor may add up to two optional `related_queries`. A batch returns a
bounded set of unique page/chunk passages, with at least one relevant result
per nonempty query when available and no more than the current total output
limit. Search results remain read-only evidence, never a game-state change.

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

## Executor search flow

1. `context_builder.build_context` continues to retrieve proactive scenario
   context. Executor first inspects that evidence; it need not call a search
   tool when the concrete ruling is already supported.
2. When detail is missing, the first explicit search can include one main
   query and up to two related, distinct current-scene queries. The gateway
   executes these local searches under one model tool-call round, removes
   duplicate passages, and bounds the combined result to the existing
   `SCENARIO_RAG_TOP_K`/response-size budget. A search failure or empty
   result is reported as such; it is never treated as evidence.
3. If a later *distinct* fact becomes necessary, one follow-up search is
   available with a required `missing_fact` description. Rephrasings of an
   already attempted query or requests whose returned passages duplicate
   the previous evidence receive an explicit already-covered result. Use
   `scenario_rag._tokenize` for exact normalized-token repeats and identify
   retrieved passages by `(page, hash(text))`; do not introduce another LLM
   call just to decide whether two queries are similar. `missing_fact` is
   optional in the first tool schema and required in OpenAI's follow-up
   schema; the gateway validates it for providers with static schemas.
   After that follow-up, Executor proceeds with established facts or states
   the uncertainty; it does not invent a ruling to satisfy a latency budget.
4. OpenAI's existing `tools_for_request` callback may narrow the offered
   schema by search phase. The gateway enforces the same bound for other
   providers, so their static tool lists cannot bypass it. Existing KP
   Assistant and legacy single-agent Keeper search behavior is unchanged.

This intentionally revises the earlier RAG-reuse spec's “no hard per-turn
search cap” non-goal for Executor only. A two-round limit plus multi-query
first round is the proposed tradeoff. The implementation must first replay
the logged basement-stairs case and at least one genuinely distinct-fact
case with the configured model. If the distinct-fact case cannot be answered
correctly within this contract, revise this spec before implementing a
stricter limit. Prompt wording alone is insufficient: the existing reuse
prompt was present during the three-search incident.

## Integration and observability

- Extend the current `app/discord_bot.py` and `app/commands/router.py`
  handoff rather than replacing locks or creating a second independent
  posting path. Reuse `_buttons_posted`, button identity helpers, and the
  stranded-claim release logic from the duplicate-button fix.
- Keep `app/agents/executor.py` as the owner of turn-local search state and
  `app/agents/tool_gateway.py`/`app/keeper.py` as the existing read-only
  search execution boundary. Reuse `scenario_rag.search` and current result
  formatting; do not add a new network retrieval service.
- Add structured events for `pending_button.claimed`,
  `pending_button.send.completed/failed`, and
  `executor.scenario_search.accepted/reused/limited`. Record elapsed time,
  kind, count, and status; do not add player text, query text, page content,
  owner IDs, or Discord message bodies to structured events. Existing
  `LOG_TEXT_ENABLED` query logging remains separate.
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
3. Search unit tests: proactive context sufficient (zero explicit search),
   batched distinct queries (one model round and bounded deduplicated
   passages), equivalent follow-up (no second retrieval), genuinely missing
   fact (one follow-up allowed), empty/degraded first result, and provider
   parity. Assert no mechanic tool or scenario access-control regression.
4. Controlled replay of the basement-stairs case and a distinct-fact case
   with the configured real model. Record Responses API calls, search
   rounds, final check/ruling, and elapsed time against the current branch.
   A result that merely suppresses needed evidence fails the experiment.
5. Run relevant Discord/router, Executor/RAG, provider, and state tests,
   then the full project suite and existing static checks. In a subsequent
   live log, compare button-send tail and model requests per gameplay turn
   against the baselines above; report sample sizes and queue load.

## Review decisions and tradeoffs

- **Recommended:** two explicit Executor search rounds at most, with up to
  three related queries in the first round. This gives distinct facts a path
  while bounding the repeated same-scene pattern. The real-model replay is
  a gate before finalizing this change because an arbitrary search cap can
  hide a necessary clue.
- **Button ordering:** send outside the conversation lock to avoid holding
  game state serialization during Discord network I/O. The claim is made
  before release; a button may become stale before delivery, as it can
  today, and identity checks remain authoritative at click time.
- **Crash recovery:** the existing claim-then-send crash gap remains. A
  durable outbox/lease would solve it but adds a schema and delivery worker;
  this latency change keeps the current failure rollback and does not add a
  persistent queue.
