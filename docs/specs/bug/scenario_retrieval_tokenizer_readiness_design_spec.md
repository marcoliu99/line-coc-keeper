# Scenario retrieval tokenizer readiness

Status: implementation in progress. Base: main_v2. Branch: bug/scenario-retrieval-tokenizer-readiness.

## Incident and evidence

On 2026-09-27, the player requested taking the advance cash and keys and reading the address. The archived request ended after 26.76 seconds with `invalid_evidence_reference`. Three scenario searches succeeded, but only search facts reached Narrator; six attempted tools were counted. The log does not retain the three other tool names or the model's raw final references, so those details cannot be reconstructed with certainty.

The deployed virtual environment lacked the already-declared `tiktoken` dependency. UTF-8 byte fallback estimated the Executor input at 65,865 units; the configured 32,000 context ceiling yielded zero retrieval budget. Chinese projection returned no content and incomplete required evidence. Original-language search returned hits with unknown completeness; this correctly did not certify the missing Chinese dependencies. The gateway rejected mutations before its normal logging/facts path. Final narration replaced the result with a generic instruction to correct the player's action.

A read-only state snapshot confirms no keys or advance cash in inventory/ledger and no pending check/Luck. Opening narration had described receipt without corresponding persisted acquisition. Do not repair live state by replaying or adding items automatically.

## Fix and scope

1. Install the declared tokenizer dependency in the deployed virtual environment and verify actual encoding loads. Retain the conservative fallback and context ceiling.
2. Emit a bounded diagnostic when tokenizer initialization fails, including exception type but no exception message, prompts, or credentials.
3. Report retrieval context cost, reserve, available budget, and actual tokenizer method. Projection metadata must report byte fallback honestly even for a named model.
4. Preserve rejected evidence/correction-gate attempts in factual handoff and structured diagnostics, without executing the underlying tool. Retain the existing evidence and mutation validators.
5. For an incomplete turn with an observed scenario-evidence rejection, return a specific explanation that evidence is missing; preserve existing partial-state and pending-check protections. Do not imply the user's declaration is invalid.

No schema changes, fixed additional LLM calls, fabricated addresses, automatic original-hit certification, or live game state mutations.

## Flow

    Load token encoder -> estimate request + reserves -> allocate retrieval budget
            | unavailable: conservative bytes + diagnostic
    Chinese evidence -> completeness gate -> tool execution / recorded rejection
    Final resolution -> existing state validation -> safe, cause-specific reply

## Verification

- Simulate missing tokenizer: conservative estimation, explicit diagnostic, honest metadata.
- Reconstruct the incident's two Chinese root sets offline: complete with tokenizer, zero-budget incomplete without it.
- Evidence rejection records a fact and diagnostic without mutating state; unrelated original hits do not release the gate.
- Matching complete evidence permits actual inventory tools and validated handoff in a mocked-provider turn.
- Incomplete replies retain partial-state/check protections and explain evidence blocking.
- Run the full isolated suite, lint, and typing. No paid model calls.

## Limits

Offline replay validates retrieval, gating, state mutation and handoff, not the real model's next chosen tools. No specific street number is supported by the retrieved handout. Restart is required for the existing bot process to discard its cached unavailable tokenizer; the user controls restart.
