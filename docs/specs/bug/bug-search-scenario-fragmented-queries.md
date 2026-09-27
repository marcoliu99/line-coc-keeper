# Search for a complete current event

[繁體中文](bug-search-scenario-fragmented-queries_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Combine predictable related NPC, location, attack, ability, trigger and consequence needs into the same query. Prefer concrete names and source aliases to vague conversational questions.

2. There is no hard one-search-per-turn limit. Reuse sufficient proactive evidence but supplement missing facts; nonempty Chinese results do not establish completeness.

3. Player lookup respects the permitted chapter/event window. KP Assistant’s description supports legitimate preparation questions, subject to the actual context and privacy policy.

## Flow and interfaces

```text
Current event -> concrete combined query -> inspect evidence -> supplement only missing facts
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/keeper.py](../../../app/keeper.py)
- [app/scenario_templates.py](../../../app/scenario_templates.py)
- [tests/test_executor_rag_reuse_prompt.py](../../../tests/test_executor_rag_reuse_prompt.py)
- [tests/test_scenario_query_fallback.py](../../../tests/test_scenario_query_fallback.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-search-scenario-fragmented-queries.md)
