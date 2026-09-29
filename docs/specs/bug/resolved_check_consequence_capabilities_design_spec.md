# Safe consequences after a resolved check

[繁體中文](resolved_check_consequence_capabilities_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Based on `main_v2` at `7cef87c`.

This spec covers consequences that become due after an investigator check is already settled. It addresses two observed failures: environmental damage was rolled but could not be committed to HP in the restricted resolved-check follow-up; and a successful Spot Hidden result could require a new Dodge check, but the follow-up could only ask the player what they did.

## Goal

Keep the original check immutable while allowing its verified outcome to produce a new, narrowly authorized consequence: a non-combat injury or a distinct player-owned check.

## Required behavior

1. Add `apply_resolved_check_damage` to the resolved-check follow-up capability. It accepts an investigator, exactly one of `damage_expression` or `final_damage`, a typed `damage_type`, `source_check_id`, `source_event_id`, `consequence_key`, and a human-readable `cause`.
   - With an expression, the deterministic dice engine rolls once. The roll receipt, HP change, and consequence receipt commit atomically.
   - With a finalized amount, the tool applies that amount without rolling again.
   - The existing major-wound rule is preserved; if required, register the CON check in the same state mutation.
   - Do not expose general `adjust_character` in this capability.

2. Add `create_triggered_check` (or an equivalently named narrow tool) for a new check caused by a settled check. It takes the investigator, skill, difficulty, originating check/event IDs, a typed trigger condition, a consequence key, and action context. It creates a new pending check with a new `check_id`; it never resolves the player's roll itself.
   - The original check remains settled and cannot be recreated or rerolled.
   - The new check uses the existing check lifecycle ownership gate and ordinary player check button/command.
   - A triggered check remains player-rolled even when group autoroll is enabled.

3. Both tools validate the active timeline, originating check/event, actor and target character identity, the settled result, and an authorization that the specific typed consequence is allowed for that outcome. Caller-supplied prose such as `cause` or `trigger_condition` is descriptive and is not authorization by itself.

4. Consequence identity is unique by `(timeline_id, source_event_id, consequence_key, subject_id)`. Persist a receipt containing the generated result or new pending check ID. An identical retry returns that receipt without rolling, applying HP again, or creating another check. A reused key with different arguments is rejected.

5. Keep consequence receipts and their authorization durable independently of the bounded `resolved_check_events` display/audit list. A stale timeline, mismatched actor/target, absent authorization, or outcome mismatch is rejected without mutation. Timeline replacement invalidates old authorizations and pending consequences.

6. Update resolved-check narration policy: never recreate, reroll, or reinterpret the originating check. If its authorized consequence is a new check, use `create_triggered_check` and let the player roll it. If its authorized consequence is damage, submit it through `apply_resolved_check_damage`; narration alone does not commit damage.

## Flow

```text
Check A settles
  -> validate its persisted consequence authorization
  -> apply_resolved_check_damage -> receipt + HP/CON state
     or
  -> create_triggered_check -> new pending Check B
  -> retry with same consequence identity returns the existing receipt
```

## Non-goals

- Do not add general `adjust_character` or `skill_check` to the resolved-check allowlist.
- Do not add a fixed LLM review stage or rerun the original check.
- Do not infer damage formulas or check triggers from narrative plausibility. Scenario rules and evidence must authorize them.
- Do not change ordinary check behavior, normal combat damage tools, privacy projection, or KP Assistant permissions.

## Data and integration requirements

- The originating `skill_check` may carry a bounded `consequences` plan before rolling. Each entry states a stable key, result condition, type, exact scenario quote, and damage or next-check parameters. Python checks the quote against the active scenario and the damage expression against that quote. The plan travels through pending check, Luck decision, and the settled result, then is persisted before Narrator follow-up. `cause` and `trigger_condition` never add authorization later.
- A fixed `final_damage` is accepted only when that exact fixed amount was authorized on the originating check. A separately rolled damage value without a durable linked receipt cannot be treated as preauthorized fixed damage.
- Persist an outcome-bound consequence authorization and idempotent receipt keyed to timeline, source event, consequence key, and subject. The bounded `resolved_check_events` list is not a sufficient retry ledger.
- Bind the tool call to the original turn actor using trusted request context; do not trust a model-provided actor ID. Resolve the target by the source event's character identity, not only a display name.
- Perform dice generation, HP mutation, major-wound handling, receipt persistence, and triggered-check registration through the existing authoritative state mutation/check-lifecycle boundaries. A partial commit must not leave a spent damage roll without its HP result.
- Keep both tools unavailable to ordinary unrestricted calls unless a separate capability explicitly authorizes them.

## Verification plan

- A resolved non-combat damage expression is rolled once, changes HP once, and returns the same receipt after duplicate call or provider retry.
- A finalized damage amount is applied without another roll; wrong timeline, actor, target, outcome, damage type, or consequence key cannot mutate state.
- Major-wound damage preserves the existing CON check gate and does not create duplicate pending checks.
- A successful Spot Hidden authorization can create a distinct Dodge pending check. Repeating the same consequence returns the same `check_id`; attempting to recreate the originating Spot Hidden check is rejected.
- Triggered checks remain pending for the player even with autoroll on; the original result remains unchanged.
- A source-backed scenario fixture covers the bed attack's trigger and damage rule. The original *The Haunting* PDF, physical page 9 (printed page 25), confirms the fall damage is `1D6 + 2` HP and Spot Hidden success permits Dodge. The parsed scenario text also contains this formula, but two-column extraction separates it from the Bed Attack heading; retrieval can therefore still miss the connection. This change validates quoted evidence but does not repair PDF column ordering.
- Run focused tool-gateway, check-lifecycle, state-persistence, narrator-handoff tests, then the full suite, Ruff 0.16.8, mypy, and compileall.

## Implementation decision

The originating `skill_check` validates and carries the plan before rolling. Manual, Luck, and autoroll paths preserve it; the settled event publishes a durable origin before Narrator follow-up. Both consequence tools verify this origin and the actual outcome. A free-text `cause` or `trigger_condition` cannot authorize a consequence.
