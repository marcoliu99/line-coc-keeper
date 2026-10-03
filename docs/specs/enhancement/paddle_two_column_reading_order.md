# Paddle two-column native-text reading order

Status: implemented following user approval of the design and public seams.

## Baseline and evidence

Branch from latest main_v2 `f98dcaa6da8a4cc49a4661b11425db33a53c60d7`, the merge of PR157 (head `e63337feb41a8166d03f22f533974e84945a245a`). Keep its OCR implementation unchanged.

Preserved five-page evidence: `/Users/marcoliu/workspace/paddle-layout-order-audit-20261003/`; subset SHA256 `362531220b2ad0d5d4479b6c5fed754de9a3660d6d1245727cd5127f0f4b322e`. PyMuPDF4LLM 1.28.2 passes 1/5; PP-DocLayoutV3 passes 4/5, improving all three real dual-column pages. The spanning-title three-column page still fails. Single-column is the unchanged control. This supports a narrow dual-column path, not general multi-column reliability.

## Goal and non-goals

Use Paddle regions and predicted reading order to arrange existing PDF native text only when the page is unambiguously two-column and every native line can be preserved exactly once. Otherwise execute the existing selection/repair/fallback behavior unchanged.

No whole-page Paddle OCR, OCR/SAN/dice correction changes, dependencies, interpreter downgrade, cross-interpreter workers, importer refactor, scenario library, admission/publication, reparsing, map/topology, /coc start, gameplay, combat/check/SAN rules, RAG, AI provider, PR155 states/gates or performance optimization. Single-column and three-column pages are not reordered by this adapter. Existing optional versions stay PaddleOCR 3.7.0, PaddleX 3.7.2, PaddlePaddle 3.3.0, CPU.

## Minimal module and integration

Proposed files: new `app/pdf_layout.py`, new `scripts/setup_paddle_layout.py`, a small call site in `app/pdf_loader.py`, formal tests/fixtures, `.env.example` and concise README setup documentation. Do not edit `app/pdf_ocr.py`, `scripts/setup_paddle_ocr.py`, OCR requirements or game/scenario/provider modules. No persistent schema change.

`pdf_layout` owns the optional layout predictor, local-model preflight, native-line mapping, conservative two-column validation and structured result. Its public page adapter returns accepted native text or fallback with a bounded reason and initialization/inference timing. It never mutates the PDF or returns OCR text.

```text
Current native / PyMuPDF4LLM candidate selection
-> optional local Paddle layout candidate
   -> accepted clear two-column candidate: use reordered native text
   -> every other outcome: retain the already selected original candidate
-> unchanged local repair / MarkItDown / AI repair / remaining vision stages
```

Invoke after the current native/layout evidence checks and before existing repair stages. An accepted candidate becomes the selected text with an explicit extraction method; existing evidence, pairs and repairs remain intact. Fallback does not add a gameplay/admission gate or new review warning. The returned tuple and existing import callers remain compatible.

## Explicit setup; no runtime download

Layout has its own persistent root: `PDF_PADDLE_LAYOUT_MODEL_DIR`, default `~/.cache/line-coc-keeper/paddle-layout`. Enable switch `PDF_PADDLE_LAYOUT_ENABLED` defaults true, consistent with PR157's optional local OCR: absent complete local model files means immediate fallback before Paddle import/rendering. A false switch guarantees the original path.

Setup explicitly downloads only the official PP-DocLayoutV3 archive, validates nonempty `inference.json`, `inference.pdiparams`, `inference.yml`, and installs through temporary staging. Use stdlib mechanisms following the existing OCR setup convention; no new package. Verify the official model URL during implementation. Runtime supplies `model_name='PP-DocLayoutV3'` and explicit local `model_dir`, CPU, MKL-DNN disabled and four threads; disable SDK model-host checks. Do not use SDK default model resolution or automatic downloads.

Lazy initialize once per configured model directory, serialize predictor use for thread safety, and log initialization time. Missing/incomplete files, unavailable package/interpreter, invalid model, empty/malformed prediction, initialization or inference errors all return fallback. Existing Python 3.14 cannot load official Paddle wheels in-process; it keeps current extraction just as PR157 does. Actual Paddle use requires the already supported Python 3.9–3.13 environment. Do not silently add a worker or claim that 3.14 gained Paddle support.

## Conservative acceptance

