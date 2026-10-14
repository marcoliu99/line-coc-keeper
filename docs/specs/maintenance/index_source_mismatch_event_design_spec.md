# A library source that is not the game's is recorded when the variant index is chosen

[繁體中文](index_source_mismatch_event_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `c6512c4`. Found in the 2026-10-14 audit of `main_v2` (RAG and scenario hashes).

## Problem

`scenario_templates.index_for_state` builds the Chinese-variant index from the library's current source (manifest `content_hash`), while `state.scenario_text` and the original-text index belong to the source the game was started with (`state.active_scenario_source_hash`). After the library entry is re-parsed, the two can answer from different versions, or the variant silently gives way to the original (`variant_fallback="invalid_or_stale"`). Nothing recorded that the game's source and the library's had parted.

## Change

`_note_source_mismatch` logs a `scenario.index.source_mismatch` WARNING (scenario id, the two hashes' first 12 characters, a hashed group id) when the game's hash and the library's differ. It runs on a shared-cache hit (the cache key holds no group or source hash), where the variant is read (a cache miss) and where the variant gives way as invalid or stale. It only records: no retrieval, fallback or reply changes, so play is exactly as before. With no game hash (an older state), an unreadable library entry or equal hashes, nothing is logged.

## Verification

`tests/test_scenario_template_v3.py`: a game started from another source still gets its variant index and one event with the two hashes; matching hashes log nothing; an unreadable library entry is not reported as a mismatch. The first fails without the change.
