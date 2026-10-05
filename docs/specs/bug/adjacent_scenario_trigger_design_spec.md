# Adjacent scenario trigger retrieval

[繁體中文](adjacent_scenario_trigger_design_spec_zh.md) | [Docs index](../../README.md)

## Status and goal

Category: `bug`. Status: **implemented**. Source: finding CS-002 of the 5-player / 500-turn Camp Sunny validation ("Camp Sunny 5-Player 500-Turn Validation — Fix Specification", Workstream A). Based on `main_v2` at `aa79f22`.

A player rang a reception bell. Retrieval returned the reception scene but not the neighbouring text that says what the bell does, so the Keeper invented a Spot Hidden check instead of running the scripted consequence. An action and its consequence can sit in two ~400-character chunks that share no word with the player's message.

## Contract

1. A text-index hit now brings along the contiguous neighbour it visibly continues into (`app/scenario_adjacency.py`, applied in `scenario_rag._result_rows`). The hit must share a query term, and one of these must hold:
   * the hit ends in the middle of a sentence (`hit_ends_mid_sentence`);
   * the next chunk opens, after the overlap the chunker repeats, with a condition or trigger such as 若／如果／當／一旦／if／when／once (`next_opens_with_consequence`);
   * the hit continues the previous chunk (`hit_continues_previous`), or itself opens with a condition, so the chunk that names the trigger precedes it (`hit_opens_with_consequence`).
2. The expansion is bounded: `SCENARIO_RAG_ADJACENT_CHUNKS` neighbours per side (default 1, 0 turns it off), at most four per search, never across more than one page, never a chunk outside the caller's visibility, and never because a neighbour merely exists. A hit that itself opens with a condition does not pull the next condition (a different rule). The chunk after the neighbour is not exposed.
3. A search reports `adjacent_chunk_count`, and each attached row carries `adjacent_chunks` with the side, page and reason.
4. The Executor policy now says: when the scenario evidence states the direct consequence of a player's action, use it and do not replace it with an improvised Spot Hidden, Listen or Luck check unless the scenario asks for one; when the evidence names the trigger object but not the consequence, make one focused `search_scenario` lookup of current scene + action + object, never a broad "what happens next" query.

## Contract kept

The index, its embeddings and the v4 record-store path are unchanged, so an existing index works without a rebuild. Ranking is unchanged; only the text of a returned row grows. No scenario text, name or trigger is hard-coded in the runtime.

## Enforcement

`tests/test_scenario_adjacency.py`: a bell/clerk fixture (reception → scripted NPC), a second fixture in another scenario and language, no exposure of the chunk after the neighbour, no expansion of a self-contained hit, no cross-page or hidden neighbour, the off switch, de-duplication when the neighbour is itself a hit, the policy text, and a gate that fails if a runtime module names the Camp Sunny scenario or its triggers.

## Not covered

The v4 record-store path follows the typed dependency graph instead of chunk order, so this change does not alter it. Whether the Keeper model then runs the consequence instead of inventing a check is model behaviour; the offline tests prove the evidence and the policy reach the Executor, not the model's decision. That needs the directed real-runtime run (not performed here).
