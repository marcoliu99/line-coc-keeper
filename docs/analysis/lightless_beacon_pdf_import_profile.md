# The Lightless Beacon PDF import profile (2026-10-04)

## Executive summary

| Question | Finding |
|---|---|
| Paddle timeout | **NO — NOT SUPPORTED.** Neither Paddle Layout nor the local Paddle OCR log has a timeout/error event for this upload. Layout inference peaked at 1.267 s. |
| Main performance bottleneck | The MarkItDown/markitdown-ocr window occupied **391.7 s (71.8%)** of a 545.4 s upload. Its 47 successful OpenAI `chat/completions` calls match 47 embedded-image OCR units found by replaying the installed converter's local image extraction on the exact 23-page subset. The 47 requests themselves span at least 382.7 s. |
| Main source of review pages | Ten pages still have <200 characters of selected non-vision source; derived vision descriptions make their *final* strings longer but do not verify that source. Seven have unresolved layout numeric evidence, five have other layout conflicts, and one has failed/exhausted local region repair. Categories below are mutually exclusive primary reasons. |
| MarkItDown/OpenAI calls | **47** `chat/completions` HTTP 200 responses, one per locally enumerated embedded image. This is separate from one AI repair and ten concurrent vision calls to `/responses`, plus later index/pregen work. |
| Budget exhausted | **Local OCR: YES**, 8/8 attempted on page 4 and six further regions marked `budget_exhausted`. **AI repair: NO**, 1/8 requests used. |
| Recommended next small PR | Measure/avoid duplicate *external* image understanding on the ten pages that received both MarkItDown embedded-image OCR and a whole-page vision pass, while retaining source and map evidence checks. Do not soften review warnings based on derived description length. |

## Evidence and attribution

- Archived runtime log: `/Users/marcoliu/coc_v2_log/20261004-055532-257518_runtime_profile-async.log` (the requested `.runtime/bots/profile-async.log` had rotated). The matching Pyinstrument file is archived alongside it; it profiles the bot session containing several imports and cannot independently isolate this one request.
- **Only this upload**: `request_id=req_a02585025e584653b82e518323872cc9`, `conversation_id=0341532cdeef`, `2026-10-04 05:39:27.806–05:48:33.161 UTC` (Taipei 13:39:27–13:48:33). The completion message names `03 The Lightless Beacon`; neighboring Haunting, Camp Sunny, Alone Against the Flames, and Dead Boarder requests were excluded.
- Persisted `data/scenarios/03-the-lightless-beacon-b0f52812/parse_quality.json` has 43 pages, the specified 23 `review_pages`, and PDF SHA-256 `14437ca4eca3b6c05b22403bdad30bdb8668f1837e0a2d0ee42e49c160d8aa07`. Its local modification time, 13:48:31 Taipei, matches this request's save interval. No full PDF, extracted prose, provider response, or OCR text is copied here.
- Exact per-page OpenAI counts were **not logged with page IDs**. The counts below are a deterministic retrospective reconstruction: production selected the 23 low-text graphic pages, and the installed `markitdown_ocr` converter loops over every image returned by its `_extract_page_images`; an offline, provider-free extraction of the same 23-page subset returned exactly 47 image units, matching the 47 HTTP responses. This supports one request per image, but individual HTTP request IDs cannot be matched to individual pages from this log alone.
- Ten vision calls were emitted by a `ThreadPoolExecutor`; their observability records have no `request_id`. They are attributed by the exact 05:47:05.896–05:47:16.863 post-MarkItDown interval, ten starts/completions and ten `/responses` HTTP 200s, and the ten pages with persisted vision candidates. Their individual page-to-transport mapping is likewise unavailable in the log.

## Performance timeline

The request was **545.355 s (9 min 5.4 s)** from `request.started` to `request.completed`. Stage boundaries marked “window” are inferred from adjacent logged events, so they are useful wall-time bounds, not independent timer spans. Percentages partition the request and should not be interpreted as precise CPU attribution.

