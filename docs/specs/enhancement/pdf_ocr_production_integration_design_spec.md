# Import-time PaddleOCR recovery on PR155

[繁體中文](pdf_ocr_production_integration_design_spec_zh.md)

Status: implementation authorized by the operator invoking `implement`; local runtime implemented, final validation/review in progress.
Branch: `enhancement/pdf-multicolumn-ingestion` (PR #155, targeting `main_v2`).
Baseline: `ead28a4ac4ab7a7b7f9829cd2e2462fc3b2c4ef6`.
This extends the existing multicolumn ingestion spec; it does not replace its ordering, map or durable-draft contracts.

## Goal and evidence boundary

Import quality first, gameplay runtime simple. Make `PP-OCRv5_mobile_rec` the first local OCR candidate generator on only suspect regions and genuinely low-text/scanned pages. Retain Tesseract, MarkItDown OCR and bounded AI recovery. Native evidence and deterministic rules decide publication, never OCR confidence.

The separate benchmark branch `enhancement/pdf-ocr-benchmark`, commit `9f48775`, preserves the completed offline evidence. Thirteen real pages / twelve verified regions were scored. English mobile and server recognizers each preserved 98/103 numeric occurrences versus production Tesseract 50/103; both preserved 6/6 strict dice tokens and 4/4 skill pairs. Raw text pairing remained 34/50 versus 22/50. Actual-box table pairing was 16/16 versus 1/16. These are bounded pilot results, not proof of global safety. The multilingual `PP-OCRv5_mobile_rec` selected by the operator has not yet been measured in that run. Its real production validation is a release gate. Preserve the prior report without relabeling its English model as multilingual.

## Existing seams and constraints

`pdf_loader._repair_local_regions()` selects blocks with replacement glyphs or unresolved native pairs, pads by two points and renders at 300 DPI. Its region budget currently counts crops. It replaces only a unique original block after `pdf_quality.accept_region()`; that gate intentionally permits observable glyph repair, not arbitrary rewriting or speculative completion of a clean ambiguous table.

`accept_region()` currently requires intact-word preservation, exact numeric multiset and resolved pair checks. Its `_NUMBER` grammar does not cover full `1d10+DB` or reliably retain a trailing percentage sign. Add explicit complete-dice and percentage preservation checks beside the existing contract, with no relaxation of crop, DPI or thresholds. Do not redefine all numeric-pair geometry.

Low-text graphic pages currently use MarkItDown and the combined scene-map image analyzer. Map candidates are held independently of successful transcription. `review_reasons` already separates some candidate-only warnings from actionable defects. Identity currently includes parser/renderer/quality/layout versions; accepted caches require complete identity equality.

## Responsibilities and routing

- Reading order remains PyMuPDF geometry, deterministic layout, then Docling only for ambiguous difficult native pages. Keep `do_ocr=False`, `do_table_structure=False`, remote services disabled, and validated permutations of existing native block IDs. No model replacement prose.
- Damaged/suspect native blocks: unchanged crop -> Paddle candidate -> deterministic gate -> Tesseract candidate if rejected, empty, unavailable or failed -> the same gate -> existing AI region recovery -> unresolved/review. Clean numeric ambiguity remains unresolved unless existing source-evidence rules can certify the candidate. Confidence cannot guess pairing.
- Scanned/very-low-text graphic pages: local Paddle/Tesseract candidates before the existing difficult-transcription fallback; retain source text and resolved numeric/pair/dice evidence. Without independent evidence for critical numbers, record OCR as unverified and retain review. Agreement between engines alone is not source authority. Do not claim that image pixels are numerically verified merely because an engine saw them.
- Failed local recovery retains MarkItDown OCR and `pdf_ai_repair` according to their existing page/region eligibility, with deterministic arbitration; do not run all engines for every page or duplicate whole-page transcription. AI is a transcription candidate, not an LLM judge.
- Maps: graphic/map indication -> rendered page -> existing `scene_map.analyze_page_image()` -> spatial graph. A map candidate remains queued even if Paddle, Tesseract, MarkItDown or AI supplies enough text. Keep `floor_plan_graph_missing`, blocked publication and existing graph/prose separation.

## Small adapter and runtime lifecycle

Add `app/pdf_ocr.py` only for local engine invocation and typed attempt/provenance records. The PDF loader owns source evidence, candidate validation and replacement. Iterate candidates lazily so rejected Paddle output still reaches Tesseract, and accepted Paddle avoids unnecessary Tesseract work. Preserve the existing Tesseract language order, pytesseract path and CLI fallback. A compatibility `_ocr_image()` must not become an acceptance shortcut for an ingestion caller.

Use a bounded CPU worker with explicit local model directories. Optional heavy imports happen only after eligibility/model checks. Initialization/inference failure, corrupt artifacts, missing dependency, missing model or worker timeout records failure and proceeds to Tesseract; it never crashes the whole import or initiates a download. Bound execution and reap workers; do not retain an unbounded background inference after fallback. Keep the adapter small, with no plugin registry or language-routing framework. OCR worker version inspection must reflect the actual worker interpreter, not merely the bot interpreter.

## Configuration, dependencies and explicit setup

Use existing config parsing conventions:

- `PDF_OCR_PADDLE_ENABLED=true` (enabled by default only after the release gate passes).
- `PDF_OCR_PADDLE_MODEL=PP-OCRv5_mobile_rec`, allowing explicit operator override without per-page language routing.
- `PDF_OCR_PADDLE_MODELS_PATH`, default persistent `DATA_DIR.parent / models / paddleocr`; never `/private/tmp` for production artifacts.
- `PDF_OCR_PADDLE_DEVICE=cpu`; reject unsupported device configuration safely. No GPU autodetection or required CUDA packages.
- A finite local worker timeout. If an independent environment is required, provide an explicit worker-Python path rather than guessing an executable.

Separate `requirements-pdf-ocr.txt`, pinning measured PaddleOCR 3.7.0, PaddlePaddle 3.3.0 and PaddleX 3.7.2. Keep minimal bot dependencies and existing layout requirements unchanged. The bot development interpreter is Python 3.14; the measured Paddle CPU environment is Python 3.11. Explicit `scripts/setup_pdf_ocr.py` must support a compatible persistent environment and clearly fail unsupported Python/platform setup rather than pretend success. Record actual package/model/CPU/platform identity and a model manifest with file digests.

Setup is an operator action: install pinned optional dependencies in the selected compatible environment, download recognizer plus required detector to persistent configured storage, verify artifacts, and print exact config/setup requirements. Runtime supplies local directories for every model used, disables model-source checks/remote retrieval paths, and fails closed to fallback if artifacts are incomplete. No bot startup download, ingestion download or cloud OCR API. Validate runtime under denied-network execution. Temporary PNG inputs may use secure temporary files; the production model cache may not.

The shared detector and recognition resize settings must be explicit, reproducible and recorded. Use the benchmark's bounded CPU detector settings as the initial adapter setup; model choice alone does not make detection cost or results identical. Do not alter production crop/DPI or deterministic thresholds in this change.

## Provenance, warnings and budgets

Extend each existing `local_repairs` record without removing `original`, bbox/crop, `ocr_text`, pair checks or final region status. Add an ordered `ocr_attempts` list containing engine, model, candidate, status (`accepted`, `rejected`, `unavailable`, `error`, `empty`), reason, pair checks and optional diagnostic confidence/runtime. Example: Paddle rejected -> Tesseract accepted must retain both records; the region final status is accepted. Record page OCR attempts similarly for scanned routing. Full transcription stays in private ingestion artifacts, never in committed corpus reports.

Attempt diagnostics remain immutable audit evidence. Recalculate final review reasons from the selected page and final region/AI/map outcomes. Rejected Paddle numeric mismatch is not an unresolved page when Tesseract safely repairs it. Conversely, do not clear a warning just because an adapter ran. Preserve `warnings` compatibility and expose resolved/remaining defect accounting without hiding failed repairs.

The existing local crop budget remains unchanged and counts one suspect region, not each engine. Add separate `local_paddle_attempts`, accepted/rejected/failure counts, `local_tesseract_attempts` and fallback/accepted counts. Local OCR must not consume Docling/provider image budget, paid vision count or AI repair request budget. Existing AI and layout budgets remain bounded and resumable.

## Extraction identity and version

Advance extraction `PIPELINE_VERSION` from `multicolumn-v3` to `multicolumn-v4`: OCR priority, validation, provenance and resume semantics have materially changed. Preserve renderer/layout versions unless their own algorithms change. Advance quality identity if adding the complete dice/percentage guard changes quality semantics.

Add an `ocr` identity with enabled flag, configured primary/model/device, adapter/pipeline version, actual PaddleOCR/PaddlePaddle/PaddleX worker versions or explicit unavailable state, plus local model-manifest digest and Tesseract identity where available. Model corruption/missing artifacts must not appear identical to ready artifacts. Paths are setup locations; digests identify content.

Keep the existing conservative whole-identity equality policy. Old accepted Tesseract pages are reprocessed under the new identity, including native pages. Within an unchanged new identity, unaffected accepted native pages may still resume without parser/provider work. Do not introduce cross-identity native-page reuse in this integration. Continue/Status/Cancel, leases, cumulative provider budgets and durable failed drafts retain existing behavior.

## Files and non-goals

Expected runtime changes: `app/pdf_ocr.py`, `app/pdf_loader.py`, `app/pdf_quality.py`, `app/config.py`. Setup/docs: `scripts/setup_pdf_ocr.py`, `requirements-pdf-ocr.txt`, environment/setup documentation and these bilingual specs/catalog. Tests: dedicated adapter tests plus loader, numeric pairs, AI, multicolumn, draft/resume and configuration regressions. Add a removable offline corpus validation helper and sanitized JSON/Markdown evidence.

Do not remove MarkItDown OCR, `markitdown_shim`, pytesseract or CLI Tesseract. Do not modify Keeper, combat, agents, tool routing, RAG, dice/NPC/narration runtime or `scene_map` semantics. Do not add Camelot, Surya, Azure or JEV. No whole-book Paddle iteration, new parser architecture, LLM judge or page-language routing. This does not authorize production document uploads to a new external service; existing bounded provider routes retain their ownership and configuration.

## Verification and release gate

Regression tests must cover bilingual Investigator/調查員, Chinese/English labels, exact STR/DEX/SAN/幸運 numbers, skill percentages and `1d6+2`, `2d6`, `1d4`, `1d10+DB`; swaps and `ld6+2` rejection; unsupported HP completion; intact source prose preservation; rejection then Tesseract acceptance; dependency/model/init/inference failures; both engines failing into existing MarkItDown/AI/unresolved routes; candidate warning isolation; map analysis despite successful OCR; separate budgets; offline runtime; identity changes and same-identity resume; Continue/Status/Cancel.

Run full pytest, `ruff check .`, `mypy app`, `python -m compileall app tests`, `git diff --check`. Minimal CI tests mock engines without requiring heavyweight Paddle installation. Explicit CPU smoke tests use real models under denied network on macOS; document Linux CPU dependency constraints and distinguish actual Linux execution from unexecuted compatibility expectations.

Repeat the same local real CoC corpus against baseline and new route with matching dependencies, configuration, budgets and provider availability. Include The Haunting, The Lightless Beacon and Dead Boarder, golden multicolumn pages, scanned character sheets, stat/skill/dice tables and known numeric/glyph-warning pages. Keep synthetic evidence separate. Record per-PDF identity hash, physical page and before/after `review_pages`, `blocked_pages`, `source_pair_unresolved`, `numeric_pair_review`, `local_ocr_review`, `ai_fields_unresolved`, `floor_plan_graph_missing`, engine attempts/acceptances/rejections/fallbacks and AI requests. Preserve floor-plan graphs separately from OCR text. Do not claim full provider/map validation from an offline-only run.

Quality priority: exact numeric values, label pairing, dice, skills, source wording, order, coverage, then speed. Save only hashes/pages/metrics/errors/timing and minimal diagnostic values in committed evidence; no copyrighted PDF bytes, whole pages or prose.

If real evidence shows numeric/dice/pairing/source-preservation regression, keep evidence and tests but retain the prior default path; explicitly report the failed release gate. Do not force-enable Paddle to declare completion. Remaining unverified/unresolved pages stay visible and resumable. Final report must enumerate architecture, files, versions/model/device/setup, exact fallbacks/gates/provenance/identity/version, per-book before/after, numeric warnings and counts, maps/unresolved pages and all verification results.

## Review decisions

The requested model, fallback order and offline policy are fixed by the operator. Implementation needs confirmation of this concrete spec under the repository branch/spec workflow. The persistent compatible worker environment, conservative identity invalidation, and review treatment of scanned critical fields are explicit implementation choices. No claim is made that the previous English-model pilot validated the new multilingual production default.


## Implementation checkpoint

The adapter uses a short-lived bounded CPU subprocess per Paddle attempt, rather than importing Paddle into the bot. Initialization is included in each measured attempt. The worker interpreter is explicit and actual package versions are part of identity. Region padding/DPI/region allowance remain unchanged; low-text page OCR has its own finite allowance equal to the configured local OCR limit, recorded separately.

Complete dice/percentage checks and intact token-order preservation close observable acceptance gaps. A source region must be uniquely replaceable before an attempt is marked accepted. If the selected source already contains a region that passes this same deterministic repair gate, skip stale OCR work. Unverified visual transcription remains derived/private evidence; unsupported critical mechanics block publication. A valid map graph survives an empty/rejected description, without weakening source validation.

Platform and full-provider limitations will be documented in the validation report; local denied-network corpus evidence must not be described as a successful paid-vision/map import.

Final validation is recorded in [validation](pdf_ocr_production_integration_validation.md). The complete-expression guard also applies to AI repair. PyMuPDF4LLM hidden OCR is explicitly disabled so all local OCR follows the adapter contract. Setup's supported worker baseline is Python 3.11–3.13. Implementation authorization was provided by the operator's `$implement`; local integration is complete, deployment limitations remain explicit in validation.
