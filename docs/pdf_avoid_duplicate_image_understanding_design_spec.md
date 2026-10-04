# PDF image-understanding deduplication: evidence and design gate

## Problem and goal

The requested optimization is to avoid a whole-page vision request when MarkItDown has already supplied selected, usable source text and no map/spatial analysis is needed. It must preserve canonical selection, final review decisions, and scene maps.

The motivating Lightless Beacon upload (`request_id=req_a02585025e584653b82e518323872cc9`, PDF SHA-256 `14437ca4eca3b6c05b22403bdad30bdb8668f1837e0a2d0ee42e49c160d8aa07`) took 545.4 seconds. Its saved quality report and archived log show 47 MarkItDown image calls, ten later whole-page vision calls, and ten overlapping pages: 2, 5, 31, 32, 34, 35, 37, 38, 40, 41.

## Current flow and decisive finding

`app/pdf_loader.py::extract_text` adds graphic pages with selected source below `_LOW_TEXT_THRESHOLD` to `pending`. MarkItDown runs once on those pages. For each candidate, existing `pdf_quality.select_text` and numeric-pair checks determine whether it becomes canonical. **The current code already removes the page from `pending` whenever the selected text reaches the threshold.** Only remaining `pending` pages go to `_analyze_graphic_page` for whole-page vision/scene-map analysis.

The exact overlap pages all fail the proposed “accepted + sufficient” condition:

| Page(s) | MarkItDown selected? | Selected non-vision source chars | Why vision remains pending |
|---|---|---:|---|
| 2 | Yes | 122 | Accepted source is still below 200 chars. |
| 5 | No candidate | 0 | No source was recovered. |
| 31, 32, 34, 35, 37, 38, 40, 41 | No | 87–139 | Candidate was rejected; selected source remains below 200 chars. |

For the eight rejected pages, the MarkItDown candidates are longer (1,683–4,233 chars), but length alone is not authority. Their saved rows retain `ocr_evidence_loss`; the final source is short. Thus the ten subsequent vision calls are **not caused by failure to clear `pending` after successful text rescue**. A helper implementing the requested rule would produce the same ten vision calls and the same ten overlaps for this import.

No page in this corpus instance meets “MarkItDown selected + source >= threshold + nevertheless received vision.” A real reproducer for the alleged duplicate-dispatch defect is absent.

## Scope and non-goals

This design considers only post-MarkItDown vision dispatch. It does not change Paddle OCR/Layout, Tesseract, PyMuPDF4LLM, text selection, review warnings, thresholds, budgets, provider routing, scenario index, pregens, RAG, maps, or gameplay. The requested per-page INFO fields (`page`, `markitdown_attempted`, `markitdown_selected`, `selected_source_chars`, `vision_needed`, `vision_reason`) are useful observability, but alone save no request or time and should not be misrepresented as a performance fix.

## Map/source distinction

`_page_has_graphic_content` detects raster images or vector drawings; it does **not** classify maps. `_analyze_graphic_page` performs a combined page-type/description/map call only *after* a page remains pending. There is no existing deterministic `map_needed` fact before that call. Adding a map-specific exemption to the skip rule therefore requires an independently validated map/spatial signal or a new classification request; neither is established by the Lightless Beacon evidence. A character-count check cannot safely substitute for it.

The existing flow already skips whole-page vision when MarkItDown selects sufficiently long text, including a possible map page. That is an existing separate map-coverage question, not evidence that this requested optimization can reduce the ten observed calls. This task must not silently change map analysis or invent a book/page heuristic.

## Data/schema changes and integration

None are justified now. A future reproducible defect could add one small dispatch helper and INFO metadata at the existing `pending` transition in `extract_text`. It would preserve `select_text`, pair checks, `_page_requires_review`, and scene-map handling. No persisted schema or publication behavior should change.

## Test and validation plan if new evidence appears

Before production edits, create a sanitized fixture where a page **actually** remains pending despite selected MarkItDown source at or above threshold, then prove the failing test. Check accepted/sufficient non-map, rejected candidate, accepted/but-short source, verified map need, and no candidate. Assert identical review and map outcomes. After a real fix, run the full pytest/ruff/mypy/compileall/diff-check suite and a hash-checked Lightless Beacon A/B under identical provider configuration and budgets. Record real call counts and wall time; do not compare a fresh run against an old run as if timing were controlled.

## Decision and unresolved question

**Do not implement the proposed skip rule against current evidence.** Baseline is 47 MarkItDown calls, 10 vision calls, 10 overlaps. Under the proposed accepted-and-sufficient condition, the predicted counts remain **47 / 10 / 10**, with no defensible “after” wall-time measurement. No review/map rule or production code was changed.

A separate future decision could target a different, demonstrated cost: two MarkItDown embedded-image requests on each of the ten pages whose candidates were rejected or insufficient. That would require source-safety validation or a bounded alternative image strategy, rather than simply skipping the subsequent vision call.
