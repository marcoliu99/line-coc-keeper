# Authoritative turn handoff

[繁體中文](authoritative_turn_handoff_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `07d55a7`.

## Current seam

`tool_gateway` observes individual tool receipts, `turn_resolution` validates Executor's completion claim, and `supervisor` currently reconstructs the pending owner and Luck priority before calling Narrator. `turn_context` separately supplies the current state to Executor. The post-resolution reconstruction remains in `supervisor`, so a consumer can accidentally present an old tool receipt as the current pending choice.

## Interface

`app/services/turn_handoff.py` prepares the Narrator-facing `MechanicResult.check_status` from the validated resolution, observed result, before snapshots, and the latest authoritative `GroupState`. It resolves a referenced character to the owning player, selects new or existing pending records per investigator, gives Luck priority over an unrolled check for the selected owner, and attaches the complete `current_turn_state`. The module owns the unchanged-pending reply shortcut. `supervisor` calls this interface once after Executor and passes its result to Narrator; it no longer reconstructs those facts.

`turn_resolution` remains responsible for proving completion and rejecting invalid identities. The handoff never accepts a model's completion claim without that validation, never mutates game state, never replays tools, and never turns private opposed details into public text. Existing tool observations, partial-completion semantics, current-state projection, and delivery filtering stay intact. The pre-Executor context remains a separate input view.

## Verification

Test the interface directly for a newly created other-player check, an existing actor check, Luck priority, a resolved partial inventory transfer with an old pending check, and an invalid/incomplete resolution. Keep focused supervisor integration coverage, then run full pytest, Ruff 0.16.8, mypy, and compileall.
