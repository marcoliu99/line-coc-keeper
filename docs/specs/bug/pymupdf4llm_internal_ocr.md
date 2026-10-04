# Disable PyMuPDF4LLM internal OCR

## Problem and goal

PyMuPDF4LLM 1.28.2 enables its Tesseract OCR by default. The production `to_markdown()` call does not override that default, so Tesseract can run before the repository's Paddle-first OCR path. Pass the installed API's `use_ocr=False` at that call so PyMuPDF4LLM provides native text and layout only.

## Scope

Change the PyMuPDF4LLM call and add a bounded, conservative numeric verification pass in `app/pdf_loader.py`, with focused tests. Keep PR161 OCR selection logs.

## Non-goals

Do not change Paddle OCR acceptance, Tesseract fallback, Paddle Layout ordering, publication, providers, or gameplay. Do not patch third-party code. Verification cannot replace canonical text or resolve unrelated warnings.

## Data and flow

`to_markdown(..., use_ocr=False)` returns native text/layout evidence. The existing low-text checks then decide whether to invoke `_ocr_image()`, which tries Paddle and falls back to Tesseract unchanged.

High-text pages whose selected source is unchanged native text and whose warnings include `numeric_pair_review`, `layout_numeric_loss`, `layout_pair_mismatch`, or `source_pair_unresolved` receive one optional whole-page Paddle second opinion. Pair warnings retain their existing checks. For `layout_numeric_loss`, compare only complete mechanics missing from the layout candidate (including dice modifiers and ordered slash expressions), and require each missing occurrence beside the same nearby text anchor in Paddle evidence. A number elsewhere on the page, an ambiguous anchor, or partial confirmation cannot clear the warning. An unresolved native pair cannot be confirmed from OCR alone. Low-text pages skip this pass to avoid duplicate inference. Failure or incomplete evidence retains every warning; this verification never falls back to Tesseract. The original warning list and canonical text stay intact; `paddle_numeric_verification` records attempt, status, checked/resolved/unresolved warnings, missing/confirmed/unconfirmed mechanics, anchor count, and pair counts without OCR prose. Final review ignores only specifically resolved warnings.

## Tests and validation

Assert the production PyMuPDF4LLM call passes `use_ocr=False`. Compare native single- and two-column page text/layout selection; verify a raster/low-text page reaches Paddle, Paddle acceptance skips Tesseract, and Paddle rejection/unavailability retains fallback. Test numeric confirmation, mismatch, missing evidence, all Paddle failure statuses, no-warning skip, low-text skip, and unchanged canonical text. Run a Python 3.13 smoke on the hash-matched private PDF and record pages 12, 16, and 17 as sanitized counts/statuses only. Run full pytest, ruff, mypy, compileall, and diff check.

## Local smoke evidence

The private source matching the prior page-12/16/17 report was identified by SHA256. A Python 3.13 local-only rerun found no PyMuPDF4LLM Tesseract/OCR-page messages. Across 27 pages, numeric verification invoked Paddle on pages 12 and 16 only; 12 existing ordinary Paddle calls remained. Page 12's one disputed pair was confirmed and its numeric warning resolved for final review, while canonical native text and warning history stayed intact. Page 16 has one missing numeric occurrence (`1918`), but its repeated occurrences cannot be uniquely bound to the lost local context (zero qualifying anchors); independent OCR therefore remains inconclusive and final review stays on. Page 17 used its ordinary low-text Paddle path once and did not enter numeric verification.

`vision_review_required` stays in warning history but does not alone require review when a vision candidate exists, pair checks are clean, and final text is usable. For a page originally marked `low_text`, a long derived vision description alone does not establish usable selected source text. Replaying the hash-matched production report keeps pages 7, 16, and 17 under review: page 7 also has `ocr_evidence_loss`, and page 17's selected non-vision candidate remains below the text threshold. Scene maps remain present on pages 7 and 17. No map or OCR warning is silently cleared.
