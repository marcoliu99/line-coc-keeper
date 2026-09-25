# Spec: adjust_character reports a false "CON check created" message when one already exists

## Changeset Tracking
- **main_v2 start**: origin/main_v2:0fb7113fa112c58aeefd32f1044c1f63bea43ad3
- **implementation end**: bug/adjust-character-false-major-wound-check — ruff/mypy/compileall/pytest all green

## Purpose & Scope

Found during a full main_v2 code review (cross-validated by two independent
reviewers), not from a specific production incident.

`app/keeper.py`'s `adjust_character` tool handler (`_apply_attribute_delta`,
~line 2417-2463) sets `major_wound = True` unconditionally whenever a single
HP-reducing delta crosses the major-wound threshold (≥ half max HP), before
checking whether a pending check actually gets registered:

```python
if field_name == "hp" and delta < 0 and new_val > 0 and -delta >= target_char.hp_max / 2:
    major_wound = True                      # set regardless of what happens below
    con_value = resolve_skill_value(target_char, "CON")
    if target_state.autoroll_checks:
        ...
    elif target_char.owner_id not in target_state.pending_checks:
        target_state.pending_checks[target_char.owner_id] = {...}   # only this branch actually writes
```

When the character already has an unrelated pending check (non-autoroll
mode), the `elif` branch is skipped — nothing gets written to
`pending_checks` — but `major_wound` is still `True`, so the tool result
still includes `"note": "...已替玩家建立待處理的 CON 檢定，請等待玩家輸入
/coc check CON"`. The Keeper tells the player a CON check now exists; it
doesn't. The player's `/coc check` has nothing to resolve (the original,
unrelated pending check is still sitting there instead).

`app/combat.py`'s `_resolve_major_wound_check` (the equivalent logic for
combat damage) already handles this correctly: it returns `None` when
`pc.owner_id in state.pending_checks` in non-autoroll mode, so the caller's
`major_wound_triggered` correctly comes back `False` — no false claim.
`adjust_character`'s version never got the same fix.

## Root cause

`major_wound = True` is set at the top of the threshold-check block instead
of only when a branch actually does something (auto-resolves or registers a
new pending check).

## Fix

Move `major_wound = True` into each branch that actually acts (the
`autoroll_checks` branch and the "no existing pending check" `elif`
branch), matching `combat.py`'s pattern. When neither branch runs (an
unrelated pending check already exists), `major_wound` stays `False` and
the response omits `major_wound`/`major_wound_check`/the misleading `note`
entirely — the HP change itself still applies and saves correctly; only the
false claim is removed.

## Testing Strategy

- New regression test in `tests/test_npc_attack_latency.py`: character with
  an existing unrelated pending check takes major-wound-threshold damage
  via `adjust_character` — asserts `major_wound` is absent from the result,
  no misleading `note`, the pre-existing pending check is untouched, and
  the HP change itself still applied.
- Existing `test_adjust_character_major_wound_defaults_to_player_pending`
  (the no-existing-pending-check happy path) must keep passing unchanged.
- Pure application logic, no real-API verification needed — this is a
  deterministic branch-selection bug, not a model-behavior question.
- Standard four checks (ruff, mypy, compileall, pytest).

## Notes
- Found via a full-codebase review requested by the user; not tied to a
  specific reported production incident, but the failure mode (player told
  a check exists that doesn't) is the same class of confusion as
  `docs/specs/bug-self-corrected-check-leaves-stale-pending.md`, just
  triggered by different code.
