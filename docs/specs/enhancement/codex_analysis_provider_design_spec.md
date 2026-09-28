# Codex Structured Text Analysis Provider

## Goal

Use the authenticated Codex CLI for structured text tasks selected through `LLM_PROVIDER`. Keep PDF/image/OCR and pre-generated character-card extraction on API providers because measured Codex extraction quality is insufficient for those tasks. `ANALYSIS_PROVIDER=codex` is not supported.

## Baseline behavior and verified facts

As of 2026-09-28, on `main_v2` after PR #120:

- Before this branch, all analysis consumers selected providers through `registry.analysis_provider()`.
- Codex conversation requests can return structured text decisions; image extraction must meet the document workflows' accuracy needs before Codex can be considered for `ANALYSIS_PROVIDER`.
- The seven current analysis call sites run outside the event loop: their callers use `asyncio.to_thread` or a worker process. A synchronous adapter can therefore run the existing async transport with `asyncio.run` without changing those consumers.
- Codex CLI 0.157.1 supports `codex exec -i/--image` and `--output-schema`, which allowed a direct capability probe before deciding not to ship image support.
- `ANALYSIS_PROVIDERS` and configuration intentionally exclude Codex after the real-PDF map quality test.
- PDF repair and page-image/map analysis may call image analysis once per page. Since `ExecTransport` starts a CLI process per request, this can multiply startup latency and consume ChatGPT plan capacity. Measure this before implementation.

Provider routing is selected by work type, not merely by whether the source originated in a PDF. Preserve these assignments:

| Work | Provider setting | Reason |
|---|---|---|
| PDF page image classification, scene-map/room-graph extraction, and image-based PDF repair/OCR | `ANALYSIS_PROVIDER` (API provider only) | Direct page-image and document-extraction work; may run once per page. Codex map extraction failed the measured quality check. |
| Pre-generated investigator/character-card extraction, including structured fields and skills | `ANALYSIS_PROVIDER` | User decision: character-card extraction stays with document analysis even when its input is already extracted text. |
| Scenario index (NPCs/locations), opening narration extraction, scenario-text comparison, Keeper history summarization, and other non-PDF structured text analysis | `LLM_PROVIDER` | These are general text tasks and should share the conversation provider selection. |

The implementation moves general non-PDF text consumers to the active LLM provider while retaining `ANALYSIS_PROVIDER` for PDF/page-image and pregen consumers. Keep the existing `None`-on-handled-failure behavior for structured analysis.

## Implemented scope and policy

- Add synchronous `codex_provider.analyze_text(text, tool, prompt_text)` for general structured text tasks routed through `LLM_PROVIDER`.
- Derive a Codex strict-output-schema projection from the supplied `tool["input_schema"]`, then validate returned JSON against the original schema with `jsonschema` before returning a dictionary. The projection must set `additionalProperties: false` on every object and satisfy Codex strict-mode required-property rules without widening the original schema.
- Return `None` for handled CLI, timeout, parse, or schema failures. Do not route a failed request to another provider.
- Keep `ExecTransport.request` text-only. Do not expose Codex image extraction through the analysis registry.
- Do not add Codex to `ANALYSIS_PROVIDERS`; reject `ANALYSIS_PROVIDER=codex` with an actionable configuration error.
- Route scenario indexing, opening narration extraction, scenario-text comparison, and Keeper history summarization through `LLM_PROVIDER`; retain pregen extraction, PDF repair, and scene-map/page-image analysis under `ANALYSIS_PROVIDER`.
- Update `.env.example` and provider/configuration documentation to keep PDF/image/OCR and pre-generated card extraction on an API provider when `LLM_PROVIDER=codex`.
- Keep Codex analysis independent of `OPENAI_API_KEY`. Document that separately enabled RAG embeddings still use the existing OpenAI Embeddings path and may independently need that key.

## Non-goals

- Changing any analysis consumer interface or moving those calls onto the event loop.
- Changing player conversation, CoC tools, game rules, or scenario data formats.
- Silently falling back to OpenAI, Anthropic, or Gemini if Codex fails.
- Replacing the conversation transport or adding image support to app-server.
- Migrating features that do not use the analysis provider registry.
- Removing OpenAI Embeddings from the separate RAG indexing/search path.

## Interface and data flow

The public analysis interface remains synchronous:

```text
existing worker-thread/process caller
  -> registry.analysis_provider()
  -> codex_provider.analyze_text
  -> asyncio.run(one-shot ExecTransport.request(...))
  -> Codex strict-schema projection
  -> codex exec --output-schema
  -> strict JSON and original caller-schema validation
  -> dict, or None on a handled failure
```

