# Going somewhere or talking to an NPC is narration; a roll made is evidence; a repeated look-up is answered

[繁體中文](executor_travel_npc_and_evidence_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `c85efbd`.

## Problem

The 2026-10-11 Scritch Scratch rerun4 (27 fallbacks, 6 error replies) and Camp Sunny rerun3 (9 fallbacks, 5 error replies, 1 internal error) had no tool failure at all. The turns the players lost fell into five patterns in the Executor's decisions and the code that classifies them.

1. **「去教堂查」「在酒館問村民」「向保羅確認」 answered as `blocked` or `deferred`** (15 of 27 in Scritch Scratch, 6 of 9 in Camp Sunny; nine NPCs never answered, the Sheriff stood waiting eight turns). A `deferred` with nobody waiting is rightly refused by validation, but the retry hint was generic, so the turn ended `incomplete` and the player read 「這次行動尚未完整處理」. The same content was narrated without trouble on other turns.
2. **A Sanity roll made, SAN lost, then the completion refused** (Scritch Scratch turn 5; also turns 6, 39, 77 with `record_clue`): `resolved_without_check` demanded a literal `scenario_context` reference even when the turn's own tools had rolled or written something; with dice rolled there is no retry.
3. **`rag_status` was `"error"` on every turn** with the scenario search disabled (the default), so every blocked turn was labelled 「查不到劇本依據」 and the recovery searched again for nothing.
4. **A deferral after one look-up was called a pending state** (Camp Sunny turn 45): 「請先完成它」 about a check that did not exist, and no retry, because `classify` treated any tool call as a reason not to call it a turn with no action.
5. **The same read-only look-up twice in one conversation ended the turn** (Camp Sunny turn 76): `CodexError('codex_duplicate_tool_attempt')` became 「系統發生內部錯誤」 for the player, over a search that changes nothing.

## Change

- **The Executor's rules say it, and the retry says it again.** `EXECUTOR_INSTRUCTION`: going to a place the scenario has shown, speaking to an NPC, waiting for an NPC or for the scene to move on are narration (`no_mechanics`), not `blocked` or `deferred`; `deferred` is for a combat turn not yet one's own or another's check in the way; a 「如果／若…」 line is judged against the current state and answered. `final_feedback` adds `nobody_waiting`: when a deferral is refused with no fight, no pending check and no Luck decision, the retry is told nobody is waiting and to answer with `no_mechanics`.
- **A roll or a write this turn is evidence.** `validate_resolution` accepts `resolved_without_check` when a referenced tool rolled (`resolved`) or wrote (an ok call to a tool that is not read-only and did not no-op), not only when the scenario was quoted.
- **No search is `disabled`, not `error`.** `build_context` reports `rag_status` `"disabled"` when no scenario search task ran, so classification says `unsupported_action` and recovery does not search.
- **A deferral is no action when only look-ups ran.** `classify` tests the tool calls against `READ_ONLY_TOOL_NAMES` instead of their presence.
- **A repeated look-up is answered from the transcript.** The Codex provider, on the same read-only `(name, arguments)` again in one conversation, appends the earlier result as a replayed tool entry and continues; the iteration limit still bounds a model that keeps asking. Logged as `codex.tool.lookup_replayed`.

## Not changing

- A validated `blocked` still reaches the player as the fixed 「無法繼續」 text; narrating the Executor's own reason is a separate change.
- The Keeper driving the plot on its own (Camp Sunny never met its threat) is not addressed here.

## Verification

- `tests/test_turn_consistency_handoff.py`: a refused deferral with nobody waiting gets the `nobody_waiting` feedback and the turn completes as `no_mechanics`; a Sanity roll or a `record_clue` referenced by the decision validates `resolved_without_check`, a look-up that found nothing does not.
- `tests/test_turn_fallback.py`: a refused deferral after `search_scenario` is `executor_no_action`; after `add_carried_item` it is `unresolved_pending_state`.
- `tests/test_retrieval_prefetch.py`: with the scenario search off, `rag_status` is `"disabled"`.
- `tests/test_codex_decision_context.py`: the same `search_scenario` twice runs the host tool once and the second is answered from the transcript.
