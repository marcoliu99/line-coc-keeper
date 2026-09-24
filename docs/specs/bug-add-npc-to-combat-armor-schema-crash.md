# Spec: add_npc_to_combat crashes on armor entries with an unexpected key

## Changeset Tracking
- **main_v2 start**: origin/main_v2:80f245fc6591a021b40dbbedb9d44d41e6b9ec9f
- **implementation end**: TBD

## Purpose & Scope

Real production log evidence (`/Users/marcoliu/profile-async2.log`,
2026-09-24 16:01:46-16:02:23, `gpt-6-luna`/`none` Executor turn during a
mid-combat evacuation scene): `add_npc_to_combat` failed outright —

```
add_npc_to_combat 失敗：ArmorRule.__init__() got an unexpected keyword argument 'name'
```

The model passed an `armor` entry with a `name` key, but `ArmorRule`'s
real fields (`app/models.py:408-421`) are `id`/`label`/`value`/
`applies_to`/`bypass_tags`/`public_hint` — `name` isn't one of them, and
`ArmorRule.from_dict` does an unfiltered `ArmorRule(**data)`, so any
unexpected key raises `TypeError` immediately. The enemy was never
added; combat was left with only the two investigators (Ken, Marco) and
nothing to fight. The rest of that turn (6 real, correctly-cycling
`advance_combat_turn` calls between the two of them) was a downstream
symptom of this — not a bug in `advance_combat_turn` itself (see
`docs/specs/bug-advance-combat-turn-skips-pending-action.md`, which
misdiagnosed this same incident before this was found, and has been
reverted/corrected).

## Root cause

`add_npc_to_combat`'s `armor` parameter schema (`app/keeper.py`,
`TOOLS`) documents no field names at all:

```python
"armor": {
    "type": "array",
    "description": "敵人護甲規則；玩家未發現前不要公開具體數字",
    "items": {"type": "object"},
},
```

Compare to its siblings in the same tool call, which do:

```python
"attacks": {..., "description": "敵人攻擊表，每筆含 id/label/skill_name/skill_value/damage/range_band 等。..."},
"abilities": {..., "description": "敵人特殊能力，每筆含 id/name/priority/trigger/check/effect/usage/reveal_policy 等"},
```

The model had no way to know `armor` needed `id`/`label` rather than
`name` — and `abilities`' own real field is literally called `name`
(`SpecialAbility.name`, `app/models.py:446-463`), giving a plausible
reason to guess `name` would work for `armor` too, by analogy within the
very same tool call.

**This is a systemic pattern, not armor-specific**: `_coerce_armor`,
`_coerce_attacks`, and `_coerce_abilities` (`app/combat.py:98-108`) all
do the same unfiltered `SomeDataclass(**data)` construction with zero
tolerance for unexpected keys, via `ArmorRule.from_dict`/`AttackRule.
from_dict`/`SpecialAbility.from_dict` (`app/models.py`). `attacks`/
`abilities` are less exposed only because their descriptions happen to
list real field names — the same crash is reachable through either of
them too if the model passes any unexpected key.

Also in scope (per explicit instruction — not deferred): `SpecialAbility.
trigger`/`check`/`effect`/`usage`/`reveal_policy` are typed as
`dict[str, Any]`, but nothing validates the model actually provides a
dict — a string value (a very plausible LLM mistake, e.g. `"trigger":
"任何時候"` instead of a structured trigger object) would construct
successfully (Python doesn't enforce dataclass type hints at runtime)
and then crash later in unrelated consuming code. Confirmed every real
consumer of these five fields (`app/combat.py`, grepped all `.trigger`/
`.check`/`.effect`/`.usage`/`.reveal_policy` accesses) reads them via
`(ability.X or {}).get(...)` expecting a dict with specific known
sub-keys (`type`, `on_success`, etc.) — a plain string could never
satisfy any of those lookups meaningfully even if preserved verbatim, so
the correct fix is the same as what already happens when the field is
simply omitted: coerce a non-dict value to `{}` rather than trying to
wrap it into a guessed key nothing downstream would read anyway.

## Changes

Two independent, complementary fixes — both land together, since they
address different failure points of the same underlying issue:

1. **Schema documentation** (prevents the model from guessing wrong in
   the first place): add explicit field-name documentation to `armor`'s
   description in `app/keeper.py`, matching the style already used for
   `attacks`/`abilities` — e.g. "敵人護甲規則，每筆含 id/label/value/
   applies_to/bypass_tags/public_hint 等；玩家未發現前不要公開具體數字
   （public_hint 可用中性描述）".
2. **Defensive parsing** (prevents a crash even if a bad key still gets
   through — a different field, a future model quirk, a KP-Assistant
   typo via some other path): make `ArmorRule.from_dict`, `AttackRule.
   from_dict`, and `SpecialAbility.from_dict` (`app/models.py`) filter
   the incoming dict to only known dataclass field names before
   constructing, instead of an unfiltered `**data` unpack — silently
   dropping unrecognized keys rather than raising. Applying to all three
   (not just `ArmorRule`) since they share the identical fragile pattern
   and the identical fix.
3. **`SpecialAbility`'s dict-typed fields**: in the same `from_dict`,
   coerce `trigger`/`check`/`effect`/`usage`/`reveal_policy` to `{}`
   whenever the incoming value isn't already a `dict` — matching what
   already happens when the model omits the field entirely, and
   preventing the delayed downstream crash a wrong-typed string value
   would otherwise cause the first time consuming code calls `.get()` on
   it.

## Testing Strategy

- **Defensive parsing**: straightforward unit tests — `ArmorRule.
  from_dict({"id": "x", "label": "y", "name": "z"})` (extra unknown key)
  should construct successfully and silently ignore `name`, not raise;
  same pattern for `AttackRule`/`SpecialAbility`. This is pure
  application-logic correctness, fully testable without any real-API
  call.
- **Schema documentation**: real-API verification (per this project's
  established practice for prompt/description changes) — reconstruct
  the real incident (mid-combat, needing to add an enemy with armor) and
  check whether the model reliably uses `id`/`label` instead of `name`
  once the description documents the real fields, across enough trials
  to say something meaningful (this project's tiering spec's own bar is
  N≥3-5 per condition, given documented high run-to-run variance).
- Regression test confirming `add_npc_to_combat`'s tool handler
  (`app/keeper.py`'s `_execute_tool`) no longer surfaces a raw Python
  `TypeError` message as the tool result when given a plausible-but-wrong
  armor key — either it succeeds (ignoring the extra key) or, if some
  other genuinely invalid input is given, returns a normal `{"ok": False,
  "error": "..."}` shape consistent with this project's other tool
  handlers, not an uncaught exception's `str()`.
- Standard four checks (ruff, mypy, compileall, pytest).

## Notes
- Supersedes `docs/specs/bug-advance-combat-turn-skips-pending-action.md`
  as the real fix for the production incident both specs were built from
  — that spec's original diagnosis and PR (#62) have been corrected/
  closed; see that spec's own "Correction" section for the full story of
  how the misdiagnosis happened (a synthetic-mock verification flaw) and
  was caught (a code-review comment).
- This is a real application-logic bug (a crash + a tool-schema gap), not
  a "the model is unreliable" issue like `docs/specs/bug-continuing-
  damage-rolls-corrupt-luck-stat.md`'s reasoning-effort finding — the
  defensive-parsing half of this fix is unconditionally correct
  regardless of model choice/reasoning effort, and doesn't need real-API
  verification to justify landing it.
