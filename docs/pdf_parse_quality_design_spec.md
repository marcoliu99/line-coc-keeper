# PDF parse quality improvement

## Goal

Preserve complete source evidence for gameplay and external translation. Fix parser
precedence, empty-result replacement, destructive source truncation and summarizing
OCR instructions. Improve conservative column ordering and make uncertainty visible.
No per-turn model stage is introduced.

## Flow

    PDF -> native blocks + optional layout parser
        -> conservative native column ordering
        -> compare candidate coverage and numeric evidence
        -> choose layout or native per page
        -> targeted fallback only for pages lacking usable text
        -> retain page markers, flag possible cross-page continuation
        -> complete source + parse_quality.json + page images/maps
        -> existing chapter window / bounded prompt / RAG

## Contracts

- Keep extract_text's five-element return contract; optional quality_report output
  records parser version, page count, source selection, warnings and continuation
  candidates. The legacy truncated flag is false because source is retained whole.
- Layout text cannot be replaced by an empty alternate result. Missing source words
  or numeric evidence cause a conservative native fallback and a review warning.
- Reorder native blocks only with strong two-column evidence and no spanning body
  block. Ambiguous layouts are flagged, not silently assigned a guessed order.
- MarkItDown processes only requested fallback pages; page numbers map back to the
  original PDF. It does not run on every already-readable page.
- OCR asks for verbatim original-language transcription including blank field labels,
  age, tables and full prose. No translation or invented missing values. Whole-page
  map interpretation remains separate, explicitly labeled derived material.
- Do not delete repeated headers or join uncertain cross-page prose automatically.
  Record probable continuation for review while preserving original page boundaries.
- Never cut library source at MAX_SCENARIO_CHARS. Existing prompt bounds remain.
- Persist quality report with source artifacts; report suspicious pages to the uploader.
  Warnings are heuristics, not proof of semantic correctness or accurate table mapping.
- Close PDF handles and isolate page fallback errors so one failed page does not
  discard readable pages. Unresolved pages remain visible in the quality report.

## Validation

Synthetic regression tests exercise precedence, empty alternates, lost numeric fields,
column order, full-source retention, continuation reporting and persistent diagnostics.
Run a local no-API probe of the supplied scenarios and role cards, report coverage and
warnings without claiming human-level parsing accuracy. Paid OCR is mocked in tests.
