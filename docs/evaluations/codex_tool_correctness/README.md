# Codex tool correctness investigation (2026-09-28)

Environment: Codex CLI 0.157.1, gpt-6-luna, medium reasoning, isolated synthetic
Supervisor fixtures, real ChatGPT OAuth. No production data or Discord traffic.
Both transports ran concurrently; these small samples are diagnostic, not a
controlled latency comparison or a general accuracy guarantee.

## Findings

1. Pending state and check IDs were already present. The original 50-case run
   advertised check-creation tools even for an occupied investigator. Seven
   duplicate skill_check attempts were rejected by Python; state safety did not
   mean correct model decisions.
2. A valid six-case baseline trace reproduced a model claiming skill_check was
   unavailable while its request listed that tool. Native Codex tool isolation
   was confused with Python host JSON actions. Baseline flow success was 4/6;
   all actual calls were legal, demonstrating why call validity alone is weak.
3. After explicit protocol examples and pending target restrictions, 10/12
   diagnostic cases passed. Both remaining new-check failures emitted final
   incomplete without any tool_call, claiming a request had been made and its
   result was missing. This directly reproduces the failure class. The original
   50-case missing-check failure has no raw decision trace, so its exact cause
   cannot be proven retroactively.

## Changes

- Distinguish executable JSON host actions from disabled native tools.
- Refresh decision context and exact pending/Luck identity after each tool.
- Limit check-creation investigator schemas to currently eligible characters;
  retain explicit correction/clear, other investigators and inventory tools.
- Validate refreshed argument constraints immediately before execution.
- Allow one conditional retry for incomplete Executor finals with no prior
  transcript/dispatch, available tools and sufficient iteration budget. Genuine
  blockers remain incomplete. No automatic check creation or fixed reviewer.
- Trace rejected proposals, conditional retries, decisions, receipts and Python
  validation codes separately. Traces here omit duplicated dynamic prompts;
  all content is synthetic. The invalid early instrumentation run is excluded.

## Final targeted results

| Case | exec flow/tools | app-server flow/tools |
|---|---:|---:|
| Existing pending, preserve identity | 3/3, 3/3 | 3/3, 3/3 |
| New check, Python roll, follow-up narration | 3/3, 3/3 | 3/3, 3/3 |
| Pending plus unrelated pickup stress probe | 1/3, 3/3 | 2/3, 2/3 |

The requested pending/new-check paths passed 12/12 with no rejected proposals,
conditional retries or unwanted rolls. The retry safeguard is covered by unit
fixtures; it was not exercised in the final live sample. Do not infer its causal
benefit or universal reliability from those 12 successes.

Including the extra pickup probe: flow 15/18, tool correctness 17/18. Remaining
issues are real: twice exec acquired the item but declared resolved_without_check,
which Python rejected because the old pending remained. The existing completion
exception only covers exact investigator-to-investigator transfers; a waiting
resolution can still be valid with preserved pending. Once app-server merely
claimed pickup without dispatch. The final sample is therefore **not fully
correct**, despite no overwritten pending or unsolicited dice rolls. Further
work should connect action effects, remaining pending, and final handoff without
loosening evidence validation or assuming every missing call needs a skill check.

Regression: 1,316 tests passed, 1 skipped, 45 subtests passed; Ruff passed; mypy
passed for 94 source files. No PR or merge.

## Reproduce

```sh
python scripts/codex_evaluate.py --transport exec --output /tmp/codex-check-exec.jsonl --repeats 3 --kinds pending check_success pending_pickup --trace
python scripts/codex_evaluate.py --transport app-server --output /tmp/codex-check-server.jsonl --repeats 3 --kinds pending check_success pending_pickup --trace
```

Original results remain in [50-case report](../codex_oauth_50/README.md).