| Stage | UTC start–end | Calls | Pages | Duration | % total |
|---|---|---:|---|---:|---:|
| Upload/preview, PyMuPDF4LLM native-layout extraction, per-page native extraction, Paddle Layout, local region OCR | 05:39:27.806–05:40:31.599 | 43 layout decisions; 8 local OCR attempts | 43; local OCR only p4 | 63.793 s window | 11.7% |
| MarkItDown/markitdown-ocr conversion | 05:40:31.599–05:47:03.276 | 1 subset conversion; **47** embedded-image OpenAI chat calls | 23 low-text graphic pages | **391.677 s window** | **71.8%** |
| AI targeted repair | 05:47:03.276–05:47:05.885 | 1 `/responses` call | p4 | 2.609 s | 0.5% |
| Whole-page vision / scene-map classification | 05:47:05.885–05:47:16.863 | 10 concurrent `/responses` calls | p2, 5, 31, 32, 34, 35, 37, 38, 40, 41 | 10.978 s window | 2.0% |
| Post-vision local numeric verification and index preparation | 05:47:16.863–05:47:45.078 | 2 recorded inconclusive Paddle verification results; 5 skips | p6, 19; skips p17, 18, 28–30 | 28.215 s **unseparated** | 5.2% |
| Scenario index (`LLM_PROVIDER=codex`) | 05:47:45.078–05:48:02.270 | 1 analysis CLI dispatch | scenario text | 17.192 s | 3.2% |
| Pregen extraction (`ANALYSIS_PROVIDER=openai`) | 05:48:02.270–05:48:31.803 | 1 `/responses` call | scenario text | 29.533 s | 5.4% |
| Save, reload, state update, confirmation | 05:48:31.803–05:48:33.161 | 1 final persistence/confirmation sequence | whole scenario | 1.358 s | 0.2% |

The first layout result appeared at 05:39:37.943. The 10.137 s before it includes preview, PyMuPDF4LLM and first-page work; the log cannot split those. Layout's 43 decisions span 53.656 s, with **41 nonzero model inferences summing to 38.394 s**. The remaining time in that early window includes page processing, rendering, eight local OCR attempts, and other extraction work; separate durations were not recorded. MarkItDown's first HTTP response appeared at 05:40:40.411 and last at 05:47:03.098, a **382.687 s response span** within its 391.677 s window. No per-call start/duration is logged for those chat calls, so “382.687 s” is not their summed service time.

## Paddle and request inventory

Paddle Layout was evaluated for all 43 pages: **3 accepted** (`two_columns`), **40 fallback** (18 `incomplete_mapping`, 15 `malformed_result`, 5 `not_two_columns`, 1 `invalid_order`, 1 `repair_required`). Its 41 nonzero inference times were min **0.892 s**, median **0.917 s**, p95 (nearest-rank) **1.043 s**, max **1.267 s**. One `repair_required` and one mapping fallback had zero inference time. There was no `inference_error`, `initialization_error`, backend unavailable, or timeout event.

The eight logged local OCR attempts, all on p4, yielded Paddle **3 accepted / 5 rejected / 0 error / 0 empty / 0 unavailable**. Each rejected attempt fell back to Tesseract, and five `ocr=tesseract status=accepted` events appear. These accepted OCR *candidates* did not pass the separate region-replacement safety check: the quality report records eight `review_required` local repairs and zero accepted replacements. Paddle OCR has no per-call inference timer in this log. The p6/p19 numeric second-opinion records are separate direct Paddle verifications, both `inconclusive`; no timeout status is recorded there either.

OpenAI HTTP responses attributable to this import total **59**: 47 `/chat/completions` (MarkItDown), one `/responses` targeted repair, ten `/responses` vision, and one `/responses` pregen extraction. The ten vision transports lack `request_id` because of the worker-thread context boundary; this is a tracing limitation, not evidence of extra requests. The scenario index made one separate Codex analysis CLI dispatch. All 59 observed OpenAI HTTP responses were 200; no repeat transport or 429/timeout appears in this request window. A 200 response does not mean its content passed source validation.

The production subset had 23 pages: p1, 2, 5, 16–18, 25, 27–42. Offline replay of the installed converter produced **three embedded-image OCR units on p1 and two on each other page** (47 total). Thus p18, for example, accounts for **two**, not four, MarkItDown calls. PyMuPDF's raw image inventory counts some pages differently; it is not the converter's actual call list. MarkItDown was called once on the 23-page subset, not once per page or repeatedly on the whole book.

The ten pages with *both* MarkItDown image OCR (2 calls each) and later whole-page vision (1 call each) are **p2, 5, 31, 32, 34, 35, 37, 38, 40, 41**. Example: p31 = MarkItDown 2 + vision 1. No page in this run had the triple combination local Paddle OCR + MarkItDown + vision: the eight local Paddle OCR attempts were all p4, which never entered the MarkItDown low-text subset. Pregen/index work is separate again.

## Review pages

