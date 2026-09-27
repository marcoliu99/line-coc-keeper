# Repairing a PDF source before Chinese authoring

[繁體中文](scenario_source_review_zh.md)

Use the administrator CLI from the repository and Python environment that owns the
intended scenario library. These commands use local PDF extraction and rendering;
they do not call a translation/OCR API. Work on a copy first when investigating an
existing game. Review files contain private Keeper material.

## 1. Prepare evidence

```sh
python3 -m app.scenario_source_review prepare SCENARIO_ID /absolute/new/review-directory
```

The directory must not exist. It will contain `proposal.md`, physical-page PNGs,
and original/native text files. A separate private server registry binds the
original source, PDF, manifest and page identities. Raster pages may have no native
text; transcribe them from the image or an external OCR candidate and review them.
Native extraction alone is never marked reviewed.

Edit only `proposal.md`, keeping it alongside the original page PNGs. Each page has:

- `page`: immutable physical PDF page, not its printed footer number.
- `text`: corrected complete transcription, including meaningful dates, prices,
  percentages, dice, limits and reference rules.
- `review_note`: what was compared/corrected and why; record uncertain items until
  resolved. Do not publish while semantic or visual doubts remain.
- `evidence`: rectangles `[x0, y0, x1, y1]` in the rendered PDF page's point
  coordinates, with a note explaining their relevance. A whole-page rectangle is
  supplied as a starting point.
- `image_only`: explicitly preserve a purely visual page with no native text and
  no invented description. Visible labels or rules still need transcription.

Do not replace missing text with numbers merely to pass validation. Record removal
of decorative glyphs/footer numbers and preserve meaningful references in context.
A border glyph rendered as `0` beside `1D4` is not evidence for a `1D40` die.

## 2. Check the exact candidate

```sh
python3 -m app.scenario_source_review check /absolute/review-directory/proposal.md --report /absolute/new-check-report.md
```

The report includes complete before/after text and numeric counts. `ready: true`
means the review data is structurally complete; it does **not** prove that a human
or model transcribed the PDF correctly. Inspect the evidence. The operator remains
responsible for that decision. No source is changed by checking.

## 3. Publish a separate source version

```sh
python3 -m app.scenario_source_review publish /absolute/review-directory/proposal.md --reviewer REVIEWER_NAME --expected-digest DIGEST_FROM_CHECK
```

Publication rejects a changed proposal, source, PDF, manifest or evidence image.
It creates a new scenario ID with a source audit. It preserves the old scenario,
exports, drafts and active game selection. No Chinese template is automatically
approved. Repeating the identical operation with the same reviewer returns the same
new ID. Reviewed source blocks retain physical page boundaries.

Old derived indexes, pre-generated character objects and inferred maps are not
copied; they could still describe the faulty extraction. Full PDF images remain
available, initially Keeper-only. Rebuild derived information from the reviewed
text through the normal preparation workflow (`/coc index`, `/coc pregens` when
appropriate); those normal commands may call configured APIs. Do not reparse the
original PDF just to rebuild derived objects, as that would replace the corrections.

## 4. Rebind existing Chinese drafts

```sh
python3 -m app.scenario_source_review rebind NEW_SCENARIO_ID --old-scenario-id OLD_SCENARIO_ID --old-export-id OLD_EXPORT_ID
```

This creates a fresh ordinary authoring export with at most three workbooks and
result drafts. Reuse requires one old unit, a unique exact text match, and no
relations/dependencies that might be detached. Source/unit/record IDs and quotes
are compiled against the new registry. Anything changed or ambiguous is retained
in the private `migration-report.md` for manual retranslation/rebinding.

Complete all pending records, then import using the normal Help file picker.
These are first submissions under a **new** export, so do not copy old
`replace_record_ids`. Correcting records already saved in the new draft requires
that new batch's existing IDs. Final numeric checks and explicit template approval
still apply. Numeric inventories cannot establish complete semantic fidelity.
