# Shared retry and request admission

[繁體中文](enhancement-llm-rate-limit-and-turn-latency_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Retry classified transient failures with bounded exponential backoff and jitter; honor usable retry-after information. Do not retry arbitrary application errors or replay tools.

2. OpenAI concurrency defaults to 3. Adaptive admission is now implemented and enabled by default: shared response-header-derived RPM/TPM budgets and cooldown supplement the semaphore.

3. A turn deadline bounds accumulated waiting. Retry/queue diagnostics distinguish observed quota information from guessed causes; a 429 alone does not establish whether RPM or TPM caused it.

4. Admission is process-local, not a Redis/distributed queue or a Batch API migration. No fixed 180000-token budget is assumed for every account.

## Flow and interfaces

```text
Request budget -> shared admission/cooldown -> API -> classify -> retry within deadline
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/providers/retry.py](../../../app/providers/retry.py)
- [app/providers/admission.py](../../../app/providers/admission.py)
- [app/providers/turn_budget.py](../../../app/providers/turn_budget.py)
- [app/config.py](../../../app/config.py)
- [tests/test_llm_retry.py](../../../tests/test_llm_retry.py)
- [tests/test_adaptive_admission.py](../../../tests/test_adaptive_admission.py)
- [tests/test_retry_diagnostics.py](../../../tests/test_retry_diagnostics.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/enhancement-llm-rate-limit-and-turn-latency.md)
