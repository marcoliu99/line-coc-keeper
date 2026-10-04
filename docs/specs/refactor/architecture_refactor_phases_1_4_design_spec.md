# Architecture refactor, phases 1–4

[繁體中文](architecture_refactor_phases_1_4_design_spec_zh.md) | [Docs index](../../README.md)

## Status and goal

Category: `refactor`. Status: **partial** — phase 1 is implemented; phases 2–4 land as stacked pull requests. Based on `main_v2` at `2affd06` (2026-10-04). The Traditional Chinese edition is the full requirements document this summary is drawn from; where the two differ, treat the Chinese edition as the source of intent and this page as the index to what is built.

Principle: **the AI understands intent, chooses legal operations and narrates; the engine owns dice, resource changes, the check lifecycle and consistency.** No extra model call, narrator service or reasoning round is introduced.

## Phases

| Phase | Goal | Spec / report | Status |
| --- | --- | --- | --- |
| 1 | One transaction boundary for every game-state write (lock, reload, timeline, action ledger, revision, atomic commit) | [state transaction](state_transaction_design_spec.md), [report](../../refactor/phase1-result.md) | implemented |
| 2 | One check engine and lifecycle (command, button and tool share rules; Luck and pending; consequences) | report in `docs/refactor/phase2-result.md` | planned |
| 3 | One combat engine; remove the `combat` ↔ `combat_flow` cycle; keep legacy and managed modes | report in `docs/refactor/phase3-result.md` | planned |
| 4 | Retire `legacy_commands`; move the remaining duties to their owners | report in `docs/refactor/phase4-result.md` | planned |

## Invariants every phase keeps

1. All state writes go through the same transaction boundary; an old snapshot never overwrites newer state.
2. A retried, resent or re-run action never rerolls, double-deducts HP, SAN, Luck or ammo, or advances a turn twice.
3. The same kind of check shares one lifecycle and rule decision whether it arrives from a command, a button or a tool.
4. Deterministic steps that need no player input settle in one place; a player choice saves a pending entry and returns.
5. `legacy_commands.py` is deleted, not renamed into another large module.
6. Existing games, saves, commands, buttons and tool schemas keep working; new fields are optional on read.
7. Not touched: OCR, layout, numeric validation, fallback order, scenario import thresholds, map extraction, provider selection, the supervisor / turn pipeline, Discord cleanup beyond wiring.

Policies kept as-is: Traditional Chinese and the Keeper voice, spoiler boundaries, autoroll default off, the player roll / defend / Luck choices, and no Luck on SAN or combat checks.

## Verification approach

Characterisation tests before changes; scripted-dice domain tests; storage integration on real SQLite (concurrency, rollback, restart, deduplication); adapter contracts through public entry points; an offline five-player Chinese replay for the whole chain. An AST architecture gate keeps the dependency direction (commands / buttons / tools → check and combat services → state transaction → repository). Offline results are not reported as live Discord or provider verification.
