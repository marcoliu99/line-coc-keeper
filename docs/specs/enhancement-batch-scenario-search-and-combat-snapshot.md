# Spec: Batch scenario searches and reuse the combat snapshot

## Status

The user approved implementation after a small real-provider trial. The first
implementation is scoped to combat snapshot reuse; multi-query scenario search
remains a separate, unimplemented item in this spec.

## Goal

Reduce avoidable model/tool round trips in gameplay turns by addressing two
patterns measured in the current `profile-async2.log`:

1. Several related `search_scenario` calls in one turn, each requiring another
   model response before the next search.
2. A `get_combat_status` call at the beginning of a turn even though the
   current combat status is already present in the dynamic prompt.

The optimization must preserve retrieval coverage, combat correctness, and
the ability to refresh information after state changes. It targets tool-loop
round trips; it does not promise lower total provider requests if a batched
search makes extra embedding requests.

## Current behavior

- `search_scenario` accepts one `query`, runs `scenario_rag.search` once, and
  returns up to `SCENARIO_RAG_TOP_K` results. The tool is shared by the legacy
  Keeper and Supervisor/Executor paths when scenario RAG is enabled.
- The existing `docs/specs/bug-search-scenario-fragmented-queries.md` already
  improved query formulation instructions. That change is on `main_v2`; this
  proposal is a separate follow-up because the current supplied log still
  shows multiple explicit searches in some turns. This is not a proposal to
  reinstate a hard one-search-per-turn limit.
- When combat is active, `_build_dynamic_prompt` includes
  `combat.status_text(...)` with the round, initiative order, and current
  actor. `get_combat_status` is still available as a read-only tool, and its
  current description does not tell the model to reuse the prompt snapshot
  until that snapshot becomes stale.

## Evidence from the supplied current log

The user confirmed that the supplied profile files represent the current
behavior; treat them as the baseline. Parsed observations from
`profile-async2.log`:

- 69 turns called `search_scenario`; 23 of those turns made two to five
  searches. This is 39 additional search calls beyond one call in each of
  those turns. It is an upper bound on opportunities for batching, not a
  guaranteed number of saved model requests: a batch may still need a follow-up
  search, and not every query can be predicted before seeing results.
- 30 of the search turns also had proactive scenario RAG in `context_builder`.
  The batch tool should be assessed alongside that already existing context;
  this spec does not add another proactive RAG pass.
- There were 27 `get_combat_status` calls. Twelve were first-iteration calls
  with no earlier same-turn state-mutating tool. Log result text was available
  for 11 of those: nine returned an active combat status, two said no combat
  was active, and one had no extractable status text. The nine active cases
  are evidence-backed candidates for gating; the two inactive cases must keep
  status lookup available. The unclassified case is not counted as a saving.

These two opportunities can overlap within a turn, so their counts must not be
added to estimate total savings.

## Pre-implementation real-provider trial

Ran real OpenAI Responses API calls with the configured Executor model
`gpt-6-luna` and reasoning effort `none`. All trials used synthetic combat
state and a mock tool executor that performed no game-state writes.

- Direct status question, one run per variant: both baseline and gated tool
  lists answered directly in one request; neither called a tool. This case did
  not exercise the status-call pattern.
- Focused action with only `skill_check` and `get_combat_status` exposed, two
  runs per variant: baseline called status then skill check in both runs
  (three requests each). The gated variant used two requests once; on the
  other run it called `skill_check` twice and used three requests. This narrow
  schema is not representative enough to establish the expected saving and
  showed model variance in tool choice.
- Focused action with the production player tool list, one run per variant:
  both used three requests. Baseline did not call `get_combat_status` and
  instead selected an unrelated `adjust_ammo` call; the gated variant did not
  select that call. No stale-state decision occurred. This is a smoke check,
  not evidence of a guaranteed request reduction.

Interpretation: the real-provider trial did not show increased request count
or stale state with the production tool list, but it was too small and failed
to reproduce a baseline status call with that full list. The production log
provides the stronger evidence that first-iteration status calls occur. Treat
the expected benefit as conditional: up to nine observed calls are candidates,
with actual savings to be measured after rollout. Keep the metric-based
rollback criteria below.

## Scope

### 1. Multi-query `search_scenario`

- Preserve the existing single-query input and behavior for compatibility.
- Add an optional `queries` array for a small set of related, concrete queries
  that the model expects to need for the same current event. Accept one to
  four non-empty query strings; reject malformed or over-limit input with a
  clear tool error rather than silently dropping entries.
