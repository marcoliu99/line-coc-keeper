# Codex 150-case stability evaluation

Date: 2026-09-28. Runtime commit: `90f0299`. Codex CLI 0.157.1,
gpt-6-luna, medium reasoning; exec and app-server run 75 cases each.
Each transport repeats the same five synthetic fixtures 15 times: pending pickup,
ordinary pickup, preserve an existing check, successful check with follow-up,
and failed check with follow-up. No runtime changes during the evaluation.

These are real ChatGPT OAuth requests through the existing Supervisor pipeline,
with isolated synthetic data and deterministic Python dice. No production state
or Discord traffic. This does not exercise a full campaign, live RAG, arbitrary
combat, real user concurrency, or every possible narrative assertion.

Both processes share OAuth capacity; timing is descriptive. A scenario includes
any required player check submission and follow-up narration, so case counts
are not model request counts. A failed case is retained rather than replaced
with a rerun. JSONL retains decisions, tool receipts and validator outcomes;
repeated dynamic prompts are omitted.

## Metrics

- Flow: state and narrative fixture assertions pass.
- Tools: expected successful calls/arguments, no unexpected calls or locally
  rejected proposals. A safely rejected proposal is still a tool error.
- First pass: flow and tools pass without incomplete or rejected-final repair.
- Repairs, model requests, actual game calls, Python rolls and case latency are
  reported separately. A repaired success is not silently counted as first-pass.

## Reproduce

```sh
python scripts/codex_evaluate.py --transport exec --output /tmp/inventory-150-exec.jsonl --repeats 15 --kinds pending_pickup pickup pending check_success check_failure --trace
python scripts/codex_evaluate.py --transport app-server --output /tmp/inventory-150-server.jsonl --repeats 15 --kinds pending_pickup pickup pending check_success check_failure --trace
```

Previous [25-case results](../codex_inventory_25/README.md) and
[18-case diagnosis](../codex_tool_correctness/README.md) remain unchanged.

## Observed defects

- app-server repetitions 7 and 13, pending_pickup: the first final cited nonexistent
  tool:1 and claimed pickup before dispatch. Python rejected the reference;
  one final-feedback recovery caused exactly one add_carried_item and a valid
  waiting handoff. This is recovered success, not first-pass correctness.
- app-server repetition 8, pending: the player only repeated the document action,
  but the model called add_carried_item for the scenario's key. The real inventory
  mutated. The pending identity remained intact and its waiting resolution passed.
  The tool correctness assertion correctly fails this case. The fixture's flow
  assertion is too weak here because it checks pending and spoilers but not an
  unchanged inventory; therefore overall acceptance MUST require flow AND tools.
- app-server repetition 10, pending_pickup: no inventory tool was called. The model
  returned await_check with a valid old identity and state-only evidence, while
  admitting the requested pickup had no success receipt. Python accepted this
  pending identity and no recovery was triggered. The inventory assertion failed.

The remaining gap is request-to-effect coverage. Availability of an item in the
scenario does not authorize taking it in this player turn, and a valid pending
identity does not prove that a newly requested independent action was performed.
The Codex final-feedback repair only helps when existing validation rejects a
final; it cannot fix semantically incomplete finals that the validator accepts.

Next hardening should explicitly connect requested actions to allowed mutations
and verified effects, distinguish completed independent work from remaining
pending, and strengthen pure-pending inventory invariants. Exact repeated pending
actions can conservatively use a read-only tool scope; broader natural-language
intent needs a grounded action contract, not ad-hoc keyword matching. Do not
weaken evidence validation or auto-execute guessed inventory actions. No runtime
fix is included in this fixed-version evaluation.

## Final results

| Transport | Strict final pass | First pass | Final repairs | Median | P95 |
|---|---:|---:|---:|---:|---:|
| exec | 75/75 | 75/75 | 0 | 25.365 s | 38.035 s |
| server | 73/75 | 71/75 | 2 | 23.738 s | 38.202 s |

Overall **148/150 (98.67%)**, first pass **146/150 (97.33%)**. 452 model requests, 120 game calls, 60 deterministic player rolls. No observed reroll or overwritten pending, but one unsolicited inventory mutation means state safety cannot be claimed universally.

Pending pickup: 29/30 final, 27/30 first pass. Pure pending: 29/30. Ordinary pickup, successful checks and failed checks: 30/30 each. Raw flow assertions pass 149/150, but include the unsolicited pickup caught by tool assertions; do not present that as overall success.

The previous 25/25 did not establish absence of rare errors. Keep the current exec setting while strengthening request/effect validation before evaluating app-server further; even exec 75/75 does not establish universal correctness. This run adds reports only; no runtime changes, PR or merge.
