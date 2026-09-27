# NPC attack latency and deterministic consequences

[繁體中文](npc_attack_latency_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Consolidate attack registration and player defense choices where supported, preserving authoritative attack rolls, weapon metadata and pending identity.

2. During combat, proactive scenario RAG is avoided by the existing context policy; explicit search remains available when necessary. Do not confuse this with deleting scenario lookup for attacks or abilities.

3. Melee/ranged resolution, Luck and damage follow current deterministic rules. A latency change cannot shorten the turn by dropping required damage, limits or pending choices.

4. Historical log timings motivate the work but are not guarantees for current providers. Measure full-turn latency including tool continuation and delivery.

## Flow and interfaces

```text
NPC attack tool -> authoritative attack/defense state -> player decision -> deterministic resolution -> followup
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/keeper.py](../../../app/keeper.py)
- [app/agents/context_builder.py](../../../app/agents/context_builder.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [app/dice.py](../../../app/dice.py)
- [tests/test_npc_attack_latency.py](../../../tests/test_npc_attack_latency.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/npc_attack_latency_design_spec.md)
