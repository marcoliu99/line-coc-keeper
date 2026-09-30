# PDF multicolumn validation — 2026-09-30

[繁體中文](pdf_multicolumn_ingestion_validation_zh.md)

## Local source-preservation corpus

Source PDFs were supplied locally; no PDF bytes or story paragraphs are committed. Exact file hashes, physical pages, block disposition counts and span checks are in [machine-readable results](pdf_multicolumn_ingestion_corpus_results.json).

| Book | Golden physical page | Old selected heading order | New order | Full-book accepted / unresolved / other | Geometry scan |
| --- | --- | --- | --- | --- | --- |
| The Haunting | 14 | Extension → Walter Corbitt → Conclusion → Rewards | Conclusion → Rewards → Extension → Walter Corbitt | 14 / 1 / 12 | 0.096 s |
| Dead Boarder | 5 | Introduction → Keeper Considerations → Rules Notes → Time Limit | unchanged, correct | 18 / 3 / 11 | 0.141 s |
| The Lightless Beacon | 6 | Introduction → Running the Game | unchanged, correct | 15 / 2 / 26 | 0.129 s |

All three golden pages and all 47 geometry-accepted pages preserved exact native word and numeric/dice token multisets (word recall 1.0). Unicode source-unit ranges round-trip, remain contiguous, cover the rendered text, and match physical page offsets. These are source-preservation checks, not semantic proof of every paragraph's position. The baseline Haunting layout candidate adds numeric tokens despite passing the existing loss-only selector.

The whole-book scan uses native geometry only. It does not run full OCR/map ingestion or establish publication readiness. Unresolved pages: Haunting 11; Dead Boarder 12, 19, 20; Beacon 3, 13, all `unsupported_spanning_region`. Other pages retain existing routes.

Golden-page parser times were 0.329 / 0.160 / 0.196 seconds; additional geometry/candidate checks were 0.006 / 0.070 / 0.004 seconds in one local run. These small samples are not a throughput benchmark.

## Real ordering-only image calls

Used the main_v2 environment's configured OpenAI analysis provider (`OPENAI_MODEL=gpt-6-luna`) with isolated data/database/log paths. Each request sends one supplied PDF page image and bounded original block excerpts; no gameplay state is shared. The adapter requires a full permutation and geometrically valid placement, and assembles original text only.

| Book/page | Source blocks | Result | Calls | Wall time |
| --- | --- | --- | --- | --- |
| Haunting 11 | 13 | accepted ordering | 1 | 5.996 s |
| Dead Boarder 12 | 13 | accepted ordering | 1 | 4.025 s |
| Beacon 13 | 18 | accepted ordering | 1 | 12.707 s |

[API results and budget](pdf_multicolumn_ingestion_api_results.json). All three attempts passed the deterministic ordering validator. This does not certify the entire book or every ambiguous layout. An earlier sandbox-restricted run produced no usable responses; it was excluded from successful API evidence. No fallback silently accepted those failures.

## Remaining measurement limits

- Docling package resolution succeeded on Python 3.14, but actual local model conversion and its accuracy/cost have not been measured. It remains opt-in; missing artifacts and failures preserve the draft.
- Complex tables, scanned column layouts, new OCR reconstruction and region extraction are outside the first release. Existing routes remain explicitly labeled, not presented as newly verified multicolumn evidence.
- Page/request caps are conservative defaults. Continued imports retain cumulative usage and only explicit configuration increases add allowance.
- No normal gameplay LLM calls were added; all new analysis occurs during import.

Docling option names and offline artifact setup were checked against its [official pipeline options](https://github.com/docling-project/docling/blob/main/docling/datamodel/pipeline_options.py).

## Review disposition

### Standards

The review identified one hard requirement: model closed layout value sets as types. Layout records now use Literal/TypedDict declarations. Import identity checks move behind the draft owner's public lease interface. The legacy five-value extraction contract remains for downstream compatibility; optional worker transport stays in the import-only adapter to avoid a new speculative abstraction.

### Spec

The review found three gaps: failed visual extraction could publish, exhausted Continue had no renewal mechanism, and cache validation omitted actual parser/renderer identity. Failed graphic extraction now blocks; request usage persists with explicit configured cap increases and distinct-page accounting; source, renderer and parser identities invalidate stale caches. Concurrent fresh uploads and cancellation are additionally covered by reserved import identity. Ordering runs serially per import, with subprocess processing durations recorded separately; there is no new adapter admission queue.
