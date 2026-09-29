# Structured request and turn observability

[繁體中文](structured_performance_logging_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `feature`. Status: **implemented**. Initially audited against `main_v2` at `afe8ace` (2026-09-27); the provider-specific reasoning-effort contract was updated in PR #140.

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Keep request/turn/agent/provider context across async work and thread offloads. Record outcome, duration, request counts, usage, RAG and tool metrics at their actual boundaries.

2. LOG_ENABLED defaults false; LOG_TEXT_ENABLED defaults true. Structured metrics and free-text logging are independent, and disabling metrics avoids unnecessary instrumentation work.

3. Use allowlisted safe error/rate-limit fields and hash identifiers by default. Do not place model/scenario/player prose into structured events to diagnose a rejection.

4. Report actual delivery completion and tool-call counts, including failures. Generation time, queue time and whole-turn latency are distinct and must not be conflated.

5. Current Executor resolution logs include fixed validation codes; scenario search records source/fallback diagnostics without promoting a retrieval hit to a completeness guarantee.

6. `llm.turn` and `llm.request` record the configured `reasoning_effort` for the active conversation provider. OpenAI uses `KEEPER_REASONING_EFFORT`; Codex uses `CODEX_REASONING_EFFORT`, matching its `codex.conversation` event. Providers without a configured reasoning-effort setting record `null`. This field describes the setting sent to the provider, not a measured reasoning-token count.

## Flow and interfaces

```text
Request/turn context -> nested spans and counters -> outcome/delivery -> bounded structured event
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/observability.py](../../../app/observability.py)
- [app/logging_config.py](../../../app/logging_config.py)
- [app/providers/retry.py](../../../app/providers/retry.py)
- [app/agents/executor.py](../../../app/agents/executor.py)
- [app/agents/narrator.py](../../../app/agents/narrator.py)
- [app/providers/codex_provider.py](../../../app/providers/codex_provider.py)
- [app/discord_bot.py](../../../app/discord_bot.py)
- [tests/test_observability.py](../../../tests/test_observability.py)
- [tests/test_logging_completion.py](../../../tests/test_logging_completion.py)
- [tests/test_retry_diagnostics.py](../../../tests/test_retry_diagnostics.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/structured_performance_logging_design_spec.md)
