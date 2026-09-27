# Pending-button delivery and scenario retrieval latency

[繁體中文](turn_latency_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Pending buttons use a short locked claim followed by network delivery outside the turn lock. Ownership, timeline, duplicates and failed-delivery recovery remain enforced.

2. PR83 retrieval now uses external Chinese preparation and current-window original fallback. Earlier automatic translation-job proposals in this history are superseded.

3. A speed optimization cannot remove necessary scenario facts or split an otherwise complete operation into more model round trips just to shrink tools.

4. Compare retrieval composition, generative versus embedding calls, full-turn duration and mechanic/tool accuracy. Include retries/queueing and distinguish historical pilots from current measurements.

## Flow and interfaces

```text
Claim pending delivery under lock -> unlock -> Discord send
Prepared Chinese index -> evidence-aware search -> original supplementation as needed
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/discord_bot.py](../../../app/discord_bot.py)
- [app/locks.py](../../../app/locks.py)
- [app/scenario_templates.py](../../../app/scenario_templates.py)
- [app/agents/context_builder.py](../../../app/agents/context_builder.py)
- [tests/test_pending_button_latency.py](../../../tests/test_pending_button_latency.py)
- [tests/test_scenario_query_fallback.py](../../../tests/test_scenario_query_fallback.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/turn_latency_design_spec.md)
