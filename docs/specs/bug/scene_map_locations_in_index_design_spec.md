# A scene map's location name is added to the location index

[繁體中文](scene_map_locations_in_index_design_spec_zh.md) | [Docs index](../../README.md)

Category: `bug`. Status: **implemented**. Base: `main_v2` at `37e16bd`.

## Problem

A Markdown scenario loaded with an empty location index (the library keeps only entries with a page number, and an LLM extraction without page markers reports page 0). Importing `map_*.yaml` stored the map, whose top-level `location_name` is the place's name, but left `scenario_location_index` empty; `/coc index` rebuilt the index from the text alone and never read `scene_maps`.

## Change

`scenario_index.merge_scene_map_locations(locations, scene_maps)` returns the index plus one entry `{name, aliases: [], summary: "", page: 0, source: "scene_map"}` per scene map `location_name` that no entry already holds by name or alias (`strip` + `casefold`). Existing entries are untouched and a repeat adds nothing. When an upload overwrites a map under the same key, the entry made for the old map's name is removed unless another map still names it; only entries the merge itself made (marked `source: scene_map`) can be removed, never one from the scenario text. It is called when a map upload succeeds (`map_service.handle_map_upload`, in the same state commit; an invalid map changes nothing) by `scenario_activation.install_context_fields` when it keeps the maps (a repair of the running scenario replaces the index but not the custom maps), and by `/coc index` after the numbered-heading check, so a map never hides an incomplete text extraction ([an incomplete location-index rebuild never replaces a valid index](index_location_underflow_design_spec.md)).

## Not done

Room names are not locations; no fuzzy matching, translation, NPCs from maps or LLM call; nothing else about maps, RAG or the library changes.

## Tests

`tests/test_scene_map_location_index.py`: empty index, existing entry kept, alias match, idempotent merge, successful and invalid upload, `/coc index` merging maps, and a map not covering an underflow.
