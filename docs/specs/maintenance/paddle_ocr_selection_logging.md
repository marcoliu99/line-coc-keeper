# Paddle OCR selection logging

## Problem and goal

Runtime INFO logs do not say whether Paddle OCR was selected or the existing Tesseract fallback supplied the page text. Log the actual selection for each OCR attempt without changing the text, validation, or fallback behavior.

## Scope

Only `app/pdf_loader.py` and, if needed, `app/pdf_ocr.py` may change in production. Add OCR-specific tests. The existing `OcrResult.status` values remain `accepted`, `rejected`, `unavailable`, `error`, and `empty`.

## Non-goals

No changes to OCR acceptance, SAN/dice/numeric checks, language order, timeouts, Paddle Layout, PDF admission, map, providers, or gameplay. INFO logs must not contain OCR text or PDF source.

## Schema and flow

No data structure or schema changes. At `_ocr_image()`'s selection point:

1. Paddle `accepted`: emit `ocr=paddle status=accepted` and return its text.
2. Every other Paddle status: emit `ocr=paddle status=<status> fallback=tesseract`, then run the existing fallback unchanged.
3. Only when either existing Tesseract path returns nonempty text: emit `ocr=tesseract status=accepted` and return it.

The message reports an attempted fallback, not a successful Tesseract result, until the accepted log is emitted. No Paddle log is emitted when Paddle is not called.

## Tests and verification

Use INFO log capture around the production `_ocr_image()` path for accepted, unavailable, rejected, error, and empty Paddle results. Verify Paddle acceptance skips Tesseract, each other status preserves fallback, and Tesseract acceptance is logged. Run a Python 3.13 real OCR smoke and the full pytest, ruff, mypy, compileall, and diff checks.

## Open questions

None. Existing status values and fallback branches define the required behavior.
