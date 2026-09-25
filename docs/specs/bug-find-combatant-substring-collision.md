# Spec: _find_combatant's substring matching can target the wrong enemy

## Changeset Tracking
- **main_v2 start**: origin/main_v2:0fb7113fa112c58aeefd32f1044c1f63bea43ad3
- **implementation end**: bug/find-combatant-substring-collision — ruff/mypy/compileall/pytest all green

## Purpose & Scope

Found during a full main_v2 code review, not from a specific production
incident.

`app/combat.py`'s `_find_combatant` (used by `apply_combat_damage`,
`damage_combatant`, `add_combat_effect`, `plan_enemy_turn`, and others —
every place that resolves "which combatant does this action target") did
bidirectional substring matching across every candidate's name/display_name/
ids, returning whichever combatant happened to appear first in
`state.combat.order` with ANY matching field:

```python
if norm and any(norm == _normalize(n) or norm in _normalize(n) or _normalize(n) in norm for n in names if n):
    return c
```

`find_live_enemy` (used by `add_npc_to_combat`'s duplicate guard) already
had this exact problem and was fixed to exact-match-only — its own
docstring explains why: "two distinct live enemies that happen to share a
substring (e.g. 'Cultist' and 'Cultist Leader'...) would falsely collide."
`_find_combatant` never got the same fix, and it's the function that
actually resolves damage/effect targets during combat — a real "深潛者" vs
"深潛者頭目" scenario (both alive at once — this pattern already exists in
real scenario NPC indexes) could silently apply damage to the wrong one,
with no error or warning at all.

## Fix

Two-pass lookup instead of a single mixed exact-or-substring pass:
1. Exact match (after normalization) against any of `name`/`display_name`/
   `combatant_id`/`enemy_card_id`/`character_id` — returned immediately
   when found.
2. Only if no exact match exists, fall back to substring matching — but
   only returns a result when exactly one combatant matches that way. An
   ambiguous partial name (matches more than one combatant) returns `None`
   (not-found) instead of guessing.

Substring matching isn't removed entirely (unlike `find_live_enemy`,
callers of `_find_combatant` do legitimately refer to combatants by a
partial/short name, e.g. an NPC's short name instead of its full registered
display name) — it's kept as a fallback, just no longer allowed to silently
win over an exact match elsewhere in the list, and no longer allowed to
pick an arbitrary winner among multiple ambiguous candidates.

## Testing Strategy

- `_find_combatant`-level unit tests: exact match wins even when another
  combatant would also substring-match; an ambiguous substring match (two
  candidates) returns `None`; an unambiguous substring match (one
  candidate) still resolves — preserving today's useful "refer to it by
  short name" behavior when it's actually safe.
- One end-to-end test through `apply_combat_damage` itself (not just the
  lookup helper) confirming damage lands on the intended "深潛者頭目", not
  the unrelated "深潛者".
- Pure application logic, no real-API verification needed.
- Standard four checks (ruff, mypy, compileall, pytest).

## Notes
- Related to (but a distinct bug from) `docs/specs/bug-add-npc-to-combat-
  duplicate-name-guard.md`, which covers `find_live_enemy`'s add-time
  duplicate guard — this spec covers the separate lookup function used at
  damage/effect-application time.
