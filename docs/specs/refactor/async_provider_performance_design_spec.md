# Native asynchronous provider and I/O contracts

[繁體中文](async_provider_performance_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `refactor`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. All conversation providers expose the native async contract used by agents. Blocking local tool/state work is offloaded at the gateway boundary; authoritative mutations remain sequential.

2. Use explicit request/tool/embedding/Discord timeouts and cancellation propagation. Default request timeout is 60 seconds, tool execution 30, embeddings 20 and Discord 10.

3. Shared async clients close during shutdown with a configured grace period (default 5 seconds). A cancelled task must not turn into a successful canonical commit.

4. Scenario embedding prewarm is opt-in and bounded, default off with concurrency 1. It is index prewarming, not background scenario translation.

5. The old legacy conversation path is no longer present. Shared retries, adaptive admission and turn deadlines apply through current provider adapters; no speed guarantee follows from async alone.

## Flow and interfaces

```text
Async agent -> bounded async provider request -> sequential tool callback -> bounded continuation -> shutdown
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/providers/openai_provider.py](../../../app/providers/openai_provider.py)
- [app/providers/anthropic_provider.py](../../../app/providers/anthropic_provider.py)
- [app/providers/gemini_provider.py](../../../app/providers/gemini_provider.py)
- [app/providers/retry.py](../../../app/providers/retry.py)
- [app/providers/client_lifecycle.py](../../../app/providers/client_lifecycle.py)
- [app/async_utils.py](../../../app/async_utils.py)
- [tests/test_async_provider_contract.py](../../../tests/test_async_provider_contract.py)
- [tests/test_embedding_timeout.py](../../../tests/test_embedding_timeout.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/async_provider_performance_design_spec.md)
