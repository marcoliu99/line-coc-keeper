# Player check settlement and safe follow-up

[繁體中文](player_check_settlement_design_spec_zh.md)

Status: **backlog — awaiting spec review**. Base: `main_v2` at `7cef87c` (2026-09-29).

## Problem and goal

`app/check_lifecycle.py` owns admission and identity when a Keeper tool *creates* a pending check. The other half remains inside `app/legacy_commands.py`: `/coc check` and Luck choices consume pending state, roll once, apply SAN/Luck and opposed outcomes, save state, call Keeper for follow-up, and publish a completed `resolved_check_event`. That long sequence crosses state locks, narrative serialization, tool execution, and Discord delivery. Today the deterministic roll is saved before Keeper follow-up, but the resolved event is published only after Keeper returns. A failed follow-up can leave a committed roll with no durable continuation record.

The goal is a deep settlement module that owns player-owned check and Luck transactions, records a **pending follow-up result** when a roll is final, and supports safe continuation without another roll or replay of completed tools.

## Scope and interface

- Route text commands and buttons through the same settlement interface. It loads fresh state under the existing lock, validates owner/check/decision/timeline identity, consumes or restores pending entries, performs the deterministic roll or Luck choice, applies rule effects once, and commits the result with a durable pending follow-up receipt.
- A pending Luck choice is *not* a settled check. Only the final Luck choice (or a roll needing no choice) creates the pending follow-up result. Existing admission rules, autoroll behavior, legacy identity fallback, major-wound CON effects, opposed checks, and one-time ranged attacker rolls remain unchanged.
- The receipt identifies the group timeline, investigator owner and character, check/decision identities, original action, settled dice and outcome, audience, and follow-up phase. At most one unresolved receipt exists per group. New gameplay actions for the group pause while it exists; status, correction, administration, and the dedicated resume entry remain available under their existing permissions.
- An initial follow-up failure exposes `/coc resume` and a matching button. The original player or registered KP Assistant may trigger it. Neither gains access to a private result outside its persisted audience. The resume path reloads the latest authoritative state and receipt; it never rolls or spends Luck again.
- Keeper follow-up and Discord sending stay outside the deterministic settlement transaction and its state lock. The outer orchestration uses the receipt's facts, rather than reconstructing them from chat history. System-generated check follow-up text must not pass through the player movement parser.
- If follow-up executed other tools before failing, continue only from provably recorded outcomes and current state. Never replay a completed tool. If safe continuation cannot be established, mark the receipt for manual resolution and offer the existing `/coc correct` path; do not silently claim completion. The existing mutation admission and turn resolution safeguards remain authoritative.
- Once Keeper follow-up succeeds, publish the existing `resolved_check_event` exactly once, with effects attributed from committed state and the correct private audience. If Keeper output is complete but Discord send fails, retain that output or report its status without rerunning Keeper. General durable Discord outbox/restart delivery remains a separate candidate.
- Scenario restart or chapter rollback changes timeline, invalidates the old receipt with an audit record, and releases the group pause. It never replays old dice or narration in the new timeline.

## Data and compatibility

Add a bounded `GroupState` field for the pending follow-up receipt, with an explicit phase and identity. Persist the final dice result and initial receipt in the **same** existing state transaction; later tool outcomes and completed output are recorded with stable identities before any retry may rely on them. A crash window with an unprovable tool outcome must fail closed. Keep `resolved_check_events` reserved for completed follow-up, so correction adjudication and existing readers do not mistake an intermediate receipt for a published event. Old saves without the new field load as empty. Clear or invalidate the field wherever timeline and pending checks are reset.

## Flow

```text
Player button or /coc check / Luck choice
  -> state lock + fresh identity check
  -> deterministic roll or Luck choice, once
  -> atomic state commit + pending follow-up result
  -> release state lock
  -> Keeper follow-up from recorded facts
       | completed -> publish resolved_check_event once -> deliver
       | uncertain tool result -> pause for correction; no replay
       | timeout before safe completion -> /coc resume or button
  -> other gameplay waits while the receipt is unresolved
```

## Non-goals

Do not create checks here; `check_lifecycle` remains the admission owner. Do not merge Discord transport into the settlement module, add a fixed LLM review call, change player-facing check rules, reroll on retry, grant KP Assistant an investigator, or implement the general outbox from the turn-safety spec.

## Verification

1. Exercise plain skill, SAN, choice, Luck skip/spend, autoroll, major-wound CON, opposed melee and ranged defense through both text and button entry. A replay with the same identity cannot reroll, re-spend Luck, or repeat the attacker shot.
2. Simulate failure immediately after the settlement commit, during Keeper follow-up, after a successful follow-up tool, after Keeper output, and during Discord delivery. Assert receipt phase, exact state effects, and safe recovery or explicit hold at every point.
3. Verify private results route to the persisted recipient even when a KP Assistant resumes. Group gameplay is paused; status, correction, authorized administration, and resume remain accessible.
4. Test rollback/restart invalidation, old-save loading, duplicate resume clicks, concurrent players, event publication once, and no movement-parser invocation for generated follow-up text.
5. Keep `tests/test_check_lifecycle.py`, `tests/test_luck_buyup_gate.py`, `tests/test_pending_luck_short_circuit.py`, `tests/test_resolved_check_events.py`, `tests/test_npc_attack_latency.py`, `tests/test_turn_safety.py`, and button routing tests green. Run full pytest, Ruff 0.16.8, `mypy app`, and `python -m compileall app tests` after implementation.

## Trade-off and review decision

A separate receipt adds persisted state but preserves the meaning of completed check events and makes the failure window explicit. Pausing the group preserves narrative ordering; without a KP Assistant, an absent original player must return to resume. Marco confirmed these trade-offs, the dedicated command/button, owner-or-KP authorization, timeline invalidation, and fail-closed partial-tool continuation. The detailed receipt encoding is an implementation choice subject to the invariants above. No runtime implementation begins until this spec is reviewed.
