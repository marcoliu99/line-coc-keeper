# Empty derived scenario artifacts must not fail silently

[繁體中文](empty_scenario_location_index_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `c340984`.

## Revision note

The first version of this spec claimed that empty floor plans refused every move in a twenty-five-turn session. **That causal claim was wrong and is withdrawn here**; the reasoning and the evidence replacing it are below. What survives is a separate, verified finding: a republished source invalidates `indexes`, `scene_maps` and `pregens`, and no command brings the floor plans back.

## Problem and evidence

A five-investigator party played twenty-five turns and never left the room it started in. The group's state carried:

```text
scenario_location_index: []     scene_maps: {}
current_room_id:         {}     current_location: null
```

The library it loads from holds two variants of the same scenario, and the one in play has all three derived artifacts empty:

| variant | `indexes` | `scene_maps` | `pregens` |
| --- | --- | --- | --- |
| `the-haunting-scenario-trimmed-70dbe2a5` | npcs 2, locations 8 | 1 floor plan | 4 |
| `…-ai-950721dc39db75cf` (in play) | `{}` | `{}` | `[]` |

**Extraction is not at fault.** Run against the AI variant's own text, `scenario_index.extract_scenario_index` returns 2 NPCs and 9 locations, including 舊科比特宅邸 with its aliases. The prose is intact — retrieval found the stairs, the board walls, even the smell through the seams — the publishing path simply never calls extraction.

## What actually happened in those twenty-five turns

Re-analysing the log gives numbers quite unlike the ones the first version reported:

| fact | count |
| --- | --- |
| `commit_movement` invoked | **4** (across all 25 turns) |
| failed with `movement_evidence_missing` | 3 |
| failed with `passage_blocked` | 1 |
| any map-resolution failure code (`movement_destination_mismatch`, `known_map_location_requires_path`, …) | **0** |
| turns ending `incomplete` / `arrival_not_committed` | 1 |
| turns ending `model_incomplete` | 8 |

The first version's "fourteen of twenty-five turns were refused" counted turns the model failed to finish as movement refusals. Movement was in fact **attempted four times**, and all four failed before any map was read:

- `movement_evidence_missing` (`app/services/movement.py:251`) — the quotes supplied were not verbatim substrings of a source, or were absent. This guard sits at the top of `_validate` and touches neither `scene_maps` nor `scenario_location_index`.
- `passage_blocked` (`app/services/movement.py:259`) — the model set `conditions` to `blocked` itself. Also before any map lookup.

So the party's immediate reason for being stuck was the Executor failing to produce a well-formed arrival, **not empty floor plans**.

## What empty floor plans actually cost

`app/services/movement.py` resolves destinations through `scene_map.resolve_move(state.scene_maps, ...)` and `scene_map.find_room_by_text(...)`. It **never reads `scenario_location_index`** — that index only ever enters the prompt.

Nor does a missing map refuse movement outright. The mapless branch of `_validate` (`app/services/movement.py:342-351`) accepts a destination that is in no map at all, requiring only that `path` be empty, that the destination not belong to a known map, and that the destination itself be supported by scenario text. `test_sr_m07_mapless_supported_arrival_needs_no_new_map` (`tests/test_turn_routing_and_movement.py:196`) sets `scene_maps` to `{}` and asserts the arrival succeeds.

The accurate statement is therefore:

- No floor plans → **path-based movement between known rooms is lost** (`path` must be empty; a known location requires a path). Evidence-backed arrivals still stand.
- No location index → **the prompt loses its location table**, and the Keeper re-derives the same location's details repeatedly.

Neither makes movement fail categorically.

## This is specified behaviour, not an oversight

`scenario_source_authoring.py` and `scenario_source_review.py` write `('indexes', {}), ('pregens', []), ('scene_maps', {})` and record `derived_artifacts: 'invalidated: …'`. `docs/specs/enhancement/external_english_source_preparation_design_spec_zh.md` §7 requires it: 「舊 embedding、NPC 索引、pregens、推測地圖與中文版本失效,不能複製舊值到新來源」. A test asserts it, seeding the parent with `{'1': {'old': True}}` to prove the republish does not inherit it.

An earlier attempt at this fix inherited the floor plans, on the reasoning that they derive from page images and the republish keeps the PDF byte-identical. **That reasoning is beside the point and the change was reverted**: the invalidation is about provenance, not staleness — a vision model's inferences must not cross into an audited source without being re-derived there.

**The gap is that the spec invalidates without providing a way back.** `scene_maps` reach a group only from a PDF upload's vision pass or from a library context, and no command rebuilds them; `/coc index` rebuilds the index but not the maps.

## Scope

Make the condition visible at the point a scenario is installed or switched. This does not repair any artifact, inherit one, or relax the arrival check — and it does **not** claim to address the twenty-five turns above.

`scenario_index.report_location_index(locations, *, source, scenario_title, scene_maps)` records the counts at each of the four sites that assign these artifacts — PDF upload, scenario correction, library install, chapter switch — at WARNING when empty and INFO otherwise, and returns the notice to callers that have a reply channel: the PDF upload confirmation, the chapter-switch tool result, and the `/coc scenario use` reply. Callers pass the returned string through **verbatim** rather than deciding for themselves; deciding locally is exactly how "only the index was checked, the empty floor plans went unseen" happened.

Each notice describes only the capability it costs, and must not claim movement will be refused.

## Testing

- The empty index and the empty floor plans each produce their own notice, both appear when both are empty, and neither appears when populated. Counts and source are recorded either way, with the level raised only when empty.
- **Neither notice may contain 「會被拒絕」** — pinning the false diagnosis directly against the behaviour `test_sr_m07_mapless_supported_arrival_needs_no_new_map` proves.
- The scenario title is hashed through `observability.safe_identifier`, not passed through.
- The upload confirmation shows the reporter's string verbatim, and stays silent on an empty string.
- Checked **manually**, once, against both real library variants: the original reports eight locations and one floor plan with no notice; the AI-prepared one reports zero of each and both notices. This is not a test — the library is not in the repo.

Mutation-checked: returning no notice for an empty index fails.

## Limits

This states which derived artifacts a scenario is missing and what capability that costs. It does not say why the AI preparation produced none, does not repair them, and **does not explain why those twenty-five turns stalled** — on the evidence above that was evidence quality in the Executor, a separate problem. A group already playing an affected scenario sees nothing new until the scenario is re-imported or a chapter is switched: the notice fires where the artifacts are assigned, not on every turn.
