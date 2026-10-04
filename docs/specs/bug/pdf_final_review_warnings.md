# PDF final review warnings

[繁體中文](pdf_final_review_warnings_zh.md)

Status: implemented; The Haunting real-PDF smoke passed. Branch: `fix/pdf-final-review-warnings`.
Baseline: main_v2 `92b52e4da88063d14ae3c9585fda9d5060d19b37`.

## Problem and goal

Early `low_text` and successful `local_ocr_repaired` history currently trigger review even after the selected text is repaired. Change only final review_pages selection; retain every historical warning and evidence record.

## Design

Add a private `_page_requires_review(row: dict, final_text: str) -> bool` in app/pdf_loader.py and replace its final review condition. Informational warnings are only native_two_columns, layout_unavailable and local_ocr_repaired. low_text requires review only while final selected text remains below the existing 200-character threshold. All other and unknown warnings still require review regardless of length. Preserve empty_page detection. Evaluate selected source text before appending diagnostic markers. No schema or extraction interface changes; Paddle fallback metadata remains metadata.

Existing unresolved warnings include ambiguous_columns, empty_page, source_pair_unresolved, numeric_pair_review, layout_pair_mismatch, layout_numeric_loss, layout_text_loss, ocr_pair_review, ocr_pair_mismatch, ocr_evidence_loss, local_ocr_review, ai_fields_unresolved, vision_failed, vision_pair_review, vision_pair_mismatch and vision_review_required. Do not invent warnings or infer that these issues were resolved.

## Scope

Only pdf_loader and necessary tests change at runtime. No OCR, Paddle Layout/PR160, model/dependency, provider, library, map, RAG, gameplay or Discord copy changes. No scenario import or existing data mutation.

## Validation

Use public extract_text regressions with controlled candidate/repair outputs for resolved/unresolved low_text, exact threshold, successful local repair, unresolved local repair, numeric review, OCR evidence loss, vision review, existing informational warnings, unknown warnings and empty pages. Assert original warning history is unchanged, independent of OCR engine names.

Use the same user-reported PDF/evidence for pages 12,16,17; record final text length, historical/resolved/unresolved warnings and before/after review decisions outside the repository. Do not log full PDF text or force removal of unresolved pages. Source files are read-only, with no save_scenario call. Run pytest, ruff check ., mypy app, compileall app tests and diff-check before implementation commit/push. No PR was requested.

## Open input

The requested scenario ID 70dbe2a5 is absent. An existing 1f0e59f0/source.pdf and its historical quality report were used; the PDF hash matches other saved Haunting trimmed copies. Historical provider/OCR outputs are not claimed to reproduce the same environment.

## Implementation validation

Resolved low-text cases reproduced the false review under the old condition, then passed with unchanged warning history. Public extract_text regressions cover 199/200/250 characters and successful repair history. Full pytest: 1994 passed, 1 skipped, 183 subtests passed. Ruff, mypy (128 files), compileall and diff-check passed.

The Haunting SHA256: `de28127fe4978a32076a4a5099496c0a377b91408b2eb402151bd5151009fcb1`. Historical and new review_pages are both [12,16,17]. Applying the old condition to this same new evidence also returns that list.

- Page 12: 3196 → 3196 characters; native_two_columns is informational, numeric_pair_review remains unresolved. Retained; no OCR call.
- Page 16: 3356 → 3356 characters; native_two_columns is informational, layout_numeric_loss remains unresolved. Retained; no OCR call.
- Page 17: 29 → 169 characters; low_text and vision_review_required remain unresolved. Retained; Paddle accepted, no Tesseract fallback.

Local Python 3.13 extraction had no provider keys, zero network attempts, and unchanged source hash. The actual confirmation function lists pages 12,16,17. This PDF has no page with only resolved warning history, so it does not demonstrate removal; public extract_text regressions cover that behavior. Evidence is saved as local temporary JSON; the PR includes this summary. Repeated checks: 1994 passed, 1 skipped, 183 subtests passed; ruff, mypy, compileall and diff-check passed. OCR, Layout, player copy and existing data are unchanged.
