# Historical Executor tiering and deferred tool scoping

[繁體中文](enhancement-executor-model-tiering-and-tool-scoping_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **superseded**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. The old dedicated Executor model/reasoning overrides were reverted in PR68. Current Executor uses shared provider configuration; KEEPER_REASONING_EFFORT defaults medium for the OpenAI conversation path.

2. Historical none/low/model comparisons are measurements of their original configurations, not current defaults or evidence that model tiering remains enabled.

3. General combat-state-dependent tool tiers remain a separate unimplemented spec. The current OpenAI combat-status freshness gate is narrower and must not be mislabeled as full dynamic scoping.

4. Any renewed proposal must compare accuracy, tool selection, requests, input size and full-turn latency on current code with adequate samples and rate-limit accounting.

## Flow and interfaces

```text
Shared provider configuration -> current role tools -> limited status gate -> Executor
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/agents/executor.py](../../../app/agents/executor.py)
- [app/config.py](../../../app/config.py)
- [app/providers/openai_provider.py](../../../app/providers/openai_provider.py)
- [tests/test_config_defaults.py](../../../tests/test_config_defaults.py)
- [tests/test_combat_status_tool_gate.py](../../../tests/test_combat_status_tool_gate.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/enhancement-executor-model-tiering-and-tool-scoping.md)
