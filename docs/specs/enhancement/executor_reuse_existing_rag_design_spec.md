# Reuse sufficient proactive scenario evidence

[繁體中文](executor_reuse_existing_rag_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Executor first checks the scenario content already supplied this turn. Facts explicitly covered there should not cause another identical or cosmetic reformulation search.

2. Keep search_scenario available for genuine gaps. A failed or missing proactive result must not suppress the explicit tool.

3. Chinese evidence can require original supplementation even after a hit. Completion of retrieval is not proof that armor, limits or consequences were all found.

4. Measure generative requests, embedding requests, input size, latency and correctness separately. Reuse is not evidence of a universal speedup.

## Flow and interfaces

```text
Proactive RAG -> required-fact check -> reuse / targeted supplementation -> mechanics
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/agents/context_builder.py](../../../app/agents/context_builder.py)
- [app/services/prompt_config.py](../../../app/services/prompt_config.py)
- [app/agents/executor.py](../../../app/agents/executor.py)
- [tests/test_executor_rag_reuse_prompt.py](../../../tests/test_executor_rag_reuse_prompt.py)
- [tests/test_scenario_query_fallback.py](../../../tests/test_scenario_query_fallback.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/executor_reuse_existing_rag_design_spec.md)
