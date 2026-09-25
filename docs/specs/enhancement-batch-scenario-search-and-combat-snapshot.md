# Spec: Batch scenario searches and reuse the combat snapshot

## Status

Design spec for review. No runtime changes are included in this revision.

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
  with no earlier same-turn state-mutating tool, so the prompt's combat
  snapshot was still current. These 12 are candidates for removal, not a
  claim that all status calls are unnecessary.

These two opportunities can overlap within a turn, so their counts must not be
added to estimate total savings.

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

### 2. Reuse the combat snapshot

- When combat is active, explicitly label the dynamic prompt's combat block as
  the authoritative snapshot for the start of this model turn.
- Update combat instructions and the `get_combat_status` description: do not
  call the status tool if the prompt contains a combat snapshot and no prior
  tool in this turn has changed combat state. Use that snapshot for the initial
  decision.
- After a tool changes combat state (for example, adding/removing a combatant,
  applying damage, ending combat, or advancing the turn), the prompt snapshot
  may be stale. Use a complete, authoritative status returned by that tool if
  available; otherwise call `get_combat_status` before relying on changed
  round/order/current-actor data. Never use the initial snapshot to override a
  later successful mutation result.
- Keep `get_combat_status` available for non-combat turns where state is not
  included in the prompt, for missing/incomplete snapshots, and for state
  refresh after relevant mutations. Preserve its existing public/private
  visibility rules.
- Do not remove or lazily omit combat state from the prompt in this change.

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
- Scenario retrieval may add an internal multi-query helper that accepts a
  shared index and returns a bounded, deterministically merged result list.
  The existing one-query search API remains available to other callers.
- Telemetry adds aggregate counts only; query text continues to follow the
  existing separately gated plain-text logging policy.

## Main flow

```text
Gameplay turn starts
  -> prompt includes combat snapshot when combat is active
  -> model uses snapshot unless absent or invalidated by a successful mutation
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
- Prompt instructions reuse an initial combat snapshot, but permit refresh
  after a state-changing tool; no change to public/private status visibility.

Before rollout, replay representative turns from the current log using the
same scenario data and compare old single-query behavior against batches for
retrieved fact coverage and model tool rounds. After rollout, compare at least
the next 24 hours / 500 OpenAI attempts where available, reporting separately:

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

1. Is a maximum of four query strings per batch and a merged result limit of
   twice `SCENARIO_RAG_TOP_K` acceptable?
2. Should the follow-up comparison use the same current log scenarios through
   a replay harness, or should we also run a small real-provider trial before
   implementation approval?

Implementation remains out of scope until this spec is reviewed.
