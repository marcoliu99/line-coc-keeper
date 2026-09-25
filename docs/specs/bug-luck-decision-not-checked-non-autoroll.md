# Spec: non-autoroll skill_check/sanity_check never check pending_luck_decisions

## Changeset Tracking
- **main_v2 start**: origin/main_v2:0fb7113fa112c58aeefd32f1044c1f63bea43ad3
- **implementation end**: bug/luck-decision-not-checked-non-autoroll — ruff/mypy/compileall/pytest all green

## Purpose & Scope

Found during a full main_v2 code review, not from a specific production
incident.

`pending_luck_decisions` (a character mid-decision on whether to spend Luck
to improve a just-rolled check) is only ever written to in `autoroll_checks
=True` mode. The **autoroll** branches of `skill_check`/`sanity_check`
already guard against registering a new check while one is still pending
(`app/keeper.py`, checked right before rolling). The **non-autoroll**
branches never did:

- `skill_check`'s non-autoroll branch (~line 1901-1957) only checked
  `target_state.pending_checks` before registering a new pending check.
- `sanity_check`'s non-autoroll path goes through the shared
  `_reject_if_check_already_pending` helper (~line 993), which also only
  checked `pending_checks`.

**Concrete scenario**: a group has `autoroll_checks=True`, a check resolves
with Luck buy-up options, leaving a `pending_luck_decisions` entry for the
character — then someone runs `/coc autoroll off` before the player picks
an option (any player can toggle this per the static prompt's own
documented rule). The Keeper calls `skill_check` again for the same
character; it now goes through the non-autoroll branch, sees an empty
`pending_checks`, and happily registers a brand new pending check. The
character now has two independent, unrelated "waiting for player input"
states at once — a stale Luck decision nobody resolved, and a fresh skill
check — and `/coc check`'s resolution logic was never built to handle a
character being in both states simultaneously.

## Fix

Add the same `pending_luck_decisions` guard the autoroll branches already
have, to both non-autoroll paths:
- `skill_check`'s non-autoroll branch, right after building the candidate
  check dict and before checking for an existing `pending_checks` entry.
- `_reject_if_check_already_pending` (the shared helper `sanity_check`'s
  non-autoroll path uses) — fixing it here also covers any other caller of
  this helper for free.

Both return the same error shape/wording the autoroll branch already uses:
`f"{char.name} 仍在等待 Luck 決定，請先處理 Luck 選項。"`

## Testing Strategy

- `test_skill_check_rejects_when_a_luck_decision_is_still_pending` and
  `test_sanity_check_rejects_when_a_luck_decision_is_still_pending`
  (`tests/test_npc_attack_latency.py`): a character with a
  `pending_luck_decisions` entry set (independent of how it got there)
  gets a rejected result with the Luck-decision error, not a silently
  registered new check.
- Existing non-autoroll happy-path tests (no pending luck decision) must
  keep passing unchanged.
- Pure application logic, no real-API verification needed.
- Standard four checks (ruff, mypy, compileall, pytest).
