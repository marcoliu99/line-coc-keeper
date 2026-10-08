# Laya shadow routing: log a fast router's guess beside the Executor

## Problem

A 100-turn Haunting replay (2026-10-09) spent a median 23.6 s per turn, 19.8 s of it in the Executor and 4.6 s in the Narrator. Nearly every player line goes to the Executor: `intent_router` sends only bare confirmations and parenthesised OOC lines to the Narrator alone. Lowering the Executor's reasoning effort was tried before and made its tool choices unreliable, and narration stays at medium.

## Change

[Laya](https://github.com/receptron/laya) answers typed questions (choice, score, yes/no) about a state in one encoder pass, about 140 ms warm on CPU. Before any line is routed on it, its accuracy is measured on real play:

- `app/agents/laya_shadow.py`: when `LAYA_SHADOW_URL` is set, each player line (`player_action`, not the KP assistant) is posted to a local Laya sidecar as `{玩家行動, 角色, 戰鬥中}` beside the Executor, and the answer (route, probabilities, the yes/no "needs mechanics", latency, the line itself) is logged as a `laya.shadow` event under the turn's id. The turn never waits for it and never reads it; a down or slow sidecar logs `status: error`. Off by default.
- `scripts/experiments/laya_router_eval/`: `server.mjs` (the sidecar), `questions.mjs` (the questions, shared), `eval.mjs` (scores the guesses against the tools the Executor called in the same turn: a turn that called anything besides look-ups needed the Executor; refused turns are not scored) and a README runbook.

## Not done

Routing on the guess. That needs a threshold whose "fast path but the Executor did act" rate is near zero on a real run, which this experiment is for.

## Tests

`tests/test_laya_shadow.py`: off by default, the guess is logged with its line, a down sidecar is a logged miss.
