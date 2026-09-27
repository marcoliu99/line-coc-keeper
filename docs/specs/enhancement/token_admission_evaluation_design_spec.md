# Input budgets, adaptive admission and truncation

[繁體中文](token_admission_evaluation_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Estimate input components and select recent conversation history under OPENAI_HISTORY_TOKEN_BUDGET (default 4000), retaining OPENAI_HISTORY_MIN_TURNS (default 2). This is a soft history-only budget; authoritative state and stored history remain intact.

2. Adaptive admission defaults on and learns available RPM/TPM from provider headers. Reserve estimated request demand, share cooldowns and respect LLM_TURN_DEADLINE_SECONDS (default 180).

3. Stage-specific OpenAI output caps default 0, meaning no explicit new cap from these settings. Do not claim fixed 1000/2000-token caps have been adopted.

4. Reject incomplete/truncated tool responses before executing their calls. Keep earlier committed state and output queues without replaying prior actions.

5. Evaluate changes independently: input composition, 429 count, queue time, requests, full-turn duration, completion and mechanic/tool accuracy. Historic benchmark numbers are not refreshed by unit tests.

## Flow and interfaces

```text
Measure input components -> select history -> reserve quota -> request within deadline -> reject incomplete tools
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/services/input_budget.py](../../../app/services/input_budget.py)
- [app/providers/admission.py](../../../app/providers/admission.py)
- [app/providers/turn_budget.py](../../../app/providers/turn_budget.py)
- [app/providers/openai_provider.py](../../../app/providers/openai_provider.py)
- [app/config.py](../../../app/config.py)
- [tests/test_token_admission_experiment.py](../../../tests/test_token_admission_experiment.py)
- [tests/test_adaptive_admission.py](../../../tests/test_adaptive_admission.py)
- [tests/test_retry_diagnostics.py](../../../tests/test_retry_diagnostics.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/token_admission_evaluation_design_spec.md)