- Accept either `query` or `queries`; if both are present, return a validation
  error. Existing callers sending only `query` continue to work unchanged.
- Retrieve each query against the same scenario index within the one tool
  invocation. When embeddings are enabled, batch the query embeddings into
  one embedding request where the existing cache/API helpers permit it;
  continue to use the established BM25 fallback if embeddings fail.
- Merge results deterministically and keep coverage across query intents:
  visit each query's ranked results in rank order, interleaving queries, and
  deduplicate identical page/text chunks. Bound the merged output to at most
  `2 * SCENARIO_RAG_TOP_K` chunks (10 with the current default). Preserve page
  markers and identify which query or queries surfaced each chunk where
  practical. Do not compare raw relevance scores across queries because their
  BM25 normalization is query-relative.
- Update the player and KP Assistant tool descriptions to invite a `queries`
  batch for distinct but related needs in one event/request, while retaining
  the existing event scope and spoiler policy for each role. Keep single
  `query` appropriate when there is only one information need.
- A later search remains allowed when the merged result genuinely lacks a
  fact needed to resolve the current event. There is no per-turn search cap.
- Record query count, unique result count, and whether embedding batching or
  BM25 fallback was used in existing RAG telemetry. Do not log scenario text
  or result content in structured telemetry.

### 2. Reuse the combat snapshot with deterministic tool availability

Prompt wording alone is not a reliable control: the model can still choose a
status call even when the snapshot is present. Prefer controlling whether the
read-only tool is exposed for each model request:

- In the first implementation, apply this to OpenAI only, matching the
  provider used by the supplied baseline. When combat is active and the request
  includes a complete combat snapshot, omit `get_combat_status` from the
  initial request's tool list. The snapshot remains the authoritative status at
  the start of that model turn.
- After a successful tool call that changes combat state (for example,
  adding/removing a combatant, applying damage, ending combat, or advancing the
  turn), inspect its result. If it contains a complete, authoritative status
  snapshot, carry that forward. Otherwise expose `get_combat_status` in the
  tools list of the next model request in the same tool loop, so the model can
  fetch current round/order/current-actor data when it needs them.
- Refresh tool availability between model requests in the tool loop; do not
  make the tool available retroactively to other calls in an already-returned
  batch. State-changing tools continue to execute in their existing order.
- If combat is inactive or the initial snapshot is missing/incomplete, preserve
  current tool availability. Apply the same gating only when a KP Assistant
  request has a complete private snapshot; preserve existing public/private
  visibility rules and never expose a private snapshot or result to a
  player-facing request.
- Keep combat state in the dynamic prompt. Do not depend on model instructions
  alone to suppress the redundant initial status call.
- Leave Anthropic and Gemini behavior unchanged in this first pass; consider
  them after the OpenAI trial and rollout data.

This is a narrower and more deterministic optimization than changing status
tool descriptions: it makes the redundant initial call impossible while
preserving a refresh path after mutations. It does require the shared provider
tool loop to support per-request tool-list updates and a reliable
classification of successful combat-state mutations. The current OpenAI
adapter serializes the tool list once before its loop, so it must be adjusted
to prepare the applicable schemas before each request. Scope the mutation
classifier to operations that change combat round/order/combatants/HP/active
state, and treat a tool error or failed result as no mutation. If these
integration requirements cannot be met without changing state mutation order,
do not fall back to prompt-only guidance; stop and revise the design.

## Explicit non-goals

- No Executor/Narrator merge or change to agent responsibilities.
- No combat initialization macro tool, provider concurrency changes, or
  cross-process rate limiter.
- No hard cap on scenario searches and no weakening of spoiler controls.
- No removal of proactive scenario or memory RAG.
- No change to combat mechanics, turn order, or state mutation sequencing.

## Data and API changes

- No persisted state/schema migration.
- `search_scenario` tool schema gains optional `queries: string[]`; single
  `query: string` remains supported. Runtime validation enforces exactly one
  form and a maximum batch size of four.
- The OpenAI provider loop may supply a per-request tool list. While a complete
  snapshot is present, `get_combat_status` is withheld for the first request
  and added after a successful combat mutation when its result did not include
  a complete current snapshot. This is request-local control state, not
  persisted game state. Other providers are out of scope for this first pass.