`analyze_text` sends the supplied text and task prompt as request content. The analysis schema is an output contract only: Codex must not execute application or game tools. Image analysis is intentionally excluded after the real-PDF test found no structured rooms on either sampled map page.

The text adapter must use existing Codex timeout and input/output limits. Because synchronous calls create short-lived event loops, request admission/concurrency limits must apply across those calls; a loop-local `asyncio.Semaphore` alone does not coordinate separate `asyncio.run` loops. Do not log prompts, extracted document text, credentials, or full environment values. Diagnostics may include provider, task kind, elapsed time, and safe error category.

## Configuration behavior

The settings remain independently selectable. For example:

```dotenv
LLM_PROVIDER=codex
ANALYSIS_PROVIDER=anthropic
```

Conversation and general non-PDF text analysis use `LLM_PROVIDER`. PDF/image/OCR and pregen-card extraction use `ANALYSIS_PROVIDER`, which accepts only `openai`, `anthropic`, or `gemini`. Codex CLI text calls use the local ChatGPT login and do not require or read `OPENAI_API_KEY`. RAG embeddings remain a separate configured capability and are outside this guarantee.

### Non-PDF text and structured-extraction probe (2026-09-28)

Five direct Codex CLI probes used `gpt-6-luna`, medium reasoning effort, and real scenario/character material. These probes assess capability; they are not a head-to-head provider benchmark or a release-wide accuracy guarantee.

| Task | Source-checked result | End-to-end latency |
|---|---|---:|
| Opening narration/check extraction | Correctly returned the Sanity check with success loss `1` and failure loss `1D4` from a Dead Boarder source page. | 16.4 s |
| Scenario index | Extracted the Corbitt entry from a Haunting stat page and did not invent absent HP. The returned location was not fully checked. | 20.0 s |
| Scenario-text comparison | Correctly detected a deliberately omitted Armor section and its 5-point value in a controlled two-version comparison. | 29.0 s |
| Pregen character extraction | On 10 Doors to Darkness character pages: 10/10 names, 10/10 occupation strings, 120/120 sampled numeric fields, and 10/10 Luck source checks matched. Each character had 14–18 skill entries. Age placement was not fully verified; the current schema has no dedicated age field. | 160.5 s |
| Keeper history summary | Preserved all four checked facts: arrival at Corbitt House, possession of the key, lamp/kerosene/axe not yet acquired, and purchase not completed. | 9.9 s |

The pregen schema contains dynamic-key dictionaries for skills and extra fields. Codex strict output requires a closed object schema, so the probe represented those dictionaries as key/value arrays and normalized them before validating against the existing schema. This adapter detail and age preservation require explicit implementation tests. The 10-card run's 160.5-second latency is a material risk for bulk imports. The other probes used selected pages or a short exchange; the scenario-index location and full-document coverage remain unverified.

## Real-PDF capability benchmark

Before making a provider decision, use one real scenario PDF containing selectable-text pages, at least one scanned/text-image page, and at least one map or diagram page. Use direct authenticated `codex exec -i --output-schema` calls as a capability probe.

Record:

- Total PDF pages and the number of pages that the existing PDF repair and page-image/map flows would send for analysis.
- Per-call process startup and end-to-end latency for every analyzed page, including median, p90, p95, maximum, timeout/error count, and total wall time. Separate process startup from model completion where instrumentation permits.
- ChatGPT plan usage/quota immediately before and after the run. Use CLI/provider-reported usage where available; otherwise record the account's displayed before/after quota and label it as an estimate. Do not infer exact token or quota consumption when the service does not expose it.
- Scanned-page recognition quality against a source-checked reference: required text/fields recovered, incorrect values, and unsupported additions.
- Map/diagram quality against a reference: labels/rooms and visible connections correctly identified, missed items, false connections, and invented items.
- Codex CLI version, selected model/reasoning setting, page image dimensions, schema, and whether calls were sequential or concurrent.

The report must state whether one process launch per page is acceptable for a full scenario import. If latency or plan quota is unacceptable, do not route production page analysis through Codex without revising the design.

### Initial real-PDF benchmark (2026-09-28)

The first benchmark used `/Users/marcoliu/Downloads/PDF文件/The_Haunting_Scenario_trimmed.pdf` (27 pages). With `ANALYSIS_PROVIDER=codex`, MarkItDown's OCR adapter is unavailable, so the existing PDF flow leaves the 12 low-text graphic pages (7 and 17–27) for image analysis. Each page was rendered at 200 DPI (1650 × 2150 pixels) and sent in a separate, sequential Codex CLI process. CLI version was 0.157.1; model `gpt-6-luna`, reasoning effort `medium`.

