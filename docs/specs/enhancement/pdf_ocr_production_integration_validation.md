# PR155 offline OCR integration validation

> Reassessment: this legacy v3/v4 comparison includes hidden-OCR and routing confounds. It is not a Paddle causal comparison or acceptance threshold. See [controlled/image-only validation](pdf_ocr_image_only_validation.md).

Implementation is complete on `enhancement/pdf-multicolumn-ingestion`. Deployment readiness remains limited: the offline extraction run accepted **zero real OCR candidates** and left more scanned pages blocked. This is not evidence of improved completed imports. No numeric/pair/dice/source-word regression was established; one raw word-counter difference was verified as Markdown emphasis (`Corbitt` versus `_Corbitt`), retained explicitly in the JSON audit.

## Architecture and configuration

Reading order remains native PyMuPDF geometry → deterministic layout → difficult-page Docling permutation. Docling retains `do_ocr=False`, `do_table_structure=False`; it does not replace source prose. PyMuPDF4LLM 1.28.2 has hidden OCR enabled by default: the adapter now explicitly disables `use_ocr` and `force_ocr` so it cannot bypass local candidate validation.

OCR remains import-time, eligible suspect regions (existing padding and 300 DPI) or low-text graphic pages only: `PP-OCRv5_mobile_rec` → deterministic gate → Tesseract on rejected/empty/unavailable/error → gate → existing eligible MarkItDown/AI routes → gate → unresolved. Ordinary native narrative pages do not run whole-page Paddle. Separate finite page allowance equals the local region limit; both are diagnostic local execution, not paid provider budget. Map candidates still run `scene_map` even when OCR succeeds. Map graphs survive rejected/empty transcription; missing graphs remain review reasons. No gameplay files were changed.

Optional pins: PaddleOCR **3.7.0**, PaddlePaddle **3.3.0**, PaddleX **3.7.2**. CPU, four threads, MKLDNN disabled; shared `PP-OCRv5_server_det`, max side 960; orientation/unwarping disabled. Default recognizer is multilingual `PP-OCRv5_mobile_rec`, configurable without language routing. Heavy dependencies are separate in `requirements-pdf-ocr.txt`.

Run `scripts/setup_pdf_ocr.py` explicitly with a persistent compatible Python 3.11–3.13 environment; the bot's tested Python 3.14 uses the configured worker interpreter. Setup installs pinned dependencies, downloads official models and writes hashed manifests. Production cache defaults to `DATA_DIR.parent/models/paddleocr`; temporary cache locations are rejected by setup. Validation models were stored in `/Users/marcoliu/workspace/line-coc-keeper/data/models/paddleocr`. The temporary validation worker environment is not a deployment recommendation.

Settings: `PDF_OCR_PADDLE_ENABLED=true`, `PDF_OCR_PADDLE_MODEL=PP-OCRv5_mobile_rec`, persistent `PDF_OCR_PADDLE_MODELS_PATH`, `PDF_OCR_PADDLE_DEVICE=cpu`, `PDF_OCR_PADDLE_PYTHON`, and bounded timeout. Runtime checks local artifacts and hashes, uses explicit local directories, disables model-source checks and Python network connections; it never downloads on startup/ingestion. Dependency/model/init/inference/timeout errors are audited and fall back to existing pytesseract/CLI Tesseract. Real validation additionally denied network at the OS level.

## Acceptance, provenance and identity

Confidence is diagnostic only. Gates reuse `numeric_pairs`, `check_pairs`, `_NUMBER`, `accept_region`, native geometry and intact wording. Exact complete dice and percentages are checked separately, including `d100`, `1d10+DB`; unsupported numeric additions, swapped stat/skill values and prose rewrites are rejected. A damaged region must be uniquely replaceable. Intact token order must remain. Already-recovered regions skip stale OCR only when the same gate certifies them. Scanned critical content without sufficient source evidence remains unresolved; engine agreement is not proof.

AI validation now shares the complete dice/percentage guard while preserving the existing evidenced scalar-completion contract. MarkItDown and generic vision transcription pass deterministic validation before becoming source; unverified content remains private/derived.

Attempt audit extends existing evidence with `engine`, `model`, `candidate`, `status`, `reason`, `elapsed_ms`, pair checks and optional confidence. Ordered attempts expose Paddle rejected → Tesseract accepted. Candidate diagnostics do not become final unresolved warnings after a successful repair. Committed corpus evidence contains hashes/pages/metrics, not full candidates or PDF prose.

`extraction_identity()` adds enabled/model/device, actual worker PaddleOCR/PaddlePaddle/PaddleX versions, adapter version, local model state/digest and Tesseract identity. Pipeline is **multicolumn-v4** (was v3); quality is **ai-import-repair-v6** (was v5). Changed identities invalidate old accepted OCR caches conservatively; same-identity accepted native pages remain reusable. Continue/Status/Cancel tests remain green.

## Real corpus before/after

