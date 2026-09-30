# Local PDF OCR evidence benchmark

Status: backlog (awaiting benchmark-spec review)

## Goal and baseline

Determine whether PaddleOCR justifies replacing Tesseract as primary **local OCR**, using real CoC regions and pages. No production integration is included. Base: main_v2 `189bc8e`; PR #155 is OPEN at `ead28a4` and is a separately inspected reference, not a merged baseline. Record both revisions in results and recheck before execution.

Reviewed owners: pdf_loader, pdf_quality, pdf_ai_repair, PR155 pdf_layout/pdf_layout_adapters, markitdown_shim, scene_map; tests/test_pdf_loader.py, test_pdf_numeric_pairs.py and PR155 layout/adapter/multicolumn tests and validation reports. Existing numeric/region probes establish candidate counts and one synthetic repair, not real OCR accuracy. Docling uses do_ocr=False/do_table_structure=False. MarkItDown vision and map extraction are separate owners.

## Scope and boundaries

Add a removable `scripts/experiments/benchmark_pdf_ocr.py`, benchmark-only dependencies and focused metric/worker tests. Do not edit app runtime, requirements used by the bot, OCR defaults, gameplay, map/RAG/agent code, or Docling options. No Camelot or external OCR API. Import only needed local evidence helpers; never call extract_text or provider lookup, which could invoke external analysis. Production private OCR access is permitted for experiments by existing Ruff configuration.

## Corpus and reference

Use supplied originals under Downloads/PDF文件: The_Haunting_Scenario_trimmed.pdf (27 pages, SHA256 de28127fe4978a32076a4a5099496c0a377b91408b2eb402151bd5151009fcb1), Dead Boarder.pdf (32, cf485d43f41af6beb7d72cf4ff6948c67b86666a7411f5fb34ae6a66170d5274), The Lightless Beacon - Call of Cthulhu.pdf (43, 14437ca4eca3b6c05b22403bdad30bdb8668f1837e0a2d0ee42e49c160d8aa07). Paths are local inputs, never bundled PDFs.

Initial selection: Haunting physical 11,12,14,15,17,18,20,22,24; Dead Boarder 17,18,20,21,23,25,27,29,31; Beacon 13,16,17,23,24,26,27,28,29,30,31,34,37,40. Map pages are evaluated only for OCR text, never graph accuracy. Also include original correct prose controls Dead 5 and Beacon 6. Final manifest records inclusions/exclusions and reasons **before** results. Do not select only winner pages.

Render actual pages to inspect references. Native PyMuPDF evidence is a reference candidate, not ground truth when damaged; visually verify numeric/stat/skill/dice gold. For scanned pages, build bounded visually verified gold regions; do not use Tesseract or AI extraction to define truth. Unverifiable regions remain unscored with reason. No reference means null metrics, not perfect scores. Separate representative handpicked regions from genuinely production-selected suspect blocks. Real page count means distinct physical pages actually processed, not region count.

Private gold transcriptions/images/output live outside repository. Commit only identity hashes, physical page, crop coordinates/hash, bounded necessary token/value diagnostics, metrics and errors; no complete paragraphs or page OCR dumps. Synthetic fixtures (swaps, O/0, I/1, dice, blank Luck, negation, added numbers, rotated input) are reported separately and cannot justify a real-corpus winner.

## Fair inputs and execution

Region comparison is primary. Reproduce `_repair_local_regions`: unresolved pair block or replacement character, bbox padded by (-2,-2,+2,+2), intersect page.rect, render 300 DPI; rotation skip is separately reported. Identical PNG bytes go to both engines. Scanned-card manually defined crops are labeled representative, not claimed to be production native-block selections. Full-page 300 DPI comparison is secondary. Persist coordinates, DPI and input hash.

Tesseract baseline calls existing `_ocr_image` with pytesseract and production language order chi_tra+eng then eng; record actual success branch, language, version, traineddata identity and effective PSM. Pytesseract defaults differ from CLI fallback (--psm 6). Missing dependencies/language packs are errors. Optional English-only or tuning trials are separate sensitivity runs, not substituted baselines.

Paddle uses a pinned stable officially supported local PaddleOCR/PaddlePaddle combination selected during explicit isolated setup. Record package/Python versions, detector/recognizer model IDs and hashes, language, CPU/backend, orientation settings and threads. Target CPU macOS arm64; verify official wheel compatibility including project Python 3.14. If another supported Python is needed, use an isolated environment and report deployment mismatch. Model downloads occur only during explicit setup, with local cache paths recorded; OCR workers must resolve explicit predownloaded model paths offline. No cloud API, telemetry/document upload or bot-startup downloads. Failure to install/import/initialize/infer is a Paddle failure, never Tesseract output under its name.

