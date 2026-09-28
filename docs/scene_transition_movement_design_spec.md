# Scene Transition and Movement Design

## Problem and goal

`commit_movement` currently combines movement inside a mapped building with travel between scenario locations. The model is asked to provide a map `page` even though it may only have retrieval's printed page numbers, not the internal scene-map key. It can also fail when an OCR-generated map omits a valid connection.

The safety goal is to stop unrequested teleportation: mentioning a destination, asking about it, or retrieving text about it must not move an investigator. Once a player explicitly says they are going somewhere, however, an imperfect map must not make ordinary travel impossible. Maps help describe and locate places; missing OCR edges are not proof that travel is impossible.

## Scope

- Keep explicit `LOCAL_PATH` and `SCENE_TRANSITION` intents so the resolver knows whether the player is moving within a mapped place or traveling to a scenario location.
- Require an explicit movement clause from the player's current action. A question, hypothetical, negation, or mere RAG match never moves the investigator.
- Let Python resolve internal map keys, map names, and reliable entry points. The model must not choose an internal key from a printed page number.
- Use a scene map as a positioning aid and as positive evidence when it clearly shows routes or obstacles. Do not reject a plausible route solely because an OCR-derived graph lacks an edge.
- When the map shows an indirect route between the origin and destination, resolve its intermediate rooms and check every known edge. A model cannot skip a mapped lock, required check, or reaction point by omitting intermediate room IDs.
- Allow explicitly requested travel when RAG finds the requested scenario location, even when no floor plan or complete route exists. A relevant current-turn RAG hit is sufficient destination support; do not demand a map edge or additional route proof. Narrate the travel at an appropriate level without inventing rooms, obstacles, encounters, or events.
- Preserve explicit scenario/map blockers, pending-check and Luck gates, active reaction/combat rules, and destination-arrival ordering.
- Repair current-turn evidence handling so valid scenario search results can be cited with stable Python-issued source IDs. A source ID proves current-turn provenance, not destination relevance: retrieved text must mention the destination (including a contiguous Latin name in a mixed-language request), or the destination must be present in a canonical scene map. The model's displayed RAG quote is an audit hint because translated/OCR text can vary. Non-RAG quotes must match after whitespace and typographic quote normalization. Do not use fuzzy word matching for non-RAG sources.

## Non-goals

- Do not move an investigator merely because the destination was mentioned or retrieved; the player must explicitly request movement.
- Do not allow travel to unknown locations or treat model-authored narration as canon.
- Do not infer locks, walls, encounters, or other blockers from missing/OCR-damaged map data.
- Do not bypass an explicitly described locked door, required check, hazard, reaction point, or other established obstacle.
- Do not add a fixed LLM review call or an unnecessary LLM round trip.
- Do not require generated maps to have cross-scene graph edges.

## Interface and state

Keep the existing `commit_movement` tool and agent loop. The model provides the movement kind, the destination as the player named it, the exact player movement span, current-turn evidence, and any pending-check binding. It does not provide an authoritative map key or entry room. A local path can be supplied as a proposal when useful, but the server validates it against available map evidence without treating incomplete OCR topology as conclusive proof of disconnection.

Python resolves destinations against scenario headings, known location records, established facts, and relevant current-turn RAG results. A source ID alone is not a relevance check: retrieved text must match the requested name, or the destination must match an existing map label. A supporting RAG result is sufficient even if the location is not mapped. Resolution returns a unique destination, ambiguous matches, or no match. A unique mapped destination includes the internal map key and, when reliable, its entry room. If entry-room extraction is missing or uncertain, Python records the mapped scene and a narrative position without fabricating a room ID. Multiple map matches without metadata proving they identify the same usable destination are ambiguous. A destination with no supporting result, or an ambiguous destination, does not mutate position and asks the player to clarify.

## Movement behavior

### Local movement

For movement such as entering a named room or going upstairs, use a map path when the map gives a usable route. If a supplied path skips rooms but the graph shows an indirect route, resolve that route and inspect each edge. Honor every explicit lock, blocked edge, check, choice, and reaction point. If no known graph route connects the endpoints, OCR connectivity remains unknown and must not by itself reject the move. Resolve the player's explicit action using scenario evidence and current state; narrate only the supported transition. Do not add unmentioned rooms or events to bridge the route.

### Scene transition

For travel such as leaving the house for a named shop, library, or other scenario location, no room-graph edge between the two scenes is required. If the player explicitly requests the trip and current-turn RAG returns the requested location, that hit is sufficient support to arrive; do not require a second map or route check. The response should make the travel leg clear instead of presenting arrival as an unexplained instantaneous jump. Travel narration may summarize ordinary passage of time but must not add unsupported stops, shops, NPCs, clues, or encounters.

