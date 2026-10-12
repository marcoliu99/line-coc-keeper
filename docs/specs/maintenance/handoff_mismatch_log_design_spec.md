# A handoff naming a missing check logs what the state holds

[繁體中文](handoff_mismatch_log_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `3565bed`. Found in the 2026-10-12 Lightless random rerun1 (turns 74–81 and 97–100).

## Problem

In one fight, twelve turns ended with 「這次行動尚未完整處理。目前的狀態和這次行動不一致」: the Executor's `declare_combat_action` settled Rafael's attack and the engine's next enemy turn returned a defence choice (`interaction.check_id`), but the Executor's `await_check` handoff naming that check was rejected as `pending_identity_mismatch` (「檢定／Luck 決定不存在或已過期」), and every retry replayed the same stored receipt. The runtime log shows the rejection but not why: whether the check was never registered, was already gone, or was named for the wrong owner.

## Change

When an `await_check` or `await_luck` handoff names an id that is not pending, `turn_resolution.validate_resolution` writes one text-log line with identifiers only (owners go through `observability.safe_identifier`, like every other log field): the `waiting_for` it was given, the owner it resolved to, the id it named, the id the state expected for that owner, the state's current timeline, and every pending check (owner → check id, timeline) and Luck decision (owner → decision id, timeline). Behaviour is unchanged.

## Verification

`tests/test_turn_consistency_handoff.py`: a handoff naming a check that is not pending is still `pending_identity_mismatch`, and the log line carries the named id, the expected id and the pending entries.
