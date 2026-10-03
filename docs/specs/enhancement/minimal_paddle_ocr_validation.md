# Minimal PaddleOCR validation

Baseline reverted main_v2 `7d2cc8c`; implementation `ca20c5b`. Independent work branch: `enhancement/minimal-paddle-ocr`. No PR155 code was cherry-picked.

## Implemented scope

Production modules: new thin `app/pdf_ocr.py` and small OCR evidence/selection hooks in `app/pdf_loader.py`. Optional dependencies, one explicit setup script and OCR tests. Existing requirements, Tesseract implementation, provider calls, native extraction, importer admission/publication, map logic and gameplay are unchanged.

PaddleOCR 3.7.0 / PaddlePaddle 3.3.0 / PaddleX 3.7.2 use CPU mobile detection plus multilingual PP-OCRv5_mobile_rec. Model paths are explicit, orientation/unwarping off, no model acquisition at runtime. Lazy singleton and inference share a lock. The user confirmed optional in-process operation on supported Python; no cross-version worker. Python 3.14 preserves the old OCR fallback. [Published PaddlePaddle wheels](https://pypi.org/project/paddlepaddle/3.3.0/#files) include macOS arm64 and Linux x86_64 through Python 3.13. Linux inference was not executed; availability is not a smoke PASS.

On a supported application interpreter:

```sh
python -m pip install -r requirements-pdf-ocr.txt
python scripts/setup_paddle_ocr.py
```

Default model cache: `~/.cache/line-coc-keeper/paddleocr`. `PDF_PADDLE_MODEL_DIR` selects another persistent directory; `PDF_PADDLE_OCR_ENABLED=false` disables Paddle. The setup script was actually run with a private temp model directory and downloaded both official models. Prepared setup replay makes no download. The pinned SDK was already installed in the isolated Python 3.11 environment; dependency dry-run confirms the optional pins are satisfied. Base dependencies were not upgraded.

## TDD and review closure

Initial RED: adapter missing; then dice/numeric/pair loss; then image-only integration not connected. GREEN after the thin adapter/hook. Final review exposed three RED production-seam cases: whole-page source not forwarded, added candidate mechanics preempted a working Tesseract region repair, and 1d6+DB flipped to 1d6-DB. Fixed only local source forwarding and candidate validation. Damaged-region candidates reuse the existing accept_region check before selection; complete signed symbolic dice tokens are preserved. Legacy fallback/gates are unchanged. Punctuation-only noise also went RED then GREEN and falls back. Standards and Spec re-review pass with no remaining blocking finding.

60 OCR-specific tests, 101 targeted including existing PDF/AI-repair/numeric-pair tests, pass. Disabled/unavailable/missing-model/init/inference/empty/malformed/rejected Paddle all preserve Tesseract fallback. Native PDF OFF/ON unchanged; startup imports no Paddle; prepared setup downloads nothing; singleton reuse/serialization and no external provider invocation pass.

PR #157 review fix: a SAN loss is checked as one ordered slash expression, so `SAN 1 and 1d6`, `SAN 1 1d6`, and `SAN 1d6/1` are rejected against `SAN 1/1d6`. Matching source can correct bounded `ld6`/`Id6` and a SAN numerator `l`/`I`; wrong faces and unknown-source candidates remain rejected. A production PDF seam test confirms SAN rejection still selects Tesseract. Ordinary words and other dice formulas remain intact.

Full suite: **1922 passed, 1 skipped, 152 subtests passed**, 15.72s. Ruff PASS; mypy PASS (127 files); compileall PASS; diff-check PASS.

## Actual offline CPU smoke

macOS Apple Silicon, Python 3.11.15, actual SDK/model inference with socket connections denied. No cloud provider used. Synthetic RGB 1700x900, 200 DPI, black text/white background, Arial/STHeiti Medium 42px, no crop. A/B executes the exact baseline/current local OCR function bodies, not a full PDF import.

| Fixture | Existing Tesseract chars | Paddle chars | Paddle status | Known numeric/dice preservation OFF/ON | Warm Paddle seconds |
|---|---:|---:|---|---|---:|
| Neutral scanned text | 119 | 118 | accepted | PASS/PASS | 1.10 |
| Stat/mechanics | 79 | 77 | accepted | PASS/PASS | 0.99 |
| Bilingual | 84 | 80 | accepted | PASS/PASS | 1.06 |

Character-count differences include line breaks; they do not prove broad accuracy improvement. SDK import 0.90s; engine initialization 1.19s; first OCR call 2.24s including initialization. Network attempts and external provider calls: **0**.

Two private non-map Haunting raster pages (physical 20/35) also produced accepted local candidates, 3727/1667 characters, in 16.63/17.17s. These are candidate-only smoke results: no authoritative completeness, publication or gameplay claim. No map/source-topology/corpus import executed. Full-page CPU cost remains a limitation; no optimization attempted.

Only sanitized versions, hashes, counts, statuses and timing are committed in [results](minimal_paddle_ocr_results.json). Images, private OCR prose and model binaries stay outside the repository. **MERGE READY** for the scoped optional backend; Python 3.14 in-process Paddle and Linux inference remain explicitly limited/unverified. No PR was opened automatically.