When the destination is a mapped scene, Python selects its map key and reliable entry point. If the player also requests an interior room, the route from entry is checked where map data is reliable; absent OCR edges alone do not block it, while explicit obstacles still do.

## Flow

```text
Player's current action
        |
        v
Explicit movement intent? -- no --> no position change
        |
       yes
        v
Resolve destination from the explicit request and current-turn RAG evidence
        |
        +-- unknown/ambiguous --> clarify; no position change
        |
        v
Check pending check/Luck and established blockers
        |
        +-- blocked/unresolved --> preserve state; resolve blocker first
        |
        v
Use map to locate route/entry when reliable; missing OCR edge is unknown
        |
        v
Commit requested travel; narrate the travel leg using established facts
        |
        v
After arrival, allow destination-dependent checks and effects
```

## Safety and evidence rules

1. A movement intent must be grounded in the player's current action, not in RAG output, a question, hypothetical, negation, or prior AI narration.
2. An explicit movement request plus a relevant current-turn RAG hit for the requested scenario location is sufficient to support a scene transition. A RAG hit without an explicit movement request never changes position.
3. Evidence references must point to sources registered by Python for the current turn. For RAG sources, verify that the retrieved passage supports the destination or that an existing scene-map label matches it; provenance alone is insufficient, even if the displayed quote differs through translation or OCR. For non-RAG sources, quotes must match after whitespace and typographic quote normalization. Retrieval retries may add sources to the same turn, but sources cannot be reused across turns.
4. Pending checks/Luck prevent movement when they are prerequisites for the requested action. Any prerequisite result remains bound to the original movement span and final result.
5. Explicit blockers remain authoritative. Missing, uncertain, or OCR-omitted map topology is not an explicit blocker.
6. A destination mention alone never commits position. A successful movement commit must correspond to an explicit player request and narrate the travel/entry action at a fitting level of detail.
7. Unknown or ambiguous destinations, invalid evidence, or unresolved blockers leave position unchanged and produce typed diagnostics for the executor. Repeated map matches are ambiguous unless scenario data explicitly identifies them as the same destination.
8. Arrival must be committed before destination-dependent checks, item interactions, or reveals.

## Compatibility and migration

Retain `commit_movement` and its existing tool loop to avoid adding model calls. Update schema and prompts to remove model responsibility for map keys. Update resolver, state context, source registration, result handling, and diagnostics together. Keep existing saves readable; any event-format change must be additive and backward compatible.

## Tests

- Mentioning or asking about a destination without movement intent does not change position.
- Explicit travel from Corbitt House to Boston Globe or Central Library succeeds when current-turn RAG finds the requested location, without a cross-map graph edge or additional route proof.
- Retrieval printed page numbers (for example 2, 6, or 17) never select internal scene-map keys.
- OCR map missing an otherwise plausible edge does not by itself block explicit movement; an explicit locked/blocked edge on a direct or resolved intermediate route still blocks, including when the model omits that intermediate room.
- OCR-missing/uncertain entry room does not fabricate a room ID or prevent travel to the established scene.
- A mapped room's explicit lock, required check, reaction point, and active combat blocker remain enforced.
- Unknown/ambiguous destinations, stale source IDs, fabricated quotes, unrelated current-turn RAG passages, and unsupported destinations do not mutate position.
- A destination matching multiple maps with no disambiguating metadata is rejected without changing position.
- Arrival occurs before destination-dependent checks and item interactions.
- No movement narration invents intermediate locations, NPCs, items, clues, or encounters.

Run focused movement tests, relevant executor/supervisor tests, and the full available suite.

## Evaluation plan

Use the same five-investigator `The Haunting` harness and Codex `.env` settings in an isolated test directory. After implementation, run 50 turns and record fallback replies, uncaught errors, movement rejection codes, requested/executed tools, pending state, Codex request count, and end-to-end latency. Do not send Discord messages or modify production game state. Compare failure causes with the pre-change 42-turn sample; it is a partial baseline, not a full 100-turn benchmark.

## Open implementation details

- Include relevant current-turn RAG results in destination matching; they are enough to support an explicitly requested trip. Fail closed on ambiguity, not on absent map connectivity.
- Preserve current combat rules until tests specify exactly which encounter states allow travel.
- Use existing state representation where possible for a mapped scene whose entry room is not reliably known; do not create a synthetic room ID.
