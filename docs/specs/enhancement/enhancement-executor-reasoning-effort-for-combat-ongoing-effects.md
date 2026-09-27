# Proposed reasoning policy for ongoing combat effects

[繁體中文](enhancement-executor-reasoning-effort-for-combat-ongoing-effects_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **backlog**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. No combat-specific reasoning override is currently implemented. The original premise of an Executor-only none default is obsolete after dedicated tiering was reverted.

2. The historical incident compared none (4 trials), low (7) and medium (1). These tiny samples motivate a hypothesis, not a reliable policy or current benchmark.

3. Define whether the policy covers existing effects, newly created effects, or both. Measure state/mechanic correctness as well as latency and token cost under current prompts and models.

4. Do not substitute reasoning effort for deterministic effect lifecycle or authoritative damage tools. Any future override must retain the current provider fallback and configuration behavior.

## Flow and interfaces

```text
Proposed: identify effect-sensitive turn -> select validated reasoning policy -> compare mechanics and latency
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/config.py](../../../app/config.py)
- [app/agents/executor.py](../../../app/agents/executor.py)
- [app/combat.py](../../../app/combat.py)
- [tests/test_config_defaults.py](../../../tests/test_config_defaults.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/0a448e157da82d1ae97355b9399106e8991f4060/docs/specs/enhancement-executor-reasoning-effort-for-combat-ongoing-effects.md)
