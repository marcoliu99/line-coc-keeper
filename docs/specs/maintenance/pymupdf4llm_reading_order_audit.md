# PyMuPDF4LLM reading-order audit

## Goal and scope

Explain why native multi-column text can still be out of order with PyMuPDF4LLM installed. Investigate the latest `main_v2`, compare raw PyMuPDF, PyMuPDF4LLM and the application's selected text on five pages, and preserve reproducible evidence. Only change application code if an application defect corrupts correctly ordered library output. If the library itself fails, report the limitation without adding tools.

Baseline: `7d2cc8cab76d25aa1f47c47568ad2f9ff05d910b` (Revert PR155 PDF multicolumn ingestion rollout). Python 3.14; PyMuPDF and PyMuPDF4LLM 1.28.2; installed layout mode enabled. Requirements permit different package versions, so these findings apply to this recorded environment.

No schema changes, importer refactoring, new dependency, Paddle change, gameplay change, scenario import/reparse, publication, indexing or production state writes. Existing source PDF is read only. Existing AI providers have empty credentials; local OCR and AI repair budgets are zero for the application comparison. The library's default automatic OCR is recorded separately from a fresh-document `use_ocr=False` comparison.

## Actual flow

```text
PDF bytes -> PyMuPDF4LLM.to_markdown(page_chunks=True), all supplied pages
          -> native PyMuPDF text/blocks/words per page
          -> coverage + number preservation + geometric label/value checks
          -> select layout text OR native fallback
          -> bounded local repairs when needed
          -> low-text graphic pages only: MarkItDown OCR
          -> bounded AI repairs when needed
          -> remaining low-text graphic pages: existing vision fallback
          -> unresolved annotations + page markers -> returned source text
```

`app/pdf_loader.py` owns extraction. `app/pdf_quality.py` contains the native two-column heuristic and candidate checks. `extract_preview` uses native text for similarity preview only. `legacy_commands.py` passes authoritative text to `scenario_library.save_scenario`, which writes it unchanged; `load_context` selects page ranges without reordering their paragraphs. Source-authoring and review helpers operate on explicit edits/rendering, rather than automatically replacing extraction with raw blocks.

Paddle is absent from this baseline's application and requirements. Existing OCR consists of pytesseract/Tesseract and the MarkItDown OCR adapter, plus existing provider image analysis/field repair. Installed PyMuPDF4LLM also defaults to automatic Tesseract OCR; its role is therefore not exclusively native text in this environment.

## Five-page comparison and findings

Use existing `data/scenarios/the-haunting-scenario-trimmed-191b3b2b/source.pdf` pages 1, 3 and 15 (printed 17, 19 and 31), plus synthetic single-column and spanning-title three-column pages. Render each for visual ground truth. Compare each method on fresh documents to prevent automatic OCR's in-memory page mutation contaminating the raw or native-only comparison.

| Page | Raw PyMuPDF | PyMuPDF4LLM / actual final |
|---|---|---|
| Source 1, large title + two columns | Body left then right; title near end | Default OCR mixes columns and duplicates heading; native-only restores title/left/right |
| Source 3, normal two columns | Complete left then right | Right `LOCATION 3` and `Handout 4` precede remaining left `Handout 2`; native-only also fails |
| Source 15, skills/spells/image | Complete left then right | Right spells precede left Armor and biography; native-only also fails |
| Synthetic single column | Correct | Correct |
| Synthetic title + three columns | Row interleaving from deliberately alternating content stream | Title first, then same row interleaving; native-only also fails |

All five application rows select `method=layout`; final text equals library output modulo whitespace. No B/C overwrite is reproduced and no page is skipped by a multi-column dispatch condition. The active metadata uses 1-based `page_number`, correctly mapped. The legacy `page` key is not exercised by this installed release; no speculative compatibility fix is made.

Classification: **A**, with **E** layout complexity contributing. The library's `document_layout.parse_document` runs `utils.find_reading_order` before writing Markdown. Tracing its grouping reveals:

- Source 3: one stripe containing 26 body boxes is split into five column/substripe groups `[9, 6, 1, 4, 6]`, interrupting the left column.
- Source 15: 22 body boxes become two horizontal stripes `[9, 13]`; the first is read left then right before the remaining left text. The second groups all 13 boxes together.
- Synthetic three columns: all nine body entries are detected as one wide text box, so later column grouping cannot recover separate columns.

The application's coverage/number checks do not validate reading order and therefore accept these complete-but-misordered outputs. This is an observed validation limitation, not evidence that application postprocessing corrupted a correct output. Default OCR additionally changes source 1; disabling it alone does not fix the other failures. No runtime fix is proposed in this round.

## Verification and deliverables

Durable evidence: `/Users/marcoliu/workspace/pymupdf4llm-order-audit-20261003/`.

For each comparison page preserve `page_XX_pymupdf.txt`, `page_XX_pymupdf4llm.md`, `page_XX_pymupdf4llm_native_only.md`, `page_XX_current_final.txt`, native candidate, rendered PNG, metadata/quality report, subset PDF, grouping trace, reproduction script and standalone pytest assertions. The existing scenario PDF hash is `de28127fe4978a32076a4a5099496c0a377b91408b2eb402151bd5151009fcb1`.

The public observation seam is `extract_text`'s returned text; direct library results provide the second boundary. Expected column order comes from visual positions, not from import success. Assertions cover single-column order, both real dual-column pages, large/spanning title, three columns, numbers, formulas, words and preservation of selected layout order.

- Standalone evidence assertions: **18 passed, 4 failed**, failures intentionally exposed rather than marked xfail. These are saved outside repository CI and check the captured comparison after `compare.py` runs; they are not newly installed CI regression tests.
- Existing PDF suite: **41 passed**.
- Existing full suite: **1862 passed, 1 skipped**, plus **152 passed subtests**.
- No native numeric/dice token loss or missing native word tokens on these five pages after ignoring Markdown markers; this is token preservation, not proof of semantic or paragraph correctness.
- Single-column order remains correct. No code change means no before/after fix claim.
- Page headers/footers remain in the output; source 1 retains the printed page number twice (also present in raw native text) and has a duplicated heading in default OCR output. Source 3's left paragraph is placed beneath right `Handout 4`, changing its apparent ownership despite preserving the words.

## Decision

PyMuPDF4LLM has clear limitations on the tested layouts. Keep application code unchanged. Do not add tools or adopt a fallback architecture here. A future, separately approved step may evaluate existing Paddle layout analysis or a narrow native-text option, but neither is part of this audit. Any later application change needs its own reviewed scope and actual regression tests; the isolated evidence suite provides the failing examples.
