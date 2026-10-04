# Disable PyMuPDF4LLM internal OCR

## Problem and goal

PyMuPDF4LLM 1.28.2 enables its Tesseract OCR by default. The production `to_markdown()` call does not override that default, so Tesseract can run before the repository's Paddle-first OCR path. Pass the installed API's `use_ocr=False` at that call so PyMuPDF4LLM provides native text and layout only.

## Scope

Change only the PyMuPDF4LLM call in `app/pdf_loader.py` and focused tests. Keep PR161 OCR selection logs.

## Non-goals

Do not change Paddle OCR acceptance, Tesseract fallback, Paddle Layout ordering, review-page decisions, publication, providers, or gameplay. Do not patch third-party code.

## Data and flow

No schema change. `to_markdown(..., use_ocr=False)` returns native text/layout evidence. The existing low-text checks then decide whether to invoke `_ocr_image()`, which tries Paddle and falls back to Tesseract unchanged. Existing warning and report fields remain unchanged.

## Tests and validation

Assert the production PyMuPDF4LLM call passes `use_ocr=False`. Compare native single- and two-column page text/layout selection; verify a raster/low-text page reaches Paddle, Paddle acceptance skips Tesseract, and Paddle rejection/unavailability retains fallback. Run a Python 3.13 smoke on the same PDF as the observed log when its identity is known. Record pages 12, 16, and 17 as sanitized counts/statuses only. Run full pytest, ruff, mypy, compileall, and diff check.

## Local smoke evidence

The private source matching the prior page-12/16/17 report was identified by SHA256. A Python 3.13 local-only rerun found no PyMuPDF4LLM Tesseract/OCR-page messages. Pages 12 and 16 retained native text without OCR; page 17 reached Paddle and was accepted. The existing page warnings remain and are outside this change.
