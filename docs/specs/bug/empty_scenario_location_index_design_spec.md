# An empty scenario location index must not fail silently

[繁體中文](empty_scenario_location_index_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `c340984`.

## Problem and evidence

A five-investigator party played twenty-five turns and never left the room it started in. Every attempt to reach the stairs was refused — `我往樓梯口走過去`, `我沿著樓梯慢慢往上走`, `我走進一樓的走廊`, `我推開最近的那扇門` — while the narration kept describing those stairs. Turn 19 said so outright: "沒有看見其他門窗或明顯出口；唯一可辨認的通路，是你們來時那段年久失修的樓梯." Fourteen of twenty-five turns were refused, one with `arrival_not_committed`.

The narration was otherwise sound: scene details held across all twenty-five turns, no room was invented, carried items persisted, companions were acknowledged. Only movement was impossible.

The cause is scenario data, not code. The group's state carried:

```text
scenario_location_index: []     scene_maps: {}
current_room_id:         {}     current_location: null
```

And the library it loads from has two variants of the same scenario:

| variant | `indexes.json` |
| --- | --- |
| `the-haunting-scenario-trimmed-70dbe2a5` | npcs 2, locations 8 |
| `…-ai-950721dc39db75cf` (the one in play) | **`{}`** |

An arrival is committed against a known destination, so with no locations indexed nothing can be validated and every movement is refused. The scenario's prose was intact throughout — retrieval found the stairs, the board walls, even the smell through the seams — which is why the failure looked like a model problem rather than missing data.

## Scope

Make the condition visible. This does not repair the index, restore it from another variant, or relax the arrival check.

`scenario_index.report_location_index` records the count at each of the four sites that assign the index — PDF upload, scenario correction, library install, chapter switch — at WARNING when it is empty and INFO otherwise, and returns the notice for a caller that has a reply channel. The PDF upload confirmation shows it, sourced from the state after install rather than from the extracted index, because a library variant supplies its own.

`None` is distinct from `0`: a caller that did not look must not read as "none found".

## Testing

- The notice appears for an empty index and not for a populated one, with the count and source recorded either way and the level raised only when empty.
- The scenario title is hashed through `observability.safe_identifier`, not passed through.
- The upload confirmation carries the notice at zero, omits it at eight, and stays silent at `None`.
- Checked against both real library variants: the original reports eight locations and no notice, the AI-prepared one reports zero and the notice.

Mutation-checked: returning no notice for an empty index fails.

## Limits

This says a scenario cannot support movement; it does not say why the AI preparation produced no index, and it does not repair one. Both remain open. A group already playing an affected scenario sees nothing new until the scenario is re-imported or a chapter is switched — the notice fires where the index is assigned, not on every turn.
