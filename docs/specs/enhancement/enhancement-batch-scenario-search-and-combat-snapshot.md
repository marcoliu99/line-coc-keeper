# Combat snapshot reuse and proposed batch search

[繁體中文](enhancement-batch-scenario-search-and-combat-snapshot_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **partial**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Implemented: the OpenAI request-level tool callback gates redundant get_combat_status while the dynamic combat snapshot is fresh. Actual state changes, including enemy planning effects, make refresh available again.

2. The gate must be recomputed between model requests. A list fixed only at turn start cannot correctly follow same-turn combat mutations.

3. Not implemented: a queries array, batched query embeddings and interleaved multi-query result merging. The current search tool accepts a single query plus optional source.

4. The historical trial was too small to establish universal latency savings. Future batch search must retain coverage and measure additional embedding work rather than assume fewer tools means fewer requests.

## Flow and interfaces

```text
Fresh combat snapshot -> omit redundant status tool -> mutation invalidates -> refresh allowed
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/keeper.py](../../../app/keeper.py)
- [app/agents/executor.py](../../../app/agents/executor.py)
- [app/providers/openai_provider.py](../../../app/providers/openai_provider.py)
- [tests/test_combat_status_tool_gate.py](../../../tests/test_combat_status_tool_gate.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/enhancement-batch-scenario-search-and-combat-snapshot.md)
