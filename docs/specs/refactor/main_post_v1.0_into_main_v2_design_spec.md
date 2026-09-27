# Historical post-v1.0 integration ledger

[繁體中文](main_post_v1.0_into_main_v2_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `refactor`. Status: **historical**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. This records a completed port of selected main changes into main_v2. The integration ledger is historical; it is not an instruction to merge present-day branches again.

2. Current runtime is Discord-only and uses the unified Supervisor plus independent KP Assistant. Old app/commands.py and legacy full-turn diagrams are not live interfaces.

3. The baseline and original source-target ledger remain retrievable through the immutable source revision linked below. Later feature specs are authoritative for current behavior.

## Flow and interfaces

```text
Historical source commits -> main_v2 integration -> later unified-flow changes
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/commands/router.py](../../../app/commands/router.py)
- [app/agents/supervisor.py](../../../app/agents/supervisor.py)
- [app/discord_bot.py](../../../app/discord_bot.py)
- [tests/test_unified_keeper_turn_flow.py](../../../tests/test_unified_keeper_turn_flow.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/main_post_v1.0_into_main_v2_design_spec.md)
