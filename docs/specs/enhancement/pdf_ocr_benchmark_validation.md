# Local OCR comparison — 2026-10-01

[繁體中文](pdf_ocr_benchmark_validation_zh.md) · [Machine-readable evidence](pdf_ocr_benchmark_results.json)

**Outcome C: PaddleOCR warrants a restricted challenger for image-based characteristic tables. Keep Tesseract primary globally.** This is an integration proposal, not a production change or a general character-sheet certification. Neither recognizer is selected for production: English mobile and server tied on the measured critical quality. The server was explicitly tested, not excluded because mobile is a default.

## Inputs and correctness

Baseline main_v2 `189bc8e`; separately inspected/tested PR155 `ead28a4`. Read pdf_loader, pdf_quality, pdf_ai_repair, PR155 layout/adapters, markitdown_shim, scene_map and associated PDF tests/reports. Docling remains ordering-only with OCR/table extraction disabled. Benchmark never imports extract_text or provider lookup.

13 physical pages from the supplied originals: Haunting **11,14,20**; Dead Boarder **3,5,17,20,31**; Lightless Beacon **4,6,13,27,34**. All engines completed the same **26 inputs**: 13 full pages and 13 regions. Four regions reproduce production suspect selection on the two actual replacement-character warning pages (Dead 3, Beacon 4); they are contents headings. Other regions are explicitly representative numeric crops or image-table crops. Selection is limited, not a whole-book benchmark. Inputs were manifested before inference; visual gold was reviewed independently before headline scoring, without selecting on engine results.

Identical 300-DPI PNGs go to all engines. Native suspect/representative crops use existing bbox padding (-2,-2,+2,+2), intersected with page.rect. Two manually selected scanned-table crops are separately labeled. The first two suspect blocks per warning page are sampled, not every repair block. Golden Haunting 14, Dead 5 and Beacon 6 are included. Maps are text-only secondary controls, never map-graph evidence.

Only **12 visually reviewed bounded regions** count toward headline correctness. Native full-page references are unverified candidates; image pages lacking native text have null reference metrics. No whole-page sentence-hallucination claim is made. PDFs, gold transcriptions, PNGs, raw OCR and process samples remain private outside the repo.

| Verified region measure | Tesseract | English mobile | Server |
|---|---:|---:|---:|
| Exact numeric tokens | 50/103 (48.54%) | 98/103 (95.15%) | 98/103 (95.15%) |
| Existing raw pair validator | 22/50 (44%) | 34/50 (68%) | 34/50 (68%) |
| Exact original-spelling dice | 5/6 | 6/6 | 6/6 |
| Skill label + percentage | 4/4 | 4/4 | 4/4 |
| Lexical coverage | 77.26% | 98.53% | 94.13% |
| Added numeric occurrences | 7 | 0 | 0 |
| Main-stat geometry pairs (two tables only) | 1/16 | 16/16 | 16/16 |

The headline numeric supplement only separates known adjacent labels (`SAN1` → `SAN 1`); it never repairs glyphs, leading zeros, values or dice case. Existing `_NUMBER` scores are also retained: **38/103, 98/103, 97/103** respectively. `SAN1` is a raw parsing/spacing failure, not `1→I`. `numeric_pairs`, `check_pairs`, `_NUMBER`, `_WORD`, normalization and `accept_region` are reused. Scalar/percentage and complete dice diagnostics supplement their known limitations. Casefold dice is separate: all engines preserve 6/6 under that sensitivity metric.

The strongest isolated class is the two image-based characteristic tables: numeric **6/54 → 50/54**, and actual-box geometry main-stat pairs **1/16 → 16/16**. Geometry uses real OCR single-token boxes mapped to PDF points and the existing owner; multiword segments are excluded rather than assigned invented word boxes. Raw text-pair scores remain separate: Paddle's line-separated table tokens still fail raw checks. Both Paddle models omit four faint +1/-1 age-modifier occurrences; Tesseract also misses them. This is not complete table recovery. Ordinary added words are lexical diagnostics, not automatically called invented sentences. Negation checks are retained; blank-Luck, stat/skill swaps, substitutions and extra dice are synthetic metric tests, not real-corpus wins.

## Page outcomes and failures

Both challengers have the same **raw region** classifications: wins on Dead 3/17/31, Beacon 4, Haunting 14; ties on Haunting 11 and Beacon 13; mixed on Beacon 34 (more numbers, worse raw pairing); incomparable on Haunting 20, Dead 5/20, Beacon 6/27 because no scored region exists there. No Tesseract wins or both-failed pages in this selected scored comparison. Supplementary geometry confirms both table wins on Dead 31 and Beacon 34. There are no worse added/matched numeric totals than Tesseract on the verified regions; this is not proof that every individual field occurrence improved. JSON includes each input, scope, denominators, missing/added counts, per-page classification and regression list.

An initial mobile run with default detector limits (min 64, max 4000) produced no completed four-repeat first-page artifact after at least 246 seconds and was interrupted (recorded exit -4; do not infer a dependency incompatibility). The replay with explicit detector max-side 960 completed. A nested sandbox launch initially failed (71); corrected launches enforced OS network denial. Neither failure was renamed a successful OCR call.

## Setup and cost

