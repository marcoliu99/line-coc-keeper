# Player-facing presentation: tiers, internal ids, party size

[繁體中文](player_facing_presentation_design_spec_zh.md) | [Docs index](../../README.md)

## Status and goal

Category: `bug`. Status: **implemented**. Source: findings CS-001, CS-003 and CS-004 of the 5-player / 500-turn Camp Sunny validation (Workstream F). Based on `main_v2` at `aa79f22`.

* CS-001: five investigators were narrated as six, taken from the scenario's pre-generated sheets or prose.
* CS-003: raw English result tiers (`regular 成功`, `hard`, `fumble`) reached players.
* CS-004: an internal `check_id` appeared in Keeper replies.

The engine keeps English tier names and opaque ids on purpose: saved events, logs and the phase-2 golden traces depend on them (`outcome` is stored as `hard 成功`). So the stored form is unchanged and the fix is a presentation layer at the point where a player reads.

## Contract

1. **A presentation layer** (`app/presentation.py`). `tier_label`, `difficulty_label` and `outcome_label` map engine values to the table's language (`regular 成功` → `一般成功`, `hard` difficulty → `困難`, a hard tier that failed a harder task → `失敗（擲出困難成功）`). Unknown values pass through.
2. **Display sites use it.** The public line for a settled check, the resolved-check fallbacks and outcome block, the history and pending-Luck text the Narrator is given, and the Narrator's failure message now show localised tiers and difficulties, so the model retells Chinese rather than copying English.
3. **A last line of defence.** The supervisor passes the final reply and every private message through `presentation.player_text`: `outcome 成功/失敗` and labelled `難度/等級` values are mapped, and `check_id=…`, `decision_id=…` and bare `check-<hex>`/`decision-<hex>` are removed with their brackets, so a value a model copied out of its context is not shown. Ordinary prose containing "regular" or "hard" is left alone. `DEBUG_SHOW_INTERNAL_IDS=true` keeps ids visible for debugging; tier names are never shown raw.
4. **Ids are not handed to the Narrator.** The one-line tool facts no longer include `check_id`, `decision_id`, `timeline_id`, `event_id` or `evidence_ref`; the structured payloads, logs and tests keep them.
5. **The real party size.** The Narrator's prompt states the number and names of the active investigators and forbids another figure, and the final reply is corrected when it claims a larger party ("你們六位調查員" with five → "五位"). Only a number above the real count is changed, because a smaller one may be a subgroup ("兩位調查員留下").

## Enforcement

`tests/test_presentation.py`: each tier and failure form, labelled values, prose left alone, idempotence; each id form removed with the sentence kept; the debug switch; the facts and prompts handed to the Narrator; the public check line and fallbacks; the final reply and private messages cleaned; party corrections, subgroups and unrelated numbers left alone; five investigators never narrated as six; and the Narrator prompt carrying the real roster.

## Not covered

The regexes handle the forms seen in the validation (`regular 成功`, `難度 hard`, `check_id=…`). A tier or id written in some other shape, or a party size spelled in an unusual way ("半打"), is not caught. The `/coc luck <tier>` command text still shows the English option keyword because it is the argument the command accepts. Party size is the count of bound investigators; an NPC teammate played by the Keeper is not counted.
