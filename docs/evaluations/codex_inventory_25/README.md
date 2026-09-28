# Codex pending inventory follow-up: 25 real OAuth cases

Date: 2026-09-28. CLI 0.157.1, gpt-6-luna, medium reasoning. Isolated
synthetic Supervisor fixtures, deterministic Python dice, no Discord or production
data. Same implementation and five kinds in both transports; three repetitions
with exec (15 cases), two with app-server (10). Both processes shared OAuth
capacity, so latency differences are descriptive rather than controlled results.

## Causes and changes

The previous 18-case trial had two exec cases where add_carried_item succeeded,
but resolved_without_check conflicted with the unchanged pending check. Python
rejected the handoff only after Executor exited, so the model never received the
rejection or an opportunity to correct the handoff using its existing receipt.
One app-server case claimed the scenario permitted pickup without dispatching
add_carried_item. Scenario permission was confused with a committed state change.

The new context explicitly distinguishes permission, execution and remaining
pending work: execute the independent inventory action, verify its receipt and
inventory, then hand back await_check with the old identity and the new receipt.
An existing waiting candidate must not replace the requested inventory action.

Codex additionally receives at most one rejected-final feedback from the existing
Python validator inside its decision loop. It must preserve successful tools;
there is no automatic inventory mutation, tool replay, relaxed validation or
fixed extra reviewer. The usual validator still checks the final returned result.
Other providers are unchanged. Existing incomplete-without-dispatch recovery
remains separately bounded. Both retries share the conversation iteration and
deadline limits; repair may add one or more requests when further tools are needed.

Unit integration tests reproduce both bad outputs through the real Supervisor,
Executor and inventory gateway. They check exact pending preservation, one item
addition, no dice, and successful validated handoff. Other tests cover bounded
feedback, persistent blockers and no replay after a committed tool.

## Measurement

See summary.json and the result table below. A case includes any required check
submission and follow-up narration, so 25 cases does not mean 25 model requests.
First-pass success excludes invalid proposals, incomplete retries and final
repairs; full flow success also requires fixture state/narration assertions.
Tool correctness verifies expected successful calls/arguments and excludes
unexpected calls and rejected proposals. These limited fixtures do not validate
all possible game mechanics or every natural-language claim.

Raw synthetic decisions, receipts and validation codes are retained in JSONL;
duplicate dynamic prompt bodies are omitted. No failed cases are discarded.
The original 18-case report remains unchanged.

## Reproduce

```sh
python scripts/codex_evaluate.py --transport exec --output /tmp/inventory-exec.jsonl --repeats 3 --kinds pending_pickup pickup pending check_success check_failure --trace
python scripts/codex_evaluate.py --transport app-server --output /tmp/inventory-server.jsonl --repeats 2 --kinds pending_pickup pickup pending check_success check_failure --trace
```

## Results

| Transport | Cases / flow / first-pass tools | Median | P95 | Model requests |
|---|---:|---:|---:|---:|
| exec | 15 / 15 / 15 | 27.844 s | 34.624 s | 45 |
| server | 10 / 10 / 10 | 22.066 s | 29.901 s | 30 |

Total flow and tool correctness: **25/25**. 75 model requests, 20 game calls, 10 player check rolls. Each of the five kinds passed **5/5**. No rejected proposals, final repairs or incomplete retries. Previous pending-pickup flow was 3/6 and tools 5/6; this sample is 5/5. Different small sample sizes/mixes do not establish an effect size. Final feedback was not triggered live, so its protection is demonstrated by unit/integration tests, not causal attribution for this trial.

Regression: 1,319 passed, 1 skipped, 47 subtests passed; Ruff and mypy (94 files) passed. No PR or merge.