[PaddleOCR 3.7.0](https://pypi.org/project/paddleocr/3.7.0/), PaddleX 3.7.2, [PaddlePaddle 3.3.0 / official macOS CPU setup](https://www.paddlepaddle.org.cn/documentation/docs/en/install/pip/macos-pip_en.html), isolated Python 3.11, macOS arm64. This differs from bot Python 3.14; Linux/CI execution and deployment compatibility are not demonstrated. Bot requirements are untouched.

Models: shared PP-OCRv5_server_det, en_PP-OCRv5_mobile_rec and PP-OCRv5_server_rec. Explicit model directories determine language; Paddle warns that requested `lang=en` is ignored with explicit model names. English/mobile versus multilingual/server is recorded. CPU/native backend, MKLDNN off, 4 threads, recognizer batch 6; orientation, unwarping and textline orientation disabled. Detector max-side 960 is part of the candidate configuration and may trade recall for speed; no accuracy claim is made for its defaults.

Tesseract 5.5.3 uses production `_ocr_image` behavior: pytesseract default PSM, chi_tra+eng then eng; existing CLI fallback uses PSM 6. The initial corpus run used the equivalent pytesseract calls; the added warning/table run executes the exact trusted helper via AST isolation to avoid importing provider-owning code. Both language traineddata hashes are recorded.

Warm region medians: **264 / 1363 / 1226 ms**; full-page medians **2796 / 15599 / 12985 ms** (Tesseract/mobile/server). Initial model construction about **2.1–2.4 / 3.3–3.9 s** for mobile/server. First inference and all three warm repetitions are separately saved per input. Peak worker RSS **0.12 / 6.96 / 8.53 GiB**; CPU samples are in JSON. Fixed engine order and overlapping runs confound performance comparisons: these are costs, not a throughput winner. Additional geometry probes are separate from these timings.

Models were downloaded only during explicit setup, without document inputs: archives total **181,217,280 bytes**; hashes, download durations, model directories and expanded-cache locations are in JSON. Isolated venv is about 1.0 GiB; retained archives plus expanded models about 358 MiB. Cache: `/private/tmp/coc-ocr-models`; private run data: `/private/tmp/coc-ocr-private`, `/private/tmp/coc-ocr-extra`. They are local temporary artifacts, not repository assets. Preserve/move them for future reproduction before system cleanup.

## Reproduction and checks

Use isolated Python 3.11 and `scripts/experiments/requirements-pdf-ocr-benchmark.txt`. Run `setup_pdf_ocr_benchmark.py --model-cache PATH` explicitly; it downloads public models only and accepts no document argument. `benchmark_pdf_ocr.py prepare --corpus PATH --private-dir PATH` creates the selected native/full-page manifest; manually reviewed image-table crops and gold overlays stay in private manifests. Gold overlays bind to exact input image hashes. `run` launches bounded workers under `/usr/bin/sandbox-exec` with `(deny network*)`; a platform without that launcher fails closed. For geometry use the same sandbox with `geometry --engine NAME`. Generate the report with `python -m scripts.experiments.report_pdf_ocr_benchmark --private-dir PATH` (repeat for each private run), `--model-cache PATH --outcome C --output PATH`; final engineering annotations are editorial additions to raw computed evidence. Do not run OCR workers outside network denial. No cloud OCR, model auto-download during inference, telemetry upload or bot-startup hook is added.

Final gates: **1617 passed, 2 skipped, 152 subtests passed**; Ruff 0.16.8 `check .` passed; `mypy app` passed (121 files); compileall app/tests/scripts/experiments and diff check passed. Separately tested PR155: **1689 passed, 2 skipped, 152 subtests passed**. These are different revisions and are not combined. The 14 new synthetic unit tests exercise metrics, paired denominators, worker failure isolation, image-bound gold and report redaction; they do not count as real OCR pages.

## Minimal follow-up proposal (not implemented)

Keep Tesseract primary. Add an optional, locally cached Paddle adapter behind `_ocr_image` only for an explicitly identified characteristic-table crop. Preserve OCR boxes; require deterministic numeric/geometry pair validation against available source evidence, then Tesseract fallback. If source evidence cannot establish complete fields, retain an unresolved draft/manual-review result; do not infer missing age modifiers or blank Luck. Existing graphic pages currently use scene_map/vision with `_ocr_image` only as text fallback; these measured crops do not prove an automatic production crop-selection path. Eligibility and complete-field validation need a separately reviewed follow-up.

Likely files: optional import-only local adapter, pdf_loader call boundary, pdf_quality validation interface if geometry transport needs a public seam, config plus explicit setup, and PDF loader/numeric/timeout/offline-model tests. Keep markitdown-ocr, pdf_ai_repair, Docling ordering (do_ocr=False/do_table_structure=False), scene_map, gameplay/RAG/combat/Keeper/tool routing intact. No Camelot; no Tesseract deletion; no production default switch in this work.

The reproduction runner now has an outer-process watchdog: 120 seconds per initialization/page (all four repetitions), 1800 seconds per engine, terminate/kill the process group and reap on timeout. The original long default attempt retains its historical manual-interruption failure, not a retroactive timeout success. Use `run_geometry` for the same bounded network-denied geometry wrapper. Gold pairs also include visually confirmed Move/Damage bonus/Magic points labels through the existing check_pairs interface; application vocabulary is untouched.

## Tesseract sensitivity (separate from the baseline)

Same 13 region PNGs, one pass per setting: chi_tra+eng/PSM6 gives numeric **52/103**, raw pairs **20/50**, added numeric **2**; eng/PSM6 gives **54/103**, **22/50**, added numeric **3**. Both yield **0/16** table geometry pairs. This rules out these two simple changes as an explanation for all observed Paddle gains; it does not exhaust sparse-page modes, preprocessing or Tesseract tuning. No production setting changed, and one-pass timings are not mixed with warmed medians. JSON retains all sensitivity rows. Reproduce under the same network-denied wrapper with `sensitivity --private-dir PATH --other-private-dir PATH --language eng` (or chi_tra+eng).