Baseline `ead28a4ac4ab7a7b7f9829cd2e2462fc3b2c4ef6`. Same PDFs, installed dependencies, CPU, local budgets, no provider keys, Docling disabled and OS network denied. Full extraction: **102 pages**, three books. Before included the SDK's hidden OCR; after intentionally disables it. Thus these totals compare final safety/routing, not a recognizer-only speed benchmark.

| Book | Pages | Review before → after | Blocked before → after | Seconds before → after | Paddle / rejected / failed | Tess fallback / accepted |
|---|---:|---:|---:|---:|---:|---:|
| The Haunting | 27 | 3 → 13 | 2 → 13 | 22.086 → 133.891 | 8 / 8 / 0 | 8 / 0 |
| Dead Boarder | 32 | 8 → 11 | 3 → 9 | 25.477 → 184.516 | 14 / 14 / 0 | 14 / 0 |
| The Lightless Beacon | 43 | 15 → 27 | 6 → 26 | 47.139 → 96.040 | 16 / 15 / 1 | 16 / 0 |

Totals: **38 Paddle attempts, 0 accepted, 37 rejected, 1 failed; 38 Tesseract fallbacks, 0 accepted**. The failed attempt remains explicit in private audit; no successful result is substituted for it. AI repair request accounting **2 → 2**, but **zero successful provider responses**, keys absent. Source-pair unresolved, numeric-pair review and AI-fields unresolved: **0 → 0**. Local OCR review: **2 → 2**. Floor-plan graph missing: **6 → 6**. These warning counts do not mean scanned numeric correctness was certified.

Remaining review pages (physical, one based): Haunting **7, 11, 17–27**; Dead Boarder **1, 3, 12, 19–21, 23, 25, 27, 29, 31**; Beacon **1–5, 13, 16–18, 25–42**. Detailed blocked lists, per-page source metrics, identities and warnings are in [extraction JSON](pdf_ocr_production_integration_results.json). Actual spatial graphs were not generated offline; map routing regression tests verify successful text OCR cannot bypass scene-map analysis.

## Independently verified model quality

The same **12 manually verified real region images** from the earlier pilot were run through the actual multilingual production adapter. This is separate from the full-extraction gate; reference crops are private and hash bound. [Model JSON](pdf_ocr_production_model_validation.json) contains per-region outcomes and separate synthetic results.

| Metric | Prior Tesseract pilot | Multilingual mobile adapter |
|---|---:|---:|
| Exact numeric glyph occurrences | 50/103 (48.54%) | 96/103 (93.20%) |
| Raw `_NUMBER` occurrences | 38/103 (36.89%) | 94/103 (91.26%) |
| Raw label/value pairs | 22/50 (44%) | 32/50 (64%) |
| Exact dice | 5/6 | 6/6 |
| Exact skill pairs | 4/4 | 4/4 |
| Added numeric occurrences | 7 | 0 |
| Text coverage | See prior pilot | 396/409 (96.82%) |

Table reading contains line-order/spacing mismatches and incomplete values; good numeric glyph recall does not prove pairing. Geometry table scoring was not rerun for this multilingual model. The prior English/server result of 98/103 is not relabeled as this model's result. Every adapter attempt starts a cold worker; median real-region time **3283.039 ms**, not warm per-page latency. CPU/RSS were not separately sampled. No Linux end-to-end run was available; macOS Apple Silicon CPU inference was actually executed, Linux CPU setup remains to validate.

Synthetic mixed EN/ZH fixture preserved exact Chinese, English, stats, skills and dice and passed repair validation (5343.664 ms cold). A CJK-font ASCII stress case introduced wrong/fullwidth values and was correctly rejected. Synthetic outcomes do not replace real evidence.

## Checks and reviews

- Full pytest: **1719 passed, 2 skipped, 152 subtests passed**, 20.92 seconds; nine existing warnings.
- `ruff check .`: passed.
- `mypy app`: passed, 125 source files (existing untyped-body informational note).
- `python -m compileall app tests`: passed.
- `git diff --check`: passed.
- Standards review: no runtime findings; one documentation numeric denominator correction fixed.
- Spec review: one P1 AI complete dice/percentage preservation defect fixed and regression tested; follow-up found no remaining blocking runtime findings.

Runtime files: `app/pdf_ocr.py`, `app/pdf_loader.py`, `app/pdf_quality.py`, `app/pdf_ai_repair.py`, `app/config.py`; setup/dependencies: `scripts/setup_pdf_ocr.py`, `requirements-pdf-ocr.txt`, `.env.example`, `README.md`; tests: `tests/test_pdf_ocr_recovery.py`, `tests/test_pdf_ai_repair.py`, `tests/fixtures/pdf_ocr/mixed_language.json`; removable validation helper: `scripts/experiments/validate_pdf_ocr_integration.py`; bilingual specs, catalog and these validation reports/JSON. Existing MarkItDown, Docling ordering, scene_map and gameplay modules retain their roles.

Recommendation: retain the implemented candidate/gate architecture, but do not describe this as proven improved production ingestion. Before rollout, validate unresolved image-only pages and actual map/provider routes, and confirm Linux CPU deployment. More aggressive acceptance would require new supported source evidence, not confidence thresholds or invented stat values.
