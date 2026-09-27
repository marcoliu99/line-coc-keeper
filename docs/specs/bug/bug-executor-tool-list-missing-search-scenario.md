# RAG-aware role-specific Executor tools

[繁體中文](bug-executor-tool-list-missing-search-scenario_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Build tools from tools_for_speaker_role at turn time, not from a frozen module-level list. RAG-enabled player turns must expose search_scenario.

2. KP Assistant restrictions and descriptions apply independently of player tools. Callback authorization remains necessary even when a forbidden tool is omitted from the schema.

3. Current search accepts query and optional source=auto|original. Preserve original-source supplementation when Chinese hits lack required facts.

## Flow and interfaces

```text
Turn -> current role/config -> tool definitions -> callback authorization
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/agents/executor.py](../../../app/agents/executor.py)
- [app/agents/tool_gateway.py](../../../app/agents/tool_gateway.py)
- [app/keeper.py](../../../app/keeper.py)
- [tests/test_tool_gateway_speaker_role.py](../../../tests/test_tool_gateway_speaker_role.py)
- [tests/test_executor_rag_reuse_prompt.py](../../../tests/test_executor_rag_reuse_prompt.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-executor-tool-list-missing-search-scenario.md)