- Scenario retrieval may add an internal multi-query helper that accepts a
  shared index and returns a bounded, deterministically merged result list.
  The existing one-query search API remains available to other callers.
- Telemetry adds aggregate counts only; query text continues to follow the
  existing separately gated plain-text logging policy.

## Main flow

```text
Gameplay turn starts
  -> prompt includes combat snapshot when combat is active
  -> initial tool list omits get_combat_status while that snapshot is current
  -> after a successful combat mutation, inspect its result
       -> complete current snapshot: carry it forward
       -> incomplete/missing snapshot: expose get_combat_status next request
  -> if scenario lookup is needed:
       one query for one need, or queries[] for related needs in this event
       -> local retrievals -> interleaved/deduplicated bounded result
       -> model may issue a follow-up only for information still missing
```

## Failure handling and tradeoffs

- Invalid batch input produces a tool error without mutating game state.
- If batched embedding generation fails, follow current per-search BM25
  fallback behavior; retrieval must not become less available than today.
- Multi-query merging can miss a low-ranked chunk because output is bounded.
  Interleaving protects per-query coverage; the model can make a focused
  follow-up query when needed.
- Reusing a prompt snapshot is safe only until a successful combat-state
  mutation. Tool-result completeness must be checked before treating it as the
  new snapshot.
- Withholding the status tool prevents an unnecessary initial call, but means a
  model cannot request it in the same batch as its first state mutation. It
  becomes available on the next provider request if that mutation did not
  return a complete snapshot. This may preserve or add a post-mutation model
  round in some cases; compare round counts and correctness in the provider
  trial.
- Batching reduces model tool-loop cycles, but could alter retrieval quality
  or embedding request timing. Evaluate tool-call reduction and correctness
  separately from wall-clock latency.

## Testing and evaluation plan

Implementation should add tests for:

- Legacy single-query schema/input behavior.
- Valid one-to-four query batches; empty, mixed-form, and over-limit input.
- Retrieval coverage, deterministic interleaving, page/text deduplication,
  output bound, and BM25 fallback on embedding failure.
- Player versus KP Assistant description preserves role-specific spoiler
  behavior.
- OpenAI initial tool-list construction omits the status tool only when a complete
  current snapshot is present; successful mutations refresh tool availability
  on the next request when their results are incomplete.
- Mutation batches retain their existing execution order; private status is
  never exposed to player-facing requests.

Before implementation approval, a small real-provider trial is required.
Use the configured OpenAI model/reasoning setting with the same scenario data,
starting state, prompt and equivalent tools. For scenario retrieval, compare
the current single-query tool with the proposed batch input. For combat status,
compare current tool availability with the proposed per-request OpenAI tool
list, using a read-only or mocked state mutation so no production game state
changes. Compare retrieved fact coverage, tool choices, and total model
requests. Keep the trial narrow (one or two representative cases per
optimization, with repeated runs only where needed to distinguish model
variance); record the exact model, reasoning setting, tool schema, prompt
version, and outcomes. If batching misses needed facts, or gated status access
increases requests or produces stale combat decisions, revise the design
before coding.

After implementation and rollout, compare at least the next 24 hours / 500
OpenAI attempts where available, reporting separately:

- `search_scenario` calls per scenario-lookup turn and extra model rounds
  removed by multi-query calls;
- first-iteration `get_combat_status` calls when the prompt snapshot is
  present and unchanged;
- follow-up searches and relevant retrieval misses;
- correctness signals (wrong/missed scenario facts, stale combat actor/round,
  or state errors) and turn P50/P95.

Success means fewer model tool-loop rounds in the two targeted patterns,
without an observed increase in retrieval misses or stale combat-state errors.
If retrieval coverage worsens, reduce the merge bound or adjust selection
before widening use.

## Review decisions

- The user has requested a small real-provider trial before implementation;
  this is a required gate, not an optional follow-up.
- Proposed bounds for review: maximum four query strings per batch and merged
  result limit of twice `SCENARIO_RAG_TOP_K`.
- The second optimization uses deterministic per-request tool availability,
  not prompt-only wording, and targets OpenAI in the first pass. The current
  OpenAI loop creates provider tool schemas only once per turn; implementation
  must move that preparation into the request loop without changing the
  sequential order of mutations. Other providers stay unchanged pending data.

Only combat snapshot reuse is approved for the current implementation pass;
the multi-query scenario search remains unimplemented until separately
approved.
