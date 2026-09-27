# Evidence-preserving PDF extraction and targeted repair

[繁體中文](pdf_parse_quality_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Store the complete source; MAX_SCENARIO_CHARS constrains prompt/comparison input rather than truncating the library PDF. Preserve page boundaries, original language and source coordinates.

2. Select native/layout text per page using numeric and word coverage. Coverage applies even to short pages. A layout candidate with mismatched, unresolved or unverified pairs cannot become authoritative.

3. Use word/block geometry for horizontal and aligned vertical stat tables. Keep arbitrary multiword skill labels with percentages. Prose mentions such as STR rolls are not blank character fields.

4. Local OCR repairs bounded damaged regions and preserves intact words/numbers. Import-time AI vision handles selected uncertain regions through the configured provider, default at most 8 page calls and 8 blocks per call.

5. AI transcribes visible evidence only; readable, blank and unreadable are different. Reject duplicate region answers, invented numeric additions and changed verified pairs. A human KP login is not required.

6. Blank Luck remains blank and is never completed by AI. Other unresolved core attributes receive PDF_UNRESOLVED_FIELDS markers and cannot silently default to 50 during character construction.

7. Only affected cards become unclaimable; other source pages remain usable. Explicit manual corrections survive later extraction. Budget exhaustion, provider failure and unsupported rotation preserve original evidence.

8. Persist candidate text, checks, selected method, repair decisions and crop hashes for diagnosis. Heuristic coverage and validated transcription reduce errors but do not prove every PDF is semantically complete.

## Flow and interfaces

```text
PDF -> native geometry + layout candidates -> coverage/pair validation -> bounded local OCR -> bounded AI repair -> complete source + report
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/pdf_loader.py](../../../app/pdf_loader.py)
- [app/pdf_quality.py](../../../app/pdf_quality.py)
- [app/pdf_ai_repair.py](../../../app/pdf_ai_repair.py)
- [app/pregen_extractor.py](../../../app/pregen_extractor.py)
- [tests/test_pdf_loader.py](../../../tests/test_pdf_loader.py)
- [tests/test_pdf_numeric_pairs.py](../../../tests/test_pdf_numeric_pairs.py)
- [tests/test_pdf_ai_repair.py](../../../tests/test_pdf_ai_repair.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/pdf_parse_quality_design_spec.md)
