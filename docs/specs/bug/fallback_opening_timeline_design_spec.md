# A fallback opening checks its timeline where the turn takes its own

[繁體中文](fallback_opening_timeline_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `c6512c4`. Found in the 2026-10-14 audit of `main_v2` (Phase 2A opening).

## Problem

The scripted opening commits under the extraction token's timeline (`expected_timeline`). The fallback opening hands the Keeper pipeline the token's source hash, context and participants, but not its timeline: the turn starts from `refresh_tool_state` and captures whatever timeline that state has. The pipeline's commit compares the turn's timeline only with itself, and `opening_guard` has no timeline condition. In-process the conversation lock closes the gap; an external writer that started a new game of the same scenario between the last token check and the turn would get the old opening committed to the new timeline.

## Change

`_run_fallback` compares the refreshed state's timeline with the token's before it starts the turn and answers `rejected / timeline_changed` (the reason the player already gets for a changed timeline). A fallback without a token (an older state without a source hash) is unchanged.

## Verification

`tests/test_game_opening_unlocked_extraction.py`: a token for an older timeline does not generate the opening and is rejected as `timeline_changed`; a matching token runs the turn as before. The first part fails without the change.
