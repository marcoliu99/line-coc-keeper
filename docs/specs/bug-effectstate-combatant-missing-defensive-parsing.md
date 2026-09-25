# Spec: EffectState/Combatant lack the defensive parsing ArmorRule/AttackRule/SpecialAbility already have

## Changeset Tracking
- **main_v2 start**: origin/main_v2:0fb7113fa112c58aeefd32f1044c1f63bea43ad3
- **implementation end**: bug/effectstate-combatant-missing-defensive-parsing — ruff/mypy/compileall/pytest all green

## Purpose & Scope

Found during a full main_v2 code review, not from a specific production
incident.

`docs/specs/bug-add-npc-to-combat-armor-schema-crash.md` gave `ArmorRule`/
`AttackRule`/`SpecialAbility` a `_known_fields_only` backstop (drop any
unrecognized key instead of crashing on `TypeError`) plus safe defaults for
their required fields, after a real production crash from the LLM using an
unexpected key. `EffectState` and `Combatant` are built the same way (both
have LLM-controlled construction paths — `add_combat_effect` for
`EffectState`, `combat.add_npc`/`start_combat` for `Combatant` — and both
have plain `Cls(**data)` unfiltered `from_dict` implementations) but never
got the equivalent fix.

**This is a wider blast radius than the original armor bug**: both
`EffectState` and `Combatant` are deserialized from `CombatState.from_dict`
on *every* `load_state` call for a group with a surviving combat
effect/combatant — not just once per tool call. If any future change ever
renames or removes a field on either dataclass (exactly the scenario
`_known_fields_only` exists to guard against), every group with that kind
of data persisted would fail to load at all, not just fail one tool call
the way the original armor incident did.

## Fix

- `EffectState`: give `id`/`label` safe defaults (matching `ArmorRule`'s
  pattern — `id` via `_generate_sub_id`, `label` defaulting to `""`), and
  route `from_dict` through `_known_fields_only`.
- `Combatant`: give `name`/`dex`/`hp`/`hp_max` (previously all required,
  no-default fields) safe defaults, and route `from_dict` through
  `_known_fields_only`.

Both reuse the exact same helper (`_known_fields_only`) already defined in
`app/models.py` for the armor-schema fix — no new logic, just applying the
established pattern consistently.

## Testing Strategy

- Direct unit tests on both dataclasses' `from_dict`: an unexpected key
  is silently dropped instead of raising; missing required-in-the-old-
  schema fields (`id`/`label` for `EffectState`, `name`/`dex`/`hp`/
  `hp_max` for `Combatant`) fall back to their new safe defaults instead
  of raising.
- Full existing test suite must keep passing unchanged — giving
  `Combatant`'s core fields defaults must not silently change any
  existing construction call's behavior (all existing callers already
  pass these explicitly).
- Pure application logic, no real-API verification needed.
- Standard four checks (ruff, mypy, compileall, pytest).
