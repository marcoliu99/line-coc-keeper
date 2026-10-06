# An enemy the scenario names gets its attack provenance from the scenario

[繁體中文](enemy_attack_provenance_from_scenario_design_spec_zh.md) | [Docs index](../../README.md)

Category: `bug`. Status: **implemented**. Base: `main_v2` at `910b133`.

## Problem

A real four-player run of *The Haunting* on `main_v2` `5c57ab3` got through initiative (round 5, 25 `advance_combat_turn` calls, see [a combat turn can always be ended](combat_turn_always_endable_design_spec.md)) but no enemy ever attacked: each of the rat swarm's attacks paused with "NPC attack requires verified scenario provenance" (`combat_flow.run_enemy_plan`) and was cancelled. An enemy's attack needs its card's `source` to carry `url`, `revision` and `sha256`. Nothing supplied them: the registration tools take `source` from the model, which has no real revision or hash to quote, and the Keeper prompt never says what to put there.

## Change

When `add_npc_to_combat` or `initialize_combat` registers an enemy and the scenario's NPC index names it, the missing provenance comes from the loaded scenario: `url` `scenario:<library id>`, `revision` the active chapter, `sha256` the active source hash, `extreme_rule` `maximum`. Anything the model gave itself wins. The name is matched like the indexed-HP correction (exact, then fuzzy) but with a lower threshold (0.5 instead of 0.6) because the Keeper's name for a creature need not be the index's: 老鼠 against 鼠群 scores exactly 0.5. The HP correction keeps its 0.6. Accepted trade-off (decided by the project owner): a model-invented enemy that merely shares one character with an indexed NPC (鼠王 against 鼠群) also scores 0.5 and gets the provenance; tighten to an exact or indexed-alias match if real runs show misfires. The provenance is attached only when every attack the model registered states its own hit chance (`skill_value`) and damage; an attack left to the engine's 25% / 1D3 default is not the scenario's and still pauses. An enemy the index does not name gets nothing and still pauses for a ruling, so a model-invented stat block is not vouched for.

## Not done

No provenance for enemies the index lacks; no change to attack modes, ranges or ammunition rules; `extreme_rule` stays `maximum` (piercing attacks that should impale still need the model's own `source`). Whether the Haunting's own NPC index names the rats depends on its extraction and is not asserted here.

## Tests

`tests/test_enemy_source_from_index.py`: an indexed enemy gets the provenance and its attack reaches the defender's choice, an unindexed one still pauses, the model's own source wins, `initialize_combat` does the same, and a looser name matches while an unrelated one does not, and an attack left to the default gets no provenance.
