# Command completion and player controls

[繁體中文](command_completion_controls_design_spec_zh.md)

Status: **in progress**. Base: `main_v2` at `07d55a7`.

## Current audit

The three router changes already own button/upload dispatch, authorization, and in-lock pending-button claims. Those parts of the original candidate are complete. The remaining seam is control publication: `discord_bot` still contains a second check/Luck claim-and-send path, release logic, and two caller-specific claim/send/fallback sequences. Its claimed-intent sender rechecks Luck after an await but does not recheck a replaced check before sending.

## Interface

Deepen `app/services/pending_buttons.py` into the control-completion owner. One completion object captures the before snapshots, claims under the router's lock, and publishes after unlock through a transport callback. It verifies each claimed check or Luck decision against the latest persisted identity before sending; stale replacements are skipped. A failed or cancelled send releases only its own still-matching claim. A failed in-lock claim takes the same recovery path through the service. Discord retains rendering, transport, interaction identity and permission checks.

The router keeps its existing lock and authorization policy. No extra provider call or Discord API call is added for ordinary turns. The service's persisted state reads use worker threads, and sends remain outside the conversation lock.

## Verification

Exercise the same completion interface for text, button, Help and recovery paths. Cover mutation followed by send failure, delayed send after replacement, cancellation, stale identity, and overlapping claims. Preserve existing latency and logging tests; run full pytest, Ruff 0.16.8, mypy and compileall.
