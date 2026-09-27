# Dynamic Executor tool scoping

[繁體中文](enhancement-executor-dynamic-tool-scoping_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **backlog**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. The preserved branch contains a design only. Full state-dependent tool tiers are not implemented; the existing combat-status freshness gate is a narrower feature.

2. A viable design must recompute availability within a turn, including start_combat followed by NPC additions. Turn-start-only filtering can hide necessary followup tools.

3. Read-only scenario lookup, pending/Luck handling and role-specific callback permissions must remain correct at every iteration. Reduced exposure cannot substitute for runtime authorization.

4. Rebase the old model/tool-count assumptions on the present unified pipeline. Benchmark full versus scoped tools with the same cases, provider settings and data; include input, requests, retries, latency and correctness.

## Flow and interfaces

```text
Proposed: classify current state -> choose tools -> execute -> recompute after each mutation
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/agents/executor.py](../../../app/agents/executor.py)
- [app/keeper.py](../../../app/keeper.py)
- [app/providers/openai_provider.py](../../../app/providers/openai_provider.py)
- [tests/test_combat_status_tool_gate.py](../../../tests/test_combat_status_tool_gate.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/b50b2385c4219cd7b92ffe8c18a8be0bd4690f00/docs/specs/enhancement-executor-dynamic-tool-scoping.md)
