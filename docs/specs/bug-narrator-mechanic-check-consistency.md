# Spec: keep Narrator check instructions consistent with Executor tools

## Changeset Tracking

- **main_v2 start**: origin/main_v2:0fb7113fa112c58aeefd32f1044c1f63bea43ad3
- **implementation end**: fix/narrator-obeys-mechanic-facts — full pytest, Ruff, mypy, and compileall pass

## Problem

Production logs from 2026-09-25 show two related failures during attempts to
pry open a door:

1. The first Executor turn only called `search_scenario`. Narrator told the
   player a STR check was needed and directed them to `/coc check`, despite
   no pending check being registered. The subsequent command correctly found
   none.
2. On the repeated attempt, `skill_check` returned `pending=True` and saved
   the pending STR check. Narrator then said the check had not been created.

`MechanicResult` already reaches Narrator, but its free-form tool summaries
are not an enforceable status contract. The player-facing response can
therefore contradict the actual check state.

## Goal and scope

Make the mechanic check status explicit in the Narrator context, then validate
and safely correct player-facing check instructions against that status:

- If a `skill_check` tool succeeded with `pending=True`, the reply must not
  claim that no check exists and must tell the player how to resolve the
  registered check.
- If this turn did not register a pending `skill_check`, the reply must not
  instruct the player to roll or use `/coc check` for this turn.

Keep Executor's tool execution and persistence authoritative. Narrator remains
tool-free and writes scene prose only.

## Non-goals

- Do not change when the Executor decides a check is needed, the dice rules,
  `autoroll_checks`, or `/coc check` resolution behavior.
- Do not address unrelated prose accuracy, check difficulty selection, or
  stale checks from earlier turns.
- Do not add another LLM request to repair narration.

## Design

1. Derive a small structured check-status value from actual check-tool
   results, then reconcile it with the post-Executor persisted state snapshot
   for the active player. Pass the final status alongside `MechanicResult`.
2. Include explicit conditional instructions in the Narrator's mechanic
   facts block: registered pending checks are real and must be presented as
   registered; without one, do not direct the player to roll or call
   `/coc check`.
3. Add a deterministic response check for the two prohibited contradictions.
   If Narrator violates the status contract, return a short safe correction
   based on the structured tool result, without another provider call. Keep
   ordinary narration unchanged when it is consistent.

The structured status must distinguish `pending=True` from an autorolled
result, failed tool calls, and turns with no `skill_check` call. It must be
produced at the tool gateway boundary from the tool's actual return value.

## Tests

- Unit tests for status derivation across pending, resolved, failed, and
  absent `skill_check` calls.
- Prompt-context tests for the pending and absent-check instructions.
- Response validation tests for accepting consistent narration and replacing
  each of the two contradictory outputs.
- Supervisor/Narrator pipeline test proving that the structured result reaches
  the final response validation path.
- Run relevant tests, full pytest, Ruff, mypy, and compileall.

## Tradeoffs

The deterministic correction may be less atmospheric than Narrator's prose
when it detects a contradiction. That is preferable to telling a player to
resolve a nonexistent check or denying one that has already been saved. The
validator should be narrowly scoped to explicit check-status claims and roll
instructions to avoid rewriting unrelated prose.
