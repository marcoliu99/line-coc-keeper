# Verified Luck from the final merged role card

[繁體中文](pregen_sheet_luck_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Determine Luck after source reconciliation. A valid value in the final card avoids an unnecessary creation roll; absent or unresolved values retain the owner-triggered roll flow.

2. Preserve explicit source provenance and manual corrections. Reimporting a PDF must not silently overwrite a confirmed manual field or another investigator’s value.

3. Blank Luck is intentionally not treated as unreadable mandatory core data by PDF AI repair. Other unresolved core attributes may make only that card unclaimable.

## Flow and interfaces

```text
Source cards -> identity-aware merge -> final Luck provenance -> claim without unnecessary reroll
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/pregen_extractor.py](../../../app/pregen_extractor.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [tests/test_pregen_and_creation.py](../../../tests/test_pregen_and_creation.py)
- [tests/test_pregen_extra_fields.py](../../../tests/test_pregen_extra_fields.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/pregen_sheet_luck_design_spec.md)