1. Capture nonempty native PyMuPDF lines, their original strings, stable IDs and PDF bboxes before layout inference. Render only the page for region analysis at 150 DPI. Paddle OCR detector/recognizer and full PP-Structure pipelines are never instantiated.
2. Validate predicted boxes/classes/orders and coordinate bounds. Exclude image/chart/seal/header-image/footer-image from text mapping. Reject severely overlapping competing text regions or ambiguous line assignments; duplicates or malformed order values do not become authoritative.
3. Map every native line to exactly one eligible region using measured pixel/PDF scale and intersection/native-line-area, with the evidence's 0.15 minimum. Missing or materially ambiguous mapping rejects the whole candidate; do not append unmapped lines and then accept. Source strings are never repaired, synthesized or replaced.
4. Establish exactly two separated body-column groups by horizontal overlap of body text-region intervals, with a visible gutter (initial minimum 2% of page width) and multiple native body lines in each. This is a conservative eligibility check, not a replacement reading-order algorithm. A spanning body region, one group, three or more groups, or unclear separation falls back. Do not reconstruct three columns. Header/footer/number/document-title roles do not define body columns.
5. Every body/within-column heading region must belong to one of the two groups. Permit a detected spanning title only above the body; preserve it before both columns. A spanning heading in the body is uncertain and falls back. Reject reading orders that return to the left column after entering the right column. Never override Paddle's predicted body order with a guessed column order.
6. Order accepted regions by Paddle order; within one region use y then x. Explicit header/footer roles without a body order are placed before/after body, as in the verified experiment. A title must precede both columns. Require complete left-column sequence then complete right-column sequence; mere token coverage is insufficient.
7. Verify all native IDs occur once, no unmatched or added lines exist, and strings/text tokens plus signed numeric/percent/dice and complete ordered SAN-loss expressions are retained. Preserve source case and punctuation. `1d6`, `1d10`, `1d4+2`, `SAN 1/1d6`, `50%`, `+20`, `-10` receive explicit regression coverage. Any failure returns the original candidate.

Thresholds are conservative defaults, not proof of arbitrary PDF semantics. Validate the three recorded dual-column examples against these checks; if a validated page cannot safely pass, report the conflict before widening acceptance or tuning to a page. Two-column fallback may retain a known original ordering error, but cannot prevent an otherwise accepted PDF from importing.

## Logging and timing

Logs contain only `layout=paddle status=accepted` or `layout=paddle status=fallback reason=<bounded_code>`, initialization time and per-page inference time. No native/PDF text, raw predictor results, exception message or traceback containing source content. Suggested reasons: disabled, model_unavailable, backend_unavailable, inference_error, malformed_result, not_two_columns, overlapping_regions, incomplete_mapping, ambiguous_mapping, invalid_order, content_mismatch. Timing measures initialization and inference separately; report live CPU observations versus prior 0.91–1.24 s/page without optimizing.

## Formal regression tests and agreed observation seams

Proposed public seams for user confirmation: `pdf_loader.extract_text` for final selected text/fallback behavior; the public pure native-lines + predicted-regions adapter in `pdf_layout` for captured-evidence order/eligibility checks; explicit setup entry point for local-model/no-download behavior. Do not test private implementations or call the model downloader in CI.

Convert all five preserved pages' native positions, model boxes and independently frozen expected order into compact repository fixtures, with provenance/hash/model versions. CI stubs only the external model boundary and asserts actual production ordering logic. Preserve the original three-column failure as an eligibility rejection, never as a repaired result. Also run scoped live CPU smoke on the same unchanged subset using explicitly prepared local weights; no scenario import or whole-book run.

Required cases:

- Ordinary two columns: left1/left2/left3 then right1/right2/right3.
- Large/spanning title: title then complete left then complete right.
- Complex skills/spells/illustration: complete column order, illustration ignored.
- Single column: original final text unchanged; no accepted Paddle ordering.
- Three columns: candidate rejected; original path unchanged, no claimed repair.
- Missing/disabled/incomplete model, absent package, initialization/inference error, no/malformed output: original path succeeds; no network/download call.
- Unmapped/duplicate/ambiguous lines, severe overlaps, interleaved column order: fallback.
- Exact words/line IDs, signed numbers, percentages, dice and complete SAN expression preserved; corrupt/missing candidate rejected.
- Lazy startup, explicit local paths, safe status-only logging and separate timing.

Use vertical TDD at the confirmed public seams: one failing behavior, minimal implementation, targeted checks, next behavior. Final checks: `pytest`, `ruff check .`, `mypy app`, `python -m compileall app tests`, `git diff --check`; two-axis code review. If an environment cannot run a command, resolve using the repo's existing dev dependencies or report the blocker honestly. Full suite once after implementation; broaden/repeat only for relevant changes or failures.

## Delivery and review decision

Report baseline/final SHA, exact file scope, each of the five page outcomes, every fallback case, text/number/dice/SAN conservation, model initialization/page timing, all requested checks and MERGE READY/HOLD. Commit/push implementation to `enhancement/paddle-two-column-reading-order`; do not open a PR without an explicit request.

The user approved this spec and its public seams with “開始實作”. Layout remains optional and local-only; Python 3.14 retains fallback without a new Paddle execution mechanism.

## Implementation validation

The production adapter matches independently frozen expected text on all three existing real two-column pages; single- and three-column pages fall back. CI exercises captured native lines/model regions and the actual extract_text integration, including missing models/packages, initialization/inference failures and incomplete mapping. Inverted in-column order and clearly parallel prose within a merged region are safety vetoes only, never three-column reconstruction.

The live CPU smoke blocks socket connections: five pages, zero network attempts. Weight hashes match the prior A/B model. See [results](paddle_two_column_reading_order_results.json). OCR and gameplay code are unchanged.
