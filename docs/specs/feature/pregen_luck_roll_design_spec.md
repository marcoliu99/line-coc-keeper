# Pregenerated-character Luck ownership

[繁體中文](pregen_luck_roll_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `feature`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Use a verified Luck value on the final merged role card. A blank Luck field stays blank and the owning player rolls through /coc luck roll during preparation.

2. Do not fill blank Luck through AI repair, a default constructor value or another player’s record. Pending pregen Luck blocks scenario switching/start as applicable.

3. A Luck value printed on a PDF sheet counts as verified only when the model cites its page and quotes the label with the value, the quote states that value, and it belongs to this investigator. Each occurrence of the quote is attributed by the first rule that decides: (a) the sheet's own characteristics printed near it (STR/CON/SIZ/DEX/APP/INT/POW/EDU or the Chinese labels, at least four, strictly more than any other pregen) identify the sheet whatever the order or spelling of its name; two sheets close enough that both are in reach are ambiguous and not guessed; (b) the nearest investigator name before it (on that page, or at the end of the page before) is this pregen's; (c) this is the only investigator and the only occurrence. The Luck is kept when exactly one occurrence belongs to this pregen. Anything else is dropped, with the reason logged, and the player rolls. `tests/test_pregen_luck_verification.py` lists the kept and dropped cases.

4. Normalize supported skill aliases without replacing arbitrary custom skill names. Character names and translated descriptions do not alone prove identity.

## Flow and interfaces

```text
Import/claim -> verified Luck present? -> use value / owner rolls -> ready
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/pregen_extractor.py](../../../app/pregen_extractor.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [app/skill_aliases.py](../../../app/skill_aliases.py)
- [tests/test_pregen_luck_verification.py](../../../tests/test_pregen_luck_verification.py)
- [tests/test_pregen_and_creation.py](../../../tests/test_pregen_and_creation.py)
- [tests/test_pregen_extra_fields.py](../../../tests/test_pregen_extra_fields.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/pregen_luck_roll_design_spec.md)
