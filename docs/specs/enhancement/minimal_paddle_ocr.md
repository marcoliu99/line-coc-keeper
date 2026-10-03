# Minimal local PaddleOCR backend

Baseline: reverted PR155 `main_v2`, `7d2cc8c`.

## Contract and scope

Add only an optional local OCR backend at the existing `_ocr_image` seam. Native extraction, MarkItDown, AI/vision, map handling, importer review/publication and gameplay remain unchanged. Paddle never adds a gate: disabled, unavailable, incomplete models, initialization/inference failure, empty or rejected output execute the pre-change Tesseract fallback without new review reasons.

The public seams under test are `pdf_ocr.recognize_with_paddle(image_bytes, source_text=..., pairs=...)`, existing `pdf_loader.extract_text`, and the setup command. These are the user's requested adapter/import/setup seams. SDK inference and existing external providers are fixture boundaries, never a publication judge.

A thin result holds text, engine and a closed accepted/rejected/unavailable/error/empty status. Lazy singleton initialization and inference share one lock, preserving existing worker threads. Explicit CPU, PP-OCRv5_mobile_rec multilingual recognition and PP-OCRv5_mobile_det line detection; orientation/unwarping models disabled. No worker/queue/service.

## Offline setup and dependencies

Optional `requirements-pdf-ocr.txt` pins PaddleOCR 3.7.0, PaddlePaddle 3.3.0 and compatible PaddleX 3.7.2. Existing requirements and unrelated dependency versions do not change. Official wheels support Python 3.9–3.13 on macOS arm64/Linux x86_64; the repository's Python 3.14 retains the unchanged fallback. No application Python downgrade or cross-interpreter worker is added.

Explicit installation on a supported interpreter:

```sh
python -m pip install -r requirements-pdf-ocr.txt
python scripts/setup_paddle_ocr.py
```

Setup alone downloads the two official models to a persistent directory outside the repository by default. Runtime requires complete model files and passes explicit local paths; it skips model-host connectivity checks and never requests automatic model selection/download. No Paddle import/model initialization at app startup. A disable switch allows controlled OFF/ON comparison.

## Candidate selection

Nonempty bounded text, no replacement-character damage or obvious malformed dice, and source-preserving sanity checks when native/source evidence exists. Reuse existing pdf_quality text/numeric and known pair checks, plus exact dice/percent/signed-token preservation at the adapter boundary. Uncertain Paddle output falls back, without changing acceptance criteria for the legacy fallback. Existing selected source/pairs are passed to both region and whole-page local OCR; legacy source validators remain unchanged. Damaged-region candidates preflight the existing accept_region gate so they cannot preempt a working Tesseract repair.

SAN losses compare as complete ordered `left/right` expressions, including fixed numbers and dice on either side; whitespace and dice-letter case do not change their meaning. With matching source evidence, an OCR `l` or `I` may become `1` only at a whole die-token boundary, or as the fixed `1` before a SAN slash. A mismatched face count, absent source evidence, or changed slash/order remains rejected. Ordinary prose is never rewritten.

No confidence fusion, cloud calls, new admission states or new provider paths. Normal logs include only engine/status/type metadata, never OCR prose or raw errors.

## Validation

TDD adapter/fallback integration; disabled/native equivalence, missing models/packages, init/inference/empty/malformed results, exact mechanics/pairs, lazy startup and offline no-download. Small CPU synthetic/plain scan/stat/bilingual smoke only; no maps, corpus imports or gameplay. Controlled OFF/ON reports characters, exact numeric/dice preservation and timing without changing admission. Full pytest, ruff, mypy, compileall, diff-check, Standards/Spec review.

Compatibility limitation: Paddle cannot run in-process on current Python 3.14 official wheels. A supported-interpreter application can opt in; 3.14 continues existing OCR. Optional Linux inference is reported only if actually executed; wheel availability is not a smoke PASS.

## Completed validation

See [validation](minimal_paddle_ocr_validation.md); local suite and scoped two-axis review pass. No runtime/admission/map architecture changes.
