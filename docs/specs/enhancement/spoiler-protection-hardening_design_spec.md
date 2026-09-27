# Independent spoiler and privacy policy

[繁體中文](spoiler-protection-hardening_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. SPOILER_PROTECTION_ENABLED and PRIVACY_ISOLATION_ENABLED are independent and default true. The former controls reveal pacing; the latter controls ownership/private destinations.

2. Apply policy to scenario chapters, indexes/pregens, scene summaries, NPC/Enemy details, handouts and image tools. A public-facing response must not inherit KP-only content just because retrieval can access it internally.

3. Maintain separate public/private projections and actual callback checks. Prompt instructions supplement deterministic filtering; they are not a substitute for it.

4. KP preparation and OOC authority do not automatically authorize publishing player secrets. Disabling reveal pacing must not implicitly disable privacy isolation.

5. Spoiler filtering does not prove narrative truth or mechanically verify new world facts. Canon boundaries, correction receipts and evidence handoffs remain separate.

## Flow and interfaces

```text
Role/context -> chapter and asset policy -> private/public projection -> output filtering
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/spoiler_policy.py](../../../app/spoiler_policy.py)
- [app/keeper.py](../../../app/keeper.py)
- [app/agents/supervisor.py](../../../app/agents/supervisor.py)
- [app/agents/assistant.py](../../../app/agents/assistant.py)
- [app/scenario_library.py](../../../app/scenario_library.py)
- [tests/test_spoiler_policy.py](../../../tests/test_spoiler_policy.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/spoiler-protection-hardening_design_spec.md)
