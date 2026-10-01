# PR155 graph correctness follow-up

Production rollout remains **hold**. Production code and regression tests are implemented; the new provider-enabled map and image-only experiments have not run. Automatic approval review rejected uploading real PDF-derived images to the configured OpenAI API (`api.openai.com`) without explicit payload/destination approval. No alternate transport or provider was attempted.

## Changes

- `app/scene_map.py`: total structural validation with unique legal IDs, required existing entry, valid local/cross-map target syntax and compass; diagnostics for duplicate/self/conflicting/asymmetric edges. One-way edges remain legal. No movement runtime changes.
- `app/pdf_map_analysis.py`: separate NOT_ANALYZED / ANALYSIS_FAILED / GRAPH_MISSING / INVALID / INCOMPLETE / VERIFIED states. A structural pass is followed by an original-image inventory of locations and an audit of every room/exit/entry. Missing visible labels remain incomplete; image evidence of wall crossings or unsupported nodes/edges is invalid. The provider audit is evidence, not independent human ground truth.
- Repair is bounded to one attempt per analysis, within the existing shared durable request budget. It receives the original image, graph and deterministic/image-audit errors; cannot change source transcription or invent rooms/entrances. It is revalidated and re-audited. Previous audit labels cannot be erased to pass. Unknown entry retains a blocked private draft.
- `app/pdf_loader.py`, `app/scenario_library.py`: unverified candidates stay private; graph/image/PDF-bound certificates are checked before gameplay library persistence and draft cache reuse. Map descriptions stay separate from canonical source text. Pipeline identity is `multicolumn-v6`.
- `app/pdf_ingestion_drafts.py`: graph attempts and their outputs/errors/timing/hashes survive continuation in private provenance history.
- OCR metrics include page attempts and independent agreements/conflicts. Map reports contain candidate/attempted/failed/generated/invalid/incomplete/repaired/verified counts.
- CPU smoke uses hash-pinned RGB raster fixtures with font/DPI/size/antialiasing provenance, actual offline-worker network-denial probes, a neutral inference positive and a separate exact-mechanics rejection test. macOS Apple Silicon is the required production gate; Linux is optional portability evidence.

## Real-map evidence

| Page | User-confirmed earlier result | Current-code provider result |
| --- | --- | --- |
| The Haunting p7 | 20 rooms / 44 directed exits; missing entry; unsupported basement solid-wall exit | Not run: final rooms/exits/entry, repair count and Corbitt wall correctness unverified |
| Lightless Beacon p16 | 9 rooms / 15 directed exits; dangling Lamp Room stairs target; Service Room, Lamp Room, Lantern Gallery missing | Not run: all three locations and stairs topology unverified |

Current-code real map status counts are unavailable, not zero or mock-derived successes. `pdf_map_correctness_results.json` explicitly records this. Tests prove structural rejection, bounded successful/failed repair, wall-evidence rejection, missing-location rejection and no invalid graph publication; they do not establish correctness of either real map.

## OCR evidence and gates

| Check | Result | Scope |
| --- | --- | --- |
| macOS Apple Silicon CPU positive | PASS; exact neutral candidate, offline cached model, actual worker socket calls denied | Required platform gate; synthetic fixture, not real-page acceptance |
| macOS mechanics safety | PASS; actual `ld6+2` rejected | Numeric/dice gates unchanged |
| Linux x86_64 CPU positive | PASS | Optional portability; GitHub run 36861007223, commit 403b339 |
| Linux mechanics safety | PASS; actual `ld6+2` rejected | Same pinned PNGs as macOS |
| Historical controlled Paddle OFF/ON | 102 real pages; review 50/50, blocked 48/48; no safe-page regression | Prior `multicolumn-v5` experiment, preserved unchanged |
| Final-code targeted OFF/ON | 9 previously-safe real pages; accepted 9/9 in each arm; identical selected-text hashes; numeric/dice/pair errors all zero | `multicolumn-v6`, provider OFF, hidden OCR OFF, Docling OFF in both arms; not a full-corpus repetition |
| Image-only agreement/conflict/local-only | Regression tests PASS: authoritative / unverified / unverified | Provider mocked; not new real authoritative evidence |
| Pure illustration | Regression PASS; diagnostic Tesseract noise does not block | Existing real Beacon p5 history preserved; current-code provider recheck pending |
| Real image-only authoritative positive | PENDING | Prepared Haunting p19 whole-page probe plus hash-bound Dead Boarder p17 real crop. A crop success would prove only that bounded derivative, not the complete original page |
| Real image-only conflict | Previous Haunting p20 correctly unverified | Current-code provider rerun pending; no safety relaxation |

Targeted sample: Haunting 3/6/12, Dead Boarder 5/6/11, Beacon 6/10/15. `scripts/experiments/validate_pdf_safe_page_sample.py` reproduces this isolated subset and writes only sanitized metrics publicly. Raw source text, candidate graphs and provider responses remain private. Neither controlled evaluation publishes scenarios.

## Verification

Local: **2039 passed, 2 skipped**; Ruff passed; mypy passed (132 source files); `python -m compileall app tests` passed; `git diff --check` passed. GitHub implementation CI 36861007088 passed. Existing provider architecture, Paddle model/safety gates, Docling OCR/table flags and hidden OCR policy are unchanged.

## Standards

Review found one documented stale function-reference violation; fixed to `_resolve_map_action_core`. Two heuristic maintainability observations remain: repeated legacy-visibility fixture setup and broad dictionaries for untrusted audit/provenance records. They are not correctness failures or tooling violations.

## Spec

Review found no material implementation mismatch under the explicit required-entry rule and latest platform override. Real provider evidence remains pending. No mocked result is used to claim rollout readiness.

Review totals: Standards one documented violation fixed and two heuristic observations; Spec zero material findings. Production rollout **hold** until both real maps are verified, Corbitt's wall exit is absent, Beacon's three locations/stairs are verified, and a real image-only authoritative positive succeeds.
