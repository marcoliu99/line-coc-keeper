# Codex OAuth: 50-case evaluation

Date: 2026-09-28. Model: `gpt-6-luna`. CLI: `0.157.1`.
Authentication: existing ChatGPT login; no API-key conversation calls.
Base: `main_v2` c340984 plus the isolated Codex provider implementation.

## Method

Two separate processes ran 25 synthetic cases each (five kinds, five repetitions).
Both ran the real Supervisor, Executor/Narrator, game tool gateway and temporary
SQLite persistence. Each check case includes a player action, deterministic Python
check submission (RNG fixed for reproduction), and follow-up narration. Thus 50
scenario cases are not 50 model requests or exactly 50 individual chat messages.
No production game data or Discord sends were used. Analysis/embedding API keys
were cleared in the evaluation processes. The processes shared OAuth capacity.

Exec ignored user config and used the model's medium reasoning default; the
app-server inherited medium. Both were confirmed against local model metadata
and the relevant nonsecret config value. Final code now explicitly pins medium.
No hidden CLI HTTP retries are counted: requests below mean host decision requests.

| Metric | exec | app-server |
| --- | ---: | ---: |
| Cases | 25 | 25 |
| State/narrative assertions passed | 24 (96%) | 23 (92%) |
| Strict tool selection/arguments passed | 22 (88%) | 20 (80%) |
| Whole-case median | 24.681 s | 23.424 s |
| Whole-case p95 (nearest rank) | 34.742 s | 34.836 s |
| Median among successful cases | 24.528 s | 24.855 s |
| Host decision requests | 71 | 71 |
| Game tool calls | 18 | 18 |
| Successful game tool receipts | 15 | 14 |
| Python player rolls | 10 | 9 |
| Prompt + output-schema bytes | 3,425,358 | 3,420,930 |

Across the 23 matched pairs where both completed successfully, the median
app-server minus exec time was **+0.187 seconds**. This small experiment does not
show a speed advantage for app-server. Different model decisions and shared
capacity also affect timing. Keep `CODEX_TRANSPORT=exec` as the experiment default;
app-server remains available with the same game interface.

## Failures and limitations

- Exec attempted to recreate an existing pending check in repetitions 2, 4 and 5.
  Python rejected all three attempts without replacing the pending check or rolling
  dice. Repetition 5 ended with the existing incomplete-action fallback; the other
  two recovered into a waiting instruction. They still fail strict tool correctness.
- App-server made the same redundant pending-check attempt in repetitions 1, 3, 4
  and 5. Python rejected all four without changing pending state or rolling dice.
  Repetition 5 ended with the incomplete-action fallback.
- App-server repetition 2 / check_success never called the required check tool and
  returned an incomplete-action fallback. The expected pending check was absent,
  so that case never proceeded to a player roll. The saved report does not include
  the original model decision; its exact prompting cause is not established.
- All reached player-check resolutions used one Python roll each. Successful
  checks revealed the fixture clue; failed checks did not. No failed-check clue
  disclosure, pending replacement, or reroll was observed by these assertions.
- Strict tool accuracy audits actual tool names, target investigator, skill,
  difficulty, bonus/penalty/push flags and successful receipts. OOC/pending cases
  must not attempt new mutation tools. This is not a general proof of CoC correctness.
- These simple fixtures do not evaluate combat, long campaigns, large PDFs/RAG,
  private Discord delivery, broad narrative fidelity or account-wide rate limits.
- After the live batch, output-tail draining was hardened and reasoning was pinned
  explicitly to the already-used medium value. The final transport regression suite
  passed; a second 50-case live batch was not run for those changes.

## Verification and artifacts

- Full suite: 1,305 passed, 1 skipped, 45 subtests passed.
- Final Codex-specific suite after output-tail/explicit-reasoning changes:
  31 passed, 4 subtests passed.
- Ruff and mypy passed; whitespace checks passed.
- [Exec raw cases](exec.jsonl), [app-server raw cases](app-server.jsonl),
  [machine-readable summary](summary.json).
- Reproduce with `scripts/codex_evaluate.py`; audit with
  `scripts/summarize_codex_evaluation.py`. See [guide](../../guides/codex_oauth_testing.md).

Both transports are implemented and functionally usable for this isolated
experiment. The model decision failures remain visible; the experiment is not
claimed to have achieved 100% tool accuracy. No PR or merge was performed.
