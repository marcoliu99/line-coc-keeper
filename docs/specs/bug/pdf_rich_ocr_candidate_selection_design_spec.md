# Preserve rich OCR candidates on low-text PDF pages

## Problem and goal

A low-text graphic page may leave only a page header, folio, layout markup, and vertically fragmented decorative letters in the selected native/layout source. `pdf_quality.select_text()` applies its ordinary word-coverage rule to that short source and can reject a much richer MarkItDown OCR candidate for omitting the fragments. In the hash-matched Lightless Beacon import, physical pages 31, 32, 34, 35, 37, 38, 40, and 41 each retain only 87–139 selected non-vision characters despite saved OCR candidates of 1,683–4,233 characters. The goal is a deterministic, narrow rescue path that selects a rich OCR candidate only when baseline source and mechanics safety are preserved. A longer candidate alone is never sufficient.

## Scope and non-goals

Change only the MarkItDown candidate decision in `app/pdf_loader.py`, with a small deterministic validator in `app/pdf_quality.py` if needed, synthetic tests, and this spec. Preserve the existing general `select_text()` behavior for native/layout arbitration and normal narrative pages. Keep `pymupdf4llm.to_markdown(..., use_ocr=False)`, Paddle OCR/Layout, Tesseract fallback, budgets, provider choice, map extraction, scenario index, pregens, review-warning rules, and gameplay unchanged. Do not call an LLM for candidate selection. Do not accept all MarkItDown output as PR30 did.

## Candidate decision

Run the new validator only inside the existing MarkItDown loop when selected source is below `_LOW_TEXT_THRESHOLD` (currently 200), the candidate reaches that same threshold, and the page already entered the low-text graphic `pending` path. An ordinary narrative page never enters this rescue. The current `select_text()` result remains the first decision; the rescue can apply only when its sole rejection is word coverage of a short baseline. Any numeric or pair mismatch remains a rejection.

For rescue, classify baseline evidence conservatively. Strip only deterministic parser artifacts (e.g. PyMuPDF4LLM picture-text comments), isolated folio numbers, and isolated vertically fragmented letters from the *coverage comparison*; keep the original baseline in the quality report. Substantive source phrases, labels tied to values, percentages, dice, modifiers, and full slash-delimited SAN expressions remain required. If a short baseline contains an unclassifiable substantive phrase that the candidate does not preserve, reject with `source_content_loss`; do not decide that it is a header by scenario title or page number. Numeric counts should not silently treat a folio as a game value, while any value bound to a source label or prose remains required.

Use existing native `numeric_pairs()` / `check_pairs()` evidence and compare all resolved pairs. A `pair_mismatch` or missing required pair rejects. Compare complete mechanics occurrences, retaining dice modifiers and SAN slash order as a unit; `1d6+2` is not `1d6`, and `1/1d6` is not `1d6/1`. Reuse the repository's narrow source-aware `l/I`-to-`1` dice normalization semantics where applicable; never make a global letter rewrite or guess a changed die size. Do not allow a candidate with replacement characters or malformed obvious mechanics.

Require positive structured evidence in the candidate, independent of its length: recognized CoC characteristic/resource labels with values, skill labels with values, or structured player form fields. This is a deterministic plausibility gate, **not** proof that OCR invented no content. If the page has no such anchor, or if preserved-source checks cannot be established, reject and retain review. The validator reports `accepted`, `candidate_too_short`, `numeric_loss`, `pair_mismatch`, `mechanic_loss`, `insufficient_anchor`, `source_content_loss`, or `not_low_text_source` (names may be adjusted to match code conventions).

After acceptance, select the existing MarkItDown candidate and set `method=markitdown`; keep native/layout candidates, warnings, and all earlier evidence. Add compact `rich_ocr_validation` metadata: attempted, status/reason, selected/candidate character counts, mechanics check status, pair-check status, and anchor count. Do not duplicate candidate text in this metadata. Do not change `_page_requires_review()` or resolve warning history merely because selection changed. Existing `pending` removal, vision/map need, and image persistence rules continue to depend on the selected text through their current flow.

## Data and integration

No database, API, or publication-state schema change. The optional new quality-report key must tolerate existing reports without it. The validator accepts only short selected source, candidate text, native numeric-pair evidence, and the existing threshold; it never reads model output other than the already-collected candidate. Keep the general `select_text()` function strict, and preserve rejected candidate behavior and `ocr_evidence_loss` when rescue fails.

## Tests and validation

Synthetic tests cover: a rich role sheet missing header/folio fragments; stat value conflict; complete dice modifier; full SAN expression with left/right order; rich unrelated prose without structural anchors; substantive short baseline phrase loss; ordinary >200-character narrative retaining strict coverage; empty/short candidate; and pair checks. An integration fixture verifies accepted method/text plus preserved warning history and compact validation metadata, while rejected candidates remain unchanged. Existing MarkItDown-selected pages, narrative pages, map image persistence, and scene-map behavior remain regression controls.

For the private 43-page Lightless Beacon PDF, verify SHA-256 `14437ca4eca3b6c05b22403bdad30bdb8668f1837e0a2d0ee42e49c160d8aa07` before any smoke. Report sanitized old/new selected method and character counts, candidate counts, validation reason, and review status for pages 31, 32, 34, 35, 37, 38, 40, 41. Also inspect pages 3, 6, 13, 16–19, 25, 27–30; compare page images, scene maps, MarkItDown calls, vision calls, and parse time. Do not log or commit source prose. Run full pytest, ruff, mypy, compileall, and `git diff --check` after implementation. Do not force 8/8 promotion; any failure of source/mechanics evidence remains rejected.

## Tradeoff to review

A short baseline sometimes contains only layout debris, so exact whole-page word coverage is too strict. Conversely, a rich OCR candidate can hallucinate values. The proposed validator trusts neither length nor absence of pair conflicts alone: it demands structured positive anchors and preservation of every observable source-bound mechanic and substantive phrase. Where those checks cannot establish safety, the page stays on the existing conservative path.