In this table `native/layout/final` are character counts; `final` may include a clearly labelled *derived* vision description. “OCR” denotes local region OCR, and `—` means no recorded attempt. MarkItDown call counts use the 47-unit offline reconstruction described above. “Vision yes” means a persisted vision candidate, not certified source text. No full text is included.

| Page | Native / layout / final | Canonical method | Paddle OCR | MarkItDown calls / candidate chars | AI repair | Vision | Final unresolved warning | Review reason |
|---:|---:|---|---|---:|---|---|---|---|
| 2 | 79 / 89 / 311 | markitdown | — | 2 / 122 | — | yes | `vision_review_required` | Selected non-vision source is only 122 chars; 178 derived vision chars do not verify completeness. |
| 3 | 2060 / 2115 / 2115 | layout | — | 0 | — | — | `ambiguous_columns` | Native geometry still reports ambiguous column order. |
| 4 | 1492 / 1584 / 1584 | layout | 3 accepted, 5 rejected; 5 Tesseract fallbacks | 0 | 1 unresolved | — | `local_ocr_review` | Eight region OCR candidates rejected for replacement; six further regions hit local budget. AI targeted repair did not validate them. |
| 5 | 0 / 0 / 117 | native | — | 2 / 0 | — | yes | `low_text`, `vision_review_required` | No native/layout/MarkItDown source; vision description leaves final text below 200 chars. |
| 6 | 3408 / 3383 / 3408 | native | numeric verification inconclusive | 0 | — | — | `layout_numeric_loss` | One native mechanic missing in layout; Paddle did not confirm its anchored counterpart (0/1). |
| 13 | 3388 / 3631 / 3631 | layout | — | 0 | — | — | `ambiguous_columns` | Column geometry remains ambiguous despite selected layout text. |
| 16 | 52 / 25 / 816 | markitdown | — | 2 / 816 | — | — | `layout_text_loss` | Earlier layout lost native words; accepted MarkItDown text did not resolve this historical layout warning. |
| 17 | 61 / 68 / 1292 | markitdown | numeric verification skipped | 2 / 1292 | — | — | `layout_numeric_loss`, `layout_text_loss` | Layout lost numeric/text evidence; verification skipped for selected MarkItDown source. |
| 18 | 44 / 54 / 2069 | markitdown | numeric verification skipped | 2 / 2069 | — | — | `layout_numeric_loss`, `layout_text_loss` | Same unresolved layout-vs-native numeric/text conflict. |
| 19 | 3580 / 3762 / 3580 | native | numeric verification inconclusive | 0 | — | — | `layout_numeric_loss` | One missing layout mechanic; Paddle confirmed 0/1 anchored items. |
| 25 | 67 / 102 / 431 | markitdown | — | 2 / 431 | — | — | `layout_text_loss` | Layout text loss remains flagged after MarkItDown selection. |
| 27 | 69 / 39 / 741 | markitdown | — | 2 / 741 | — | — | `layout_text_loss` | Layout text loss remains flagged after MarkItDown selection. |
| 28 | 44 / 54 / 1274 | markitdown | numeric verification skipped | 2 / 1274 | — | — | `layout_numeric_loss`, `layout_text_loss` | Layout numeric/text loss; MarkItDown selected, numeric verification skipped. |
| 29 | 61 / 68 / 1728 | markitdown | numeric verification skipped | 2 / 1728 | — | — | `layout_numeric_loss`, `layout_text_loss` | Same unresolved conflict. |
| 30 | 44 / 54 / 401 | markitdown | numeric verification skipped | 2 / 401 | — | — | `layout_numeric_loss`, `layout_text_loss` | Same unresolved conflict. |
| 31 | 40 / 101 / 1995 | layout | — | 2 / 4233 | — | yes | `ocr_evidence_loss`, `vision_review_required` | Richer OCR candidate rejected; selected source is only 101 chars. Derived vision accounts for much of final length. |
| 32 | 39 / 117 / 1566 | layout | — | 2 / 1823 | — | yes | `ocr_evidence_loss`, `vision_review_required` | Selected source 117 chars; unverified alternative and derived description. |
| 34 | 23 / 87 / 1900 | layout | — | 2 / 4037 | — | yes | `ocr_evidence_loss`, `vision_review_required` | Selected source 87 chars; unverified alternative and derived description. |
| 35 | 47 / 122 / 1669 | layout | — | 2 / 1912 | — | yes | `ocr_evidence_loss`, `vision_review_required` | Selected source 122 chars; unverified alternative and derived description. |
| 37 | 40 / 101 / 2157 | layout | — | 2 / 3977 | — | yes | `ocr_evidence_loss`, `vision_review_required` | Selected source 101 chars; unverified alternative and derived description. |
| 38 | 36 / 114 / 1634 | layout | — | 2 / 2015 | — | yes | `ocr_evidence_loss`, `vision_review_required` | Selected source 114 chars; unverified alternative and derived description. |
| 40 | 23 / 87 / 1987 | layout | — | 2 / 4037 | — | yes | `ocr_evidence_loss`, `vision_review_required` | Selected source 87 chars; unverified alternative and derived description. |
| 41 | 57 / 139 / 1602 | layout | — | 2 / 1683 | — | yes | `ocr_evidence_loss`, `vision_review_required` | Selected source 139 chars; unverified alternative and derived description. |