Bound each worker and page by timeout; kill/reap on failure. Cold initialization separate from first inference; persistent warm workers run at least three measured repetitions after warmup, alternating engine order. Record each sample, medians/p95, peak RSS (with OS units), CPU when available, host/OS/architecture, model download sizes and time. Linux CI implications are documented compatibility evidence, not a claimed Linux execution if unavailable.

## Metrics and existing validation

Reuse pdf_quality.block_evidence/numeric_pairs/check_pairs, `_NUMBER`, normalization and accept_region. Numbers use occurrence multisets: matched/expected recall plus added occurrence count; duplicates count. Report per region/page and denominators as well as aggregates. Exact rates do not repair O/0 or I/1 confusions. `_NUMBER` has limits (bare d100, +DB and percent boundary behavior); add benchmark-only complete dice/percentage token scoring with tests, leaving production unchanged. Report strict original spelling and optional case-insensitive dice rates separately. Never turn d/1 confusion into a match.

Pair metric checks exact label/value association and multiplicity, distinguishing mismatch, missing, source-unresolved and geometry-unverified. Supplement existing check_pairs with benchmark geometry for OCR boxes converted from crop pixels to PDF points, retaining ambiguity instead of forcing pairing. Publish raw-text existing-validator score and geometry-aided score separately; do not silently normalize table order to grant success. Skill metric requires exact skill label + percentage, including specialization; value-only match is failure. Report source labels excluded as ambiguous. Blank Luck must remain blank.

Text coverage is token-multiset recall against verified reference, with optional CER only where full gold exists; prose cannot compensate numeric failure. Added numeric/dice/label occurrences are severe when verified against rendered source. Ordinary added words/sentences are judged only inside gold scopes; outside them are unverified, not declared hallucinations. Preserve negation/condition diagnostic checks. Repeated true numbers elsewhere do not cure wrong local values.

Separately run existing accept_region on the same original native block and each candidate. Eligibility, acceptance and rejection reasons are pipeline metrics, not OCR correctness. It requires replacement-character corruption; a perfectly read unresolved table may still be ineligible. Report this limitation without changing validator.

## Report and recommendation

Write `docs/specs/enhancement/pdf_ocr_benchmark_results.json` and `pdf_ocr_benchmark_validation.md` (+ _zh). Include revisions, manifest, environment/setup, engines, real_pages, synthetic_pages, failures, metric numerator/denominator, region vs full-page distinction, cold/warm timings, severe regressions and missing evidence. Failures are not zero-time successes or silently dropped from completion coverage.

Per-page classifications: Paddle win, Tesseract win, tie, both failed and incomparable/unscored. Use correctness first: no new severe errors; compare paired numeric/pair/dice/skill outcomes, report tradeoffs rather than hide them in text average. If a reference metric is absent it is not a win. Show matched-region completion counts and severe-error pages.

Choose A only for consistent real-region improvement across critical covered classes without added severe errors; show absolute counts, per-class rates and paired differences, acknowledging limited handpicked samples. Choose C for isolated repeatable class improvements; B for no justified quality benefit; D for insufficient gold, runtime/model failure, incomplete comparison or inconclusive/mixed evidence. No mandatory winner. If A/C, append a minimal follow-up proposal for _ocr_image/local validation/Tesseract fallback and relevant tests; no integration in this branch. Preserve MarkItDown OCR, pdf_ai_repair, Docling ordering and scene_map.

## Validation and flow

Private PDFs -> fixed page/crop manifest -> identical 300 DPI PNG -> isolated engine workers -> private raw output + verified gold -> existing validation and separate strict metrics -> sanitized JSON/EN/ZH report -> evidence-based decision.

Focused tests: numeric substitutions/additions/duplicates, stat swaps, skill swaps, bare d100/+DB, empty denominator, incomplete references, rotated/crop coordinate mapping, worker timeout/failure, no engine substitution, aggregation/classification, report redaction. Use synthetic data in committed tests only. Run full pytest, Ruff 0.16.8, mypy app, compileall app tests scripts/experiments, diff --check. Record actual results; spec-only stage has not run benchmark or these runtime gates.

## Pending review

Confirm this isolated benchmark plan. Implementation/install/model inference begins only after spec review per branch-spec-workflow. Unknown model compatibility and gold coverage are measurement risks, not assumptions to bypass.

## Execution authorization — 2026-10-01

User approved evidence-only execution. Compare Tesseract, en_PP-OCRv5_mobile_rec and PP-OCRv5_server_rec on identical real English inputs. Prioritize exact numeric/dice/skill quality over speed; defaults confer no production preference. Use PaddleOCR 3.7.0 and officially documented macOS CPU PaddlePaddle 3.3.0 in isolated Python 3.11 (project Python 3.14 is a deployment mismatch). Download models only in an explicit setup action without document inputs; inference requires explicit local model paths and OS network denial. Record package/model/cache identities and setup requirements. No cloud calls or application/runtime changes; no production integration.
