# Offer every legal useful Luck upgrade

[繁體中文](enhancement-luck-buyup-always-offered_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Remove the historical near-miss distance cap. Offer spending whenever a legal affordable option can change the required result.

2. Respect required difficulty, opposed-defense rules and Luck eligibility. Never offer an upgrade that still fails the actual requirement or violates forbidden Luck cases.

3. Keep original roll, decision ID, owner and timeline. Spending/declining is idempotent and cannot reroll the original check.

## Flow and interfaces

```text
Resolved eligible roll -> useful affordable tiers -> owner decides -> persist once
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/luck.py](../../../app/luck.py)
- [app/keeper.py](../../../app/keeper.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [app/discord_bot.py](../../../app/discord_bot.py)
- [tests/test_luck_buyup_gate.py](../../../tests/test_luck_buyup_gate.py)
- [tests/test_npc_attack_latency.py](../../../tests/test_npc_attack_latency.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/enhancement-luck-buyup-always-offered.md)