For p16–18, 25, 27–30, `low_text` is historical: the selected MarkItDown result exceeds 200 chars. It is **not** the final reason for review. Likewise `native_two_columns` (p6, p19) and `layout_unavailable` (p5) are informational in the final review rule. For p2 and p31/32/34/35/37/38/40/41, the issue is the *selected non-vision source length*, not the longer derived final string. This is why successful vision or scene analysis cannot alone clear their review.

### Primary-category counts (one per review page)

| Category | Count | Pages |
|---|---:|---|
| A. Selected source genuinely too short / vision not source authority | 10 | 2, 5, 31, 32, 34, 35, 37, 38, 40, 41 |
| B. Numeric/mechanics evidence unresolved | 7 | 6, 17, 18, 19, 28, 29, 30 |
| C. Other layout evidence conflict | 5 | 3, 13, 16, 25, 27 |
| F. Region repair not accepted; local budget exhausted | 1 | 4 |
| D/E/G/H as exclusive primary reason | 0 | See overlapping diagnostics below. |

Overlapping diagnostics: eight A pages have a rejected richer OCR alternative (`ocr_evidence_loss`), and ten A pages have a vision-derived candidate. These are not 18 additional review pages. The local budget affected one page, not the other 22. There were no Paddle backend failures. **No confirmed false-positive review** is established by this artifact. Pages 16, 17, 18, 25, 27–30 merit a later review-precision audit because a new MarkItDown canonical text was selected while an earlier layout warning remains; they must not be silently cleared without source-bound validation. The eight A pages with rejected OCR are **not** the “healthy canonical + inferior rejected OCR” false-positive pattern: their selected canonical source remains below 200 chars.

## Budget and bottleneck conclusion

`extract_text` used its production defaults, `local_ocr_limit=8` and `ai_repair_limit=8`. The persisted counters are **8 local attempts, 0 remaining** and **1 AI repair request, 7 remaining**. P4 has 14 suspect blocks: eight got OCR candidates, none passed replacement, and six were explicitly `budget_exhausted`. AI repair then requested eight regions in one call but all remained unresolved. Local exhaustion is real and page-specific; **AI repair budget exhaustion hypothesis: NOT SUPPORTED**. No `budget_exhausted` marker appears in AI repair.

Measured bottlenecks, largest first: (1) MarkItDown embedded-image conversion window **391.7 s, 71.8%**; (2) the combined pre-MarkItDown extraction window **63.8 s, 11.7%**, including 38.4 s of summed Paddle Layout inference; (3) pregen provider call **29.5 s, 5.4%**; (4) unseparated post-vision local/index-preparation window **28.2 s, 5.2%**; (5) Codex index **17.2 s, 3.2%**; (6) concurrent vision window **11.0 s, 2.0%**. These are wall-time windows, not exclusive CPU times.

## Proposal only — no code changes in this investigation

- **P0, duplicate work:** p2, 5, 31, 32, 34, 35, 37, 38, 40, 41 each received two MarkItDown image calls and one later vision call. Instrument page/image IDs and result provenance first; then consider sharing a verified result where the two tasks truly require the same evidence. A map-specific whole-page analysis may still be necessary even after image OCR.
- **P1, external calls:** MarkItDown generated 47 OpenAI calls from 23 low-text pages, with two calls on nearly every page. A small follow-up could gate this converter on evidence that local/native extraction cannot supply required text, or compare a bounded whole-page call against multiple embedded-image calls. Preserve numeric/source checks; do not substitute an unverified description for source.
- **P2, review precision:** Audit whether selected MarkItDown source on p16–18, 25, 27–30 can source-bound resolve earlier layout warnings. Keep warning history and distinguish final unresolved evidence. The current log/report does not prove those warnings are false positives.

No production code, OCR threshold, budget, timeout, review rule, or provider behavior was changed for this profile.
