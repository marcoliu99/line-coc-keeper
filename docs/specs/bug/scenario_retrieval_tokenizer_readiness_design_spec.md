# Scenario retrieval tokenizer readiness

Status: implemented. Base: main_v2. Branch: bug/scenario-retrieval-tokenizer-readiness.

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

## Results

- Declared tiktoken dependency installed in the main_v2 virtual environment; o200k_base loaded successfully.
- Incident root set r21/r2/r22/r9/r18: 6,000 retrieval budget, 3,459 required tokens, 3,824 projected tokens including metadata, all five fragments complete. Previous-turn roots also complete (5,268 projected tokens).
- Isolated full suite: 1,039 passed, 1 skipped, 33 subtests passed. Ruff and mypy pass.
- No live API replay or game state mutation. PR #101 is preserved separately when integrating the test worktree.

## Follow-up: repeated searches in the same turn

The 19:04–19:05 logs demonstrate that tokenizer installation alone was insufficient. Proactive retrieval had a 6,000-token budget, but the address follow-up had only 1,317 remaining. It attempted to send already-delivered required records again and blocked acquisition. A later search estimated 71,123 tokens because Executor passed internal before/after gameplay snapshots to retrieval admission, although those snapshots are not sent to the provider.

Maintain a turn-local set of fragments actually delivered in proactive context or successful search results. Reuse only those fragments, under the same source/timeline/chapter binding; still traverse dependencies and require every unseen mandatory fragment. Emit explicit reused-fragment metadata. Never reuse across turns or certify original hits as complete. Continuation offsets must reflect a contiguous delivered prefix.

Compute follow-up request budget from actual returned tool receipts and arguments, including current_turn_state, excluding internal validation snapshots. Preserve existing state validation snapshots for adjudication. Search incompleteness itself must remain visible in the final failure explanation even if the model never attempts a mutation. Add multi-search Executor integration and projection tests, including unseen dependencies and turn isolation.

Follow-up verification: 1,044 passed, 1 skipped, 33 subtests passed; Ruff and mypy pass. Added repeated-search acquisition and purchase flows, including an arrival denial and unseen mandatory dependencies. Providers are mocked; live player purchases were not replayed.

## Follow-up: ranked candidates are not mandatory dependencies

At 19:15, four distinct queued requests each emitted one notice; this was queue backlog, not duplicate delivery. The remaining retrieval failure was structural: five ranked candidate source units were treated as one mandatory dependency closure. The real imported records are independent source units with no dependency edges. An unrelated candidate could therefore block an otherwise supported action. A `blocked` turn was also narrated as completed travel with a held key; the subsequent door action had no tools and invalid evidence references. Raw model references were not retained, so their exact content remains unknown.

For ranked search, admit the highest-ranked record with its full mandatory/conditional dependency closure first. Add further independent candidates only if their complete closures fit. Report deferred candidate IDs/pages/names separately and explicitly label completeness as selected records plus required dependencies, not proof that every fact needed by the player's action is present. Never downgrade a declared required dependency to a candidate. A first closure that cannot fit still fails closed with continuation where possible.

Cap proactive context at 3,000 tokens (SCENARIO_PROACTIVE_TOKEN_BUDGET) within the existing request budget to leave space for targeted follow-ups. Keep the overall 32,000 ceiling, full search budget and output/safety reserves unchanged. This avoids increasing model capacity assumptions or bypassing token admission. Support partial retrieval through existing continuation; clarify that an original-source query cannot carry a Chinese cursor.

Enforce blocked narration deterministically, retaining partial changes and pending-roll guidance. Do not turn a refusal into travel/acquisition. Emit privacy-safe invalid-reference diagnostics (counts and scenario-context availability, not raw model content). Replay the real imported records with incident root sets and successive request budgets offline before deploying; unit tests alone do not establish live-model success.

Ranked-closure verification: 1,046 passed, 1 skipped, 33 subtests passed. Offline replay of all 32 real imported source units against three incident follow-up root sets (96 combinations) produced no incomplete selected closure or output-budget overrun; minimum follow-up budget 3,736 tokens. No embeddings or model API calls were made. This verifies retrieval availability, not semantic sufficiency for a shop or possession of a key.

## Follow-up: a failed tokenizer load must not latch for the process lifetime

Installing the declared dependency and restarting repaired the deployment, but the resolver itself stayed fragile. `_encoding` was an `lru_cache`, so a `None` returned after one failed load was kept for the life of the process. Loading reaches the network whenever the BPE cache is cold — a cold load measured 3.76s here — so a momentary fault at startup, or a purged OS temp cache, silently billed every later estimate at UTF-8 bytes. That overcounts CJK roughly threefold: the incident turn measured 19,213 tokens against 64,634 bytes, which turned 6,643 tokens of headroom into −38,778 and pinned the retrieval budget at zero until an operator noticed and restarted.

Resolve tokenizers through an explicit cache instead. Keep a success for the process; keep a failure only until a retry deadline (ENCODING_RETRY_SECONDS, 60s), so the next turn past that deadline re-attempts the load and recovers without a restart. Load outside the lock so a slow download does not block other turns, and bound the resolved map as the previous `maxsize` did. Keep emitting `llm.tokenizer.unavailable` with the error type, attempt count and retry window, and add `llm.tokenizer.recovered` only when a previous failure is cleared, so a first successful load is not reported as an incident. Neither event records prompts, credentials or exception content.

This narrows the window, it does not remove the fallback: estimates inside a retry window are still bytes, and a persistently missing dependency still degrades. Treating a zero budget under a byte fallback as an infrastructure fault rather than missing scenario evidence remains open.

Latching verification: 1,049 passed, 33 subtests passed; ruff, mypy and compileall pass. Two new offline tests cover a transient failure that recovers after the retry window without a reload inside it, and a resolved tokenizer that is cached without re-loading or emitting an incident event. No model or embedding API calls were made.

## PR review: final projection budget and recovered evidence status

The ranked projection's 300-unit reservation is a heuristic, not an admission guarantee. Measure the final public row, including the scope notice and the estimate field itself. Clamp the advertised capacity to the configured retrieval ceiling. Remove optional deferred-candidate descriptions until the row fits; retain their visible total count and the scope warning so omitted hints do not imply missing locations or rules do not exist. Never delete mandatory evidence to preserve a complete flag. If the evidence/control envelope itself exceeds the available capacity, mark it incomplete with `retrieval_budget_exceeded` and `budget_exceeded: true`, retaining its honest measured size. This control response can exceed an extremely small or zero budget; it must not certify executable evidence.

When successful, explicitly complete retrieval removes the final blocked root, synchronize `scenario_evidence_blocked` with the mutation gate. Unrelated hits, partial completion, failed searches and unknown-completeness original hits must not clear an outstanding root. Subsequent incomplete evidence closes the gate again. This prevents an unrelated later purchase failure from being narrated as missing scenario evidence, while retaining the existing deterministic failure guard.

Regression coverage includes long Chinese candidate names at 1,500/1,800/3,000-unit budgets with unknown-model bytes, named-model byte fallback and a deterministic token encoder; zero/tiny-budget overflow; and a multi-search recovery followed by an insufficient-funds failure through the real gateway and narration guard. These are offline tests and do not replay live purchases or call model APIs.

Review verification after merging current main_v2: 1,135 passed, 1 skipped, 33 subtests passed. Ruff, mypy (86 source files), and `git diff --check` passed.
