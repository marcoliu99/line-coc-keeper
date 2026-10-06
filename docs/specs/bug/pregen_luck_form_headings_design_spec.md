# A Luck printed under column headings is kept, not dropped

[繁體中文](pregen_luck_form_headings_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `6f7a6d9`.

## Problem

Loading *The Haunting Scenario trimmed* listed all four pregenerated investigators with "LUCK 空白，選用後由玩家擲骰", although the printed sheet shows `Luck 50`. The bot log said why, for each of them:

```text
dropping unverified PDF pregen Luck for 未填: excerpt_does_not_state_the_value
```

The Chaosium "1920s Era Investigator" form prints small column headings, `Starting` and `Current`, between the `Luck` label and the box that holds the value, and the sheet pages are images, so the text comes from OCR in reading order. A verbatim quote of the Luck box is therefore `Luck Starting Current 50` or `Luck Starting 50 Current`. The verification (`pregen_extractor._check_pdf_luck`) accepts a quote only when the label is followed directly by the number, so these quotes were rejected and the correct value was thrown away. The Dead Boarder load at 01:22 dropped its Luck for the same reason.

The guard itself is right: a Luck the model reports must be quoted from the sheet and belong to that investigator, otherwise the player rolls it (`pregen_luck_roll_design_spec.md`). Only the quote pattern was too narrow.

## Change

- A quote states the Luck when the reported value appears after the `Luck`/`幸運` label, whatever sits in between: the form's column headings (`Starting`, `Current`), `|`, dashes or colons. The earlier pattern (#213) listed the headings and separators it knew, and each new page layout needed another entry: the second Haunting load quoted `Luck | Starting: 50`, `Luck — Starting | 60` and `Luck | 55`. The quote must still appear on the cited page and still belong to this investigator by the sheet's own characteristics, the nearest name, or being the only investigator.
- A quote that has the headings but no number is still dropped.
- The warning that reports a dropped Luck now includes the value the model reported, the cited page and the first 160 characters of its excerpt, so the next mismatch can be read from the log instead of guessed.

## Tests

`tests/test_pregen_luck_verification.py`: a quote with both headings, with one heading and the value before the second, with Chinese headings, a quote with headings and no number still dropped, and the warning text showing the reported value and excerpt. The existing attribution cases are unchanged.
