# An empty internal id label must not reach a player, and removing one must not eat the sentence

[繁體中文](internal_id_display_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `7782690`.

## Problem

The Dead Boarder 200-turn run with `NARRATION_OUTSIDE_MUTATION_LOCK=true` (`1fd3ccb`) shows an empty `check_id` field in pending-check replies (turns 40, 48, 100). `presentation.player_text` removes `check_id=<id>` and the other internal id labels, but its pattern required at least one character after the label, so `（check_id=）` was left exactly as written. Reproduced against `main_v2`: `檢定已建立（check_id=）請擲骰` came back unchanged.

Reading the same pattern for the opposite failure turned up a second defect: the value class was `\w`, which matches Chinese characters, so `check_id: 請擲骰` (no id, no space) was removed whole, text included.

## Changes

- The label is removed with whatever value follows it, **including none**. `（check_id=）請擲骰` becomes `請擲骰`.
- The value is limited to ASCII (`A-Za-z0-9_.:-`), which is what every id the engine writes is made of. It stops at the first non-ASCII character, so `check_id: 請擲骰` becomes `請擲骰` and `check_id=check-<hex>請擲骰` becomes `請擲骰`.
- Whitespace after the separator, and before a closing bracket, is limited to spaces and tabs. An empty label at the end of a line does not reach across the line break and take the first word of the next line (`event_id=` + newline + `See below` keeps `See below`).
- `DEBUG_SHOW_INTERNAL_IDS` still shows everything unchanged.

Nothing else about the mapping changes: the bare-id pattern, the tier-name mapping and the idempotence of `player_text` are as before.

## Not done

- Where the empty label comes from. The pending-check reply is written by the Narrator or the Keeper text from tool output; an empty `check_id` means the field was present without a value, which this spec does not trace. The display is fixed at the one place every reply passes through; a producer that writes an empty field is still worth finding.
- Collapsing the double space that removing a bracketed label can leave between two spaced words. Existing behaviour, cosmetic.

## Verification

`tests/test_presentation.py`: an empty label in brackets, after a colon, at the end of a sentence and at the end of a line; an id followed directly by Chinese text; a bracketed empty label leaves no label or empty brackets; the empty label is still shown when debugging. `ruff check .` and the full `pytest` pass.
