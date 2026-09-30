# PDF multicolumn ingestion and reading-order arbitration

[繁體中文](pdf_multicolumn_ingestion_design_spec_zh.md) | [Docs index](../../README.md)

Status: **backlog — design review in progress**. Base: `main_v2` at `7cef87c`. This proposal refines the external draft `pdf_ingestion_multicolumn_design_spec.md`; it does not change gameplay turn logic.

## Problem and measured baseline

The current PDF pipeline already has PyMuPDF geometry, PyMuPDF4LLM, MarkItDown, per-page numeric-pair checks, local OCR, bounded AI repair, image/map extraction, and diagnostics. `pdf_quality.select_text()` selects a candidate by text and numeric coverage; it does not validate the candidate's reading order. A text-complete page can therefore be wrong.

On the supplied PDFs, a one-page reproduction using the current selection functions found opposite winners:

| PDF physical page | Current choice | Visual reading-order finding |
| --- | --- | --- |
| `The_Haunting_Scenario_trimmed.pdf` 14 | PyMuPDF4LLM | The selected text places right-column Extension / Walter Corbitt before left-column Conclusion / Rewards. The native two-column order is correct. |
| `Dead Boarder.pdf` 5 | PyMuPDF4LLM | The native text moves right-column Rules Notes before left-column Keeper Considerations. The selected layout order is correct. |
| `The Lightless Beacon - Call of Cthulhu.pdf` 6 | Native | Both candidate heading orders agree; the layout candidate loses numeric evidence. |

These are review candidates, not a complete corpus or proof of all-page accuracy. The native text layer and any single parser can each be wrong.

## Goal and first-release scope

Improve native-text two-column pages and full-width headings using page geometry and per-page arbitration. Include Docling's full PDF layout pipeline as an optional challenger for difficult pages only. Preserve existing numeric, OCR, character-sheet, map, source-review, translation, and gameplay contracts. Use a small real-page golden corpus from the supplied scenarios; correct all known interleaved pages without regressing already-correct pages, numbers, dice expressions, or source spans.

Later phases may add structured table extraction (including Camelot where appropriate), region OCR/vision for scanned columns, and more complex sidebars. Marker and Nougat are not first-release dependencies. A scan without text-layer geometry requires image-layout evidence; the native-text gutter detector must not claim confidence for it.

## Existing contracts to preserve

- `pdf_loader.extract_text()` returns the existing five-part result and produces the full `scenario.txt` with `--- 第 N 頁 ---` markers. `scenario_library`, chapter selection, RAG, source review, and translation consume this representation.
- `parse_quality.json` retains native/layout candidates, block evidence, numeric pairs, repair attempts, derived descriptions, and warnings. Add page decisions to that report instead of replacing it.
- Source units and Unicode `[start, end)` spans are computed against the final rendered `scenario.txt`, never against a parser's raw text.
- `source.pdf` remains immutable. Scenario text content hash and PDF byte hash have different meanings and must both be recorded where needed.
- The map graph from `scene_map.analyze_page_image()` remains separate from verbatim transcription. Character-sheet blank Luck and existing unresolved-field rules remain intact.

## Internal page model

```text
PdfPageEvidence
  pdf_page, dimensions, rotation, native_blocks, words, images,
  candidates, selected_regions, decision, diagnostics

PdfBlock
  stable page-local id, text, optional bbox, coordinate space,
  role, column/region, reading order, extractor, alignment evidence

ExtractionCandidate
  extractor/version, page, text or blocks, original provenance,
  alignment status, pair checks, diagnostics
```

A bbox is optional. A Markdown candidate without verified geometry must not receive an invented PDF coordinate. Approximate text alignment may place a candidate passage against a native block, but does not edit the passage. It must be one-to-one, exceed a calibrated minimum score, and beat the second candidate by a calibrated margin. Repeated prose, numbers, dice expressions, and negation markers are checked separately. Unresolved alignment remains a page diagnostic.

The internal page evidence is new; the published text and span interface stays compatible even when the operator rebuilds scenario data.

## Proposed flow

```text
PDF bytes + immutable hash
  -> native blocks/words and existing per-page candidates
  -> detect text columns, spanning headings, floating regions
  -> candidate-to-geometry alignment and reading-order diagnosis
  -> local geometry order + optional Docling challenger on hard pages
  -> numeric/pair/source checks and decision
  -> accepted page text -> deterministic existing page-marker renderer
  -> scenario.txt -> chapters / source units / translation / RAG
```

Do not compare only a weighted score. Hard constraints come first: no missing required source text, numeric/pair regression, source-span misalignment, or unsupported placement. Among eligible candidates, compare reading order and completeness. The native layout baseline is evidence, not automatic truth. The published decision records candidate, reason, region mapping, confidence, and warnings. Docling cannot publish by itself; its ordering and text must pass the same gates. Optional dependency failure and timeout leave the existing candidates available and record a diagnostic. If none is adequate, retain a draft page, not a fabricated winner.

The first release does not change the scan, map, character-sheet, table, and local repair routes. Region-level OCR and table-specific extraction remain follow-up work. Any parser or OCR result that is only a visual description remains labeled as derived material, not a verbatim source quote.

## Draft and publication

Accepted pages and their diagnostics may be saved in an import draft. A page with unresolved reading order or source loss is blocked individually for repair. A newly published playable scenario must have all plot/rules-relevant pages resolved; an existing published scenario stays available. Publication still uses the trusted scenario-source transaction and invalidates derived translation, records, and indexes when source text changes. The exact repair route is under review.

## Versioning and cost

Record PDF hash, extraction pipeline version, renderer version, per-page candidate versions, and resulting scenario text hash. A changed PDF, parser output, or renderer cannot silently reuse stale source spans or translation packages. Only hard pages invoke Docling; report invocation count, per-page and total parse time, failures, and any quality improvement. Import cost may increase, but normal gameplay must not add LLM calls or runtime PDF parsing.

## Tests and release gate

1. Golden order on selected real native-text pages, including the opposite-winner Haunting and Dead Boarder cases, a correct control page, spanning headings, and a repeated numeric/stat block.
2. Synthetic pages for ambiguous gutters, near-equal approximate matches, repeated text, rotated coordinates, and a candidate with lost numeric or negation evidence.
3. Existing `pdf_quality` numeric-pair, local OCR, AI repair, map, character-sheet, scenario-library, source-review, translation, and RAG tests remain green.
4. Final page markers, Unicode source spans, and source quotes align after rendering. Existing scenarios continue to load; a changed source invalidates stale derived artifacts.
5. Compare per-page order, text/numeric recall, source alignment, parser calls, and import duration against the old pipeline. Release only when every known wrong-order golden page is corrected and controls, numbers, dice, and spans show no regression.

## Open decisions from the interview

- Whether blocked pages are repaired through targeted external-AI PDF/source review or another route.
- Whether existing scenarios are reprocessed only when the operator chooses each book, or automatically.
- The final domain term separating approved source text from world canon.
- Whether approximate-alignment thresholds are calibrated on the golden corpus or fixed before measurement.

Implementation waits for the completed design review.
