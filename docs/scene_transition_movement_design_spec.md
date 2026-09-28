# Scene Transition and Local Movement Design

## Problem and goal

`commit_movement` currently represents room-to-room movement and travel between scenario locations with one payload. The model must provide a `page` map key even though the conversation state does not provide map keys or entry rooms. Retrieval page numbers can be mistaken for map keys. Mapless movement also requires evidence references that must match the exact source identifiers and quotes registered for that turn; rejected evidence can stop a valid trip before movement adjudication.

Separate local movement from scene transitions while retaining strict room-graph, passage, check, reaction-point, and arrival safeguards. Python, rather than the model, resolves map keys and entry rooms.

## Scope

- Add explicit movement kinds: `LOCAL_PATH` for movement within or between connected mapped rooms, and `SCENE_TRANSITION` for travel to a scenario location or another mapped scene.
- Resolve a requested destination against the current scenario's known locations and scene maps in Python. The model supplies the player-intended destination and source evidence, never a map key or entry room ID.
- Automatically select the unique matching map and its configured `entry_room_id` for a mapped scene transition.
- Support known narrative locations that have no scene map as narrative-location transitions.
- Keep graph validation for local room movement and for any explicitly requested onward movement after scene entry.
- Preserve evidence validation, pending check/Luck gating, combat/reaction blockers, and causal ordering before arrival-dependent checks or item access.
- Improve evidence-source handling so the executor can cite only sources actually supplied during the current turn, with stable source IDs and exact quote matching. Do not accept invented source labels or quotes.

## Non-goals

- Do not permit arbitrary teleportation to unknown locations.
- Do not weaken local graph edges, locks, required checks, encounters, or reaction points.
- Do not infer that a destination is reachable merely because RAG mentions it.
- Do not let player assumptions or model-authored narrative establish scenario locations.
- Do not add a separate LLM review call.
- Do not change map extraction or require generated maps to contain cross-scene graph edges.

## Interface and state

Change the `commit_movement` schema so the model supplies `movement_kind`, `destination`, `source_span`, `evidence`, `conditions`, and optional `prerequisite_check_id`. Remove model-required `page` and `path` from scene-transition requests. Local movement may supply a sequence of room names/IDs as a proposed path, but Python validates it against the current map and proposal. `page` is never accepted as an authoritative map selector from the model.

The server derives the target map key and entry room from `GroupState.scene_maps`. Destination resolution must return one of:

- one unique mapped scene and its entry room;
- one unique scenario location without a map;
- no match; or
- multiple ambiguous matches.

No match or ambiguity returns a typed, recoverable result and asks for a clearer destination. It must not mutate position. Successful transition records the resolved destination, source evidence, previous and new location, and the entry room when applicable in the existing movement event/history conventions.

## Flow

```text
Player movement clause
        |
        v
Executor identifies LOCAL_PATH or SCENE_TRANSITION
        |
        +-- LOCAL_PATH --> validate origin, graph edges, locks/checks/reactions
        |                  --> commit mapped room position
        |
        +-- SCENE_TRANSITION --> validate exact player intent and current-turn evidence
                               --> check pending check/Luck, combat/reaction blockers
                               --> Python resolves unique scenario destination
                                    | mapped scene: set map key + entry room
                                    | narrative-only: set narrative location
                                    | unknown/ambiguous: no state mutation; clarify
                               --> commit transition and return arrival result
        |
        v
Only after committed arrival, perform destination-dependent checks/effects
```

If a player requests entry and an onward room in one clause, Python commits the external-to-map entry at the map's configured entry room first. The onward local path is then validated from that entry room through graph edges; it cannot skip blocked edges, checks, choices, or reaction points.

## Validation and safety rules

1. A `SCENE_TRANSITION` requires an explicit movement clause in the player's current action, not merely a location question, hypothetical, negation, or historical statement.
2. Destination evidence must exactly match a registered source from the current turn. Location existence is validated against canonical scenario data or established facts; a RAG hit alone does not create canon.
3. Pending checks and Luck decisions prevent transition until resolved. A prerequisite check must remain bound to the original movement clause and final result.
4. Active combat, unresolved reaction points, and scenario-authored blockers continue to prevent transition where applicable.
5. A transition to a mapped location starts at its Python-selected `entry_room_id`. Any subsequent local path is separately checked.
6. `LOCAL_PATH` never resolves by fuzzy scene-name matching and never jumps across disconnected room graphs.
7. Rejected transitions leave location and timeline state unchanged and return a typed reason to the executor.
8. Evidence source IDs must be generated by the Python retrieval/tool layer and made available to the movement session. The validator must distinguish unavailable source IDs, non-exact quotes, and quotes that do not support the requested destination in diagnostics without exposing internal details to players.

## Compatibility and migration

Keep the existing `commit_movement` tool name and agent loop to avoid adding an LLM round trip. Update the schema, prompt, state context, movement resolver, tool result, and diagnostics together. Existing map-only saves remain valid; no persistent schema migration is expected unless the existing movement event format cannot represent a scene transition. If migration is needed, make it additive and backward compatible.

## Tests

Add service-level and end-to-end tests covering:

- mapped scene A to mapped scene B with no cross-map edge succeeds and chooses B's configured entry room;
- narrative-only scenario location transition succeeds with exact current-turn evidence;
- map key remains `17` when retrieval text mentions printed pages `2`, `6`, or `17`; the model cannot select the map using those page values;
- unknown and ambiguous destinations do not mutate position;
- entering a mapped scene and proceeding to a room uses the entry room then validates every graph edge;
- locked room, missing required check, unresolved reaction point, pending check, and pending Luck still block movement;
- a known mapped room cannot be reached through a scene-transition shortcut that bypasses its graph path;
- wrong source ID, stale source, fabricated quote, and destination-irrelevant quote are rejected with typed diagnostics;
- after successful arrival, the requested destination-dependent skill check or item interaction may run; before arrival it remains blocked;
- regression cases for Boston Globe, Central Library, and Corbitt House from The Haunting.

Run the focused movement suite, relevant executor/supervisor tests, and the full test suite available in the checkout.

## Evaluation plan

Use the same five-investigator, The Haunting scenario harness and Codex `.env` configuration in an isolated test directory. Run 50 turns after implementation. Record fallback replies, uncaught errors, movement rejection codes, requested and executed tool calls, pending-check state, Codex request count, and end-to-end latency. Do not send Discord messages or modify production game state. Compare failure causes against the pre-change 42-turn sample; note that the pre-change sample was stopped early and is not a full 100-turn benchmark.

## Open decisions

- Define how canonical scenario locations are enumerated for resolver matching when the source is prose rather than an authored location registry. Initial implementation should prefer explicit scenario location headings plus established canonical facts and fail closed on ambiguity.
- Confirm whether active combat always blocks a scene transition or only when an encounter/reaction is unresolved; implementation must preserve current combat rules until a precise policy is covered by tests.
- Define exact source-ID lifecycle across retrieval retries so evidence from `search_scenario` remains valid within the same turn but cannot be reused in later turns.
