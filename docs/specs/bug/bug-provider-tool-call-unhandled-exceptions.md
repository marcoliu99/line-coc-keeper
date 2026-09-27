# Malformed provider calls and partial-failure handling

[繁體中文](bug-provider-tool-call-unhandled-exceptions_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Malformed OpenAI function-call JSON must not crash the entire tool loop without a controlled result. Truncated responses are checked before any contained tools execute.

2. An API failure after successful tools must retain committed state and queued private/image output. Retrying a model request does not authorize replaying committed tools.

3. Legacy run_turn exception handling is historical. Current failure boundaries are Executor, Narrator/Supervisor and independent KP Assistant.

## Flow and interfaces

```text
Provider output -> parse/validate -> tool result or safe failure -> preserve committed effects
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/providers/openai_provider.py](../../../app/providers/openai_provider.py)
- [app/agents/executor.py](../../../app/agents/executor.py)
- [app/agents/assistant.py](../../../app/agents/assistant.py)
- [tests/test_async_provider_contract.py](../../../tests/test_async_provider_contract.py)
- [tests/test_turn_consistency_handoff.py](../../../tests/test_turn_consistency_handoff.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-provider-tool-call-unhandled-exceptions.md)