The first 12 calls passed the existing tool schema unchanged and were rejected before model execution with `invalid_json_schema`: Codex requires `additionalProperties: false` for every object. After deriving a strict schema projection (all object properties required, with empty strings/arrays for absent optional values), all 12 calls completed successfully. Preserve the original schema and validate against it after parsing; the strict projection is a transport constraint, not permission to change caller semantics.

| Measure | Result |
|---|---:|
| Calls completed / errors after strict projection | 12 / 0 |
| Total sequential wall time | 294.3 s (4 min 54 s) |
| Per-call end-to-end latency | median 21.0 s; p90 29.1 s; p95/max 58.2 s; range 12.3–58.2 s |
| CLI event usage across 12 calls | 236,756 input tokens (16,128 cached), 10,030 output tokens, 727 reasoning tokens |
| Classification | 12/12 page types matched the visual reference |
| Populated investigator sheet, page 18 | 25/26 sampled populated label/value pairs matched; age 36 was missed. Blank Drive Auto was correctly excluded from the expected populated values. This is a bounded field probe, not a full-page accuracy score. |
| Map graph, pages 7 and 17 | 0/13 actual rooms in `rooms` on each map; 0 structured exits. The basement's separately labeled wall-space area is not counted as a room. An extra page-17 run with an explicit room-graph instruction still returned an empty room list. `scene_map.analyze_page_image` therefore would not create either map. |

One instrumented page-18 call reached the first CLI event at 0.70 s and completed at 29.21 s; most of that sample's latency was after process startup. The 12-page batch itself was sequential, while the importer may run up to 12 image requests concurrently; concurrent latency and rate-limit behavior remain unmeasured. The CLI exposes per-call token usage, but neither `codex login status` nor the JSON events exposed remaining ChatGPT plan quota, so no before/after quota balance can be reported. These token counts must not be presented as exact plan-capacity consumption.

**Decision:** the real PDF confirmed basic page classification and identified a schema compatibility requirement, but the required map graph was absent on both map pages even with a targeted prompt. Codex therefore remains disabled for `ANALYSIS_PROVIDER`; successful text probes do not establish reliable PDF, map, OCR, or character-card extraction.

## Failure and privacy behavior

- Parse exactly one JSON object; reject malformed JSON, duplicate keys, non-object output, and values that fail the supplied input schema.
- Treat missing CLI/authentication, nonzero exit, timeout, output/input limit, cancellation, and schema mismatch as `None` using the same handled-failure convention as existing analysis providers.
- Never invoke CoC tools or perform application mutations from this adapter.
- Always clean temporary schema files. Preserve existing subprocess cancellation and process-group cleanup behavior.

## Testing plan

- Offline unit tests with a mocked transport for text requests, including schema projection, validation, and concurrency behavior.
- Validate correct output and `None` for malformed JSON, duplicate keys, wrong JSON types, invalid schema, timeout, nonzero exit, cancellation, and output/input limits.
- Test that `ANALYSIS_PROVIDER=codex` is rejected and that Codex text analysis does not require `OPENAI_API_KEY`.
- Add an opt-in authenticated text smoke test, disabled by default and explicitly enabled by a test environment variable. It performs one structured text request with the installed CLI and verifies the returned schema. It must not run in normal CI.
- Use source-checked real-PDF results as a gate before enabling Codex for image/document analysis.

## Implementation record

Implemented on the Codex analysis branch:

- `CodexProvider.analyze_text` uses `ExecTransport`, projects caller schemas to Codex strict output schemas, normalizes optional and dynamic-key values, and validates results against the original JSON Schema. Nullable objects (for example an optional opening check) retain their closed object properties and required fields in the strict projection, with null as a separate alternative.
- `ExecTransport.request` remains text-only. No image adapter or image CLI input is shipped because measured extraction quality was insufficient.
- Analysis admission is bounded across short-lived event loops in the process. There is no cross-process shared limiter; deployment concurrency across multiple worker processes remains a configuration-level limit.
- Provider registration/config reject `ANALYSIS_PROVIDER=codex`; scenario indexing, opening extraction, text comparison, and Keeper summaries use `LLM_PROVIDER`. PDF/page-image/OCR-repair and pre-generated character extraction continue to use an API provider under `ANALYSIS_PROVIDER`.
- Offline tests cover schema projection/normalization, validation failures, provider routing, and concurrency. The authenticated smoke test is opt-in and makes one text call.
- Documentation states that the measured Codex map extraction produced no structured rooms on the tested maps. This implementation does not claim to improve that model capability.

The smoke test does not qualify Codex for image/document extraction. Reconsider that only after a new source-checked benchmark demonstrates reliable map, OCR, and character-card results.
