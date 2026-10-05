# Keeping the scenario search from being starved, and counting Chinese text honestly without a tokenizer

[繁體中文](retrieval_budget_headroom_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `f03a110`.

## Problem

The scenario search is given `SCENARIO_CONTEXT_TOKEN_CEILING` (default 32,000) minus what the request already weighs, minus the output reserve and a safety margin, at most `SCENARIO_RETRIEVAL_TOKEN_BUDGET` (6,000). A search with no budget returns no usable evidence, the Keeper has nothing to decide on, and the player gets a generic "cannot continue" reply.

The Haunting 500-turn run (`b0e875c`) logged `rag.retrieval.budget` as 0 on 198 events, with a context estimate that had grown to about 68k, and 32 of its 500 turns (6.4%) ended in a generic reply. The cause in code, by arithmetic rather than measurement:

- The 57 tool schemas (about 13k tokens) and the static prompt (about 8k) are sent with every Executor request: roughly 21k tokens before any state or history. These sizes are my estimate (the sandbox had no tokenizer; the figure counts a Chinese character as 0.9 tokens and other text as a quarter of its characters), not a measured count.
- 32,000 minus about 21k minus 6,144 leaves under 5k for the dynamic prompt, the game history (for a non-OpenAI provider the whole log, up to 160 entries, is sent and counted) and the tool results of the turn. After a few dozen turns that is gone.
- Separately, when no tokenizer could be loaded the estimate fell back to UTF-8 bytes, which bills every Chinese character three times over (a character is about one token), so the same context looked about three times larger and the budget reached zero sooner. The tokenizer retry fix had already landed in the Haunting baseline, so this was not shown to be the cause there; it is the failure mode if `tiktoken` is missing or its cache is cold.

## Constraint: no new model call, no longer prompt

Nothing about what is sent to a provider changes. Only the arithmetic that decides how much scenario evidence a search may return, and the label it logs.

## Changes

- **`SCENARIO_RETRIEVAL_MIN_TOKENS`** (default 3,000, capped at `SCENARIO_RETRIEVAL_TOKEN_BUDGET`, `0` restores the old behaviour): the search never gets less than this. 3,000 is half the normal budget and the same as the proactive search cap. The `rag.retrieval.budget` event gains `budget_floor_applied` and `budget_before_floor` and is a WARNING whenever the floor was used, so an operator can see that the ceiling is too low.
- **`SCENARIO_CONTEXT_WINDOW_TOKENS`** (default 128,000, below the window of every supported provider): the hard limit. The floor may exceed the ceiling, which is only a planning number, but never the window: the budget is at most what is left of the window after the prompt and the output reserve (possibly zero), so the floor cannot make a request fail by being too large. The event gains `context_window` and `budget_capped_by_window`. Set it lower for a model with a smaller window.
- **The ceiling is the real fix and it is configuration, not code:** raise `SCENARIO_CONTEXT_TOKEN_CEILING` to the model's real context window minus headroom. The value is the operator's to choose (this project does not know a provider's window); `.env.example` and the configuration guide say so.
- **The fallback estimate understands Chinese** (`input_budget.fallback_tokens`): 1.5 tokens per CJK character (kana, hangul and full-width forms included) and one token per three other bytes, instead of one token per byte. Rare characters (extension A, jamo, compatibility ideographs, and everything outside the Basic Multilingual Plane: extension B and later, emoji) cost 3, and an identifier-like run of 20 or more characters containing a digit (hex ids, hashes, base64) costs one token per two characters. It leans high on ordinary text but is an estimate, not a bound: unusual text can still cost more, which the output reserve, the safety margin and the window above are there for. It is labelled `fallback_estimate` instead of `utf8_bytes_fallback` (the old name described what it no longer does). The estimate also prices evidence inside the search when the model is unknown, so required evidence that used to be refused for cost can fit.

## Not done

- Bounding the history a non-OpenAI provider is sent (the OpenAI path keeps 4,000 tokens) would shrink the prompt, but it changes what the Keeper sees and nothing here shows it makes a turn faster: across the 92 sequential Dead Boarder turns the median wait was flat (42 s in the first 20, 42 s in the last 12) while the log grew twentyfold.
- The tool surface (57 schemas every request) is the largest fixed cost and a latency lever of its own; it is left for the measured work.
- Nothing here was run against a real provider or a real log. To see whether it helps, compare `budget_floor_applied` and the share of turns ending in a fallback (`scripts/summarize_turn_log.py`) before and after on the same scenario.

## Verification

`tests/test_retrieval_budget_floor.py` (the estimate per script and mixed text, never below one token per character, plain `estimate` through the fallback; the budget with room, with some room, with a full context, with the floor off, with the floor above the budget, and with only a higher ceiling) and the updated `tests/test_retrieval_readiness.py` (numbers that pinned the byte count now pin the new estimate; a new case keeps the "metadata shrinks to a tight budget" guarantee). `ruff check .`, `mypy app` and the full `pytest` pass.
