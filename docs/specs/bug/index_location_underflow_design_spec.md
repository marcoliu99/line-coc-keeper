# An incomplete location-index rebuild never replaces a valid index

[繁體中文](index_location_underflow_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `6de8b31`.

## Problem

The NPC/location index is extracted by an LLM and the result is not stable: repeated `/coc index` runs on *The Haunting*, whose text has `LOCATION 1:` … `LOCATION 9:`, returned 8, 9, then 8 locations. Every result was written over the previous index, so one short run downgraded a complete index.

## Change

- `scenario_index.detect_numbered_location_sequence` reads the heading numbers of lines that start with `LOCATION n:` (an optional `#` prefix, any case). Prose such as `Proceed to Location 2, 3 or 4` or `see Location 5` is not a heading. The check is active only when the headings are exactly `1..N` (at least two); with a gap, a repeat, a first number other than 1, or no headings it is skipped and behaviour is as before.
- `scenario_index.location_index_underflow` rejects a result with fewer locations than `N`; `N` or more passes. It logs `scenario.index.validation` (`status` pass/reject/skip, expected/extracted/previous counts, heading numbers, `reason`), never scenario text.
- `/coc index` checks `GroupState.scenario_text`, the text the Keeper reads (page repairs applied), before anything is assigned. A rejected rebuild commits nothing: the previous NPC and location index stay, the reply says so, and the command still succeeds. Without a previous index nothing incomplete is written and no location is invented. The commit is the usual snapshot commit, conditional on the loaded revision and timeline.
- PDF and Markdown uploads run the same check on the parsed text. An incomplete extraction is stored as an empty index with a notice that the index was not written; the import and the game start are unaffected. Republishing a library entry (`scenario_library`, as a reparse of the same scenario does) never replaces the entry's existing index with an empty one, so the correction path and later chapter reloads keep it.

## Not done

No retry, majority vote, second extractor, prompt change or deterministic location parsing; no new LLM call. `ROOM n` is not a location heading.

## Tests

`tests/test_location_index_guard.py`: heading detection (forms, prose, gaps, repeats), pass/reject/skip and the logged fields, `/coc index` with and without a previous index and with a page repair applied, the upload notice, and a republished entry keeping its index (the full correction flow is tested in `tests/test_scenario_lifecycle_characterization.py`).
