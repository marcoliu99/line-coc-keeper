# Codex Structured Document and Image Analysis Provider

## Goal

Allow `ANALYSIS_PROVIDER=codex` to handle the existing structured text and image analysis requests through the authenticated Codex CLI. Keep current analysis consumers and their synchronous provider contract unchanged.

## Current behavior and verified facts

As of 2026-09-28, on `main_v2` after PR #120:

- The analysis consumers select providers through `registry.analysis_provider()`; provider registration and config validation are the remaining selection gates.
- `codex_provider` implements conversation decisions but has no `analyze_text` or `analyze_image` adapter.
- The seven current analysis call sites run outside the event loop: their callers use `asyncio.to_thread` or a worker process. A synchronous adapter can therefore run the existing async transport with `asyncio.run` without changing those consumers.
- Codex CLI 0.157.1 supports `codex exec -i/--image` and `--output-schema`. `ExecTransport.request` already writes a schema file and passes it to `codex exec`; it currently has no image argument.
- `ANALYSIS_PROVIDERS` excludes Codex, and `app/config.py` rejects `ANALYSIS_PROVIDER=codex`.
- PDF repair and page-image/map analysis may call image analysis once per page. Since `ExecTransport` starts a CLI process per request, this can multiply startup latency and consume ChatGPT plan capacity. Measure this before implementation.

Current analysis consumers include scenario indexing and comparison, opening narration extraction, pregen extraction, scene-map analysis, PDF image repair, and Keeper-side structured extraction. Keep their existing `None`-on-handled-failure behavior.

## Scope

- Add synchronous `codex_provider.analyze_text(text, tool, prompt_text)` and `codex_provider.analyze_image(png_bytes, tool, prompt_text)` functions.
- Use the supplied `tool["input_schema"]` as the Codex structured output schema and validate the returned JSON with `jsonschema` before returning a dictionary.
- Return `None` for handled CLI, timeout, parse, or schema failures. Do not route a failed request to another provider.
- Add an optional PNG image argument to `ExecTransport.request`. Write bytes to a temporary `.png` file, pass its path with `-i`/`--image`, and remove it on success, failure, timeout, and cancellation.
- Use the one-shot `ExecTransport` path for these analysis calls; this is the verified `codex exec` path for image input and `--output-schema`. Do not change conversation transport selection or the app-server protocol in this work.
- Add Codex to `ANALYSIS_PROVIDERS`; permit `codex` in `ANALYSIS_PROVIDER` validation.
- Update `.env.example` and provider/configuration documentation for `ANALYSIS_PROVIDER=codex`, Codex CLI installation, and `codex login`.
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
  -> codex_provider.analyze_text / analyze_image
  -> asyncio.run(one-shot ExecTransport.request(...))
  -> codex exec --output-schema [ -i temporary-page.png ]
  -> strict JSON and caller-schema validation
  -> dict, or None on a handled failure
```

`analyze_text` sends the supplied text and task prompt as request content. `analyze_image` sends the task prompt as request content and the PNG through the CLI image-input flag. The analysis schema is an output contract only: Codex must not execute application or game tools.

The adapter must use existing Codex timeout and input/output limits. Because synchronous calls create short-lived event loops, the implementation must verify that request admission/concurrency limits still apply across those calls; a loop-local `asyncio.Semaphore` alone does not coordinate separate `asyncio.run` loops. Do not log prompts, extracted document text, image bytes, image paths, credentials, or full environment values. Diagnostics may include provider, task kind, elapsed time, and safe error category.

## Configuration behavior

The intended configuration is:

```dotenv
LLM_PROVIDER=codex
ANALYSIS_PROVIDER=codex
```

Conversation and analysis both use the authenticated Codex CLI. The child process receives only its existing environment allowlist; analysis must not require or read `OPENAI_API_KEY`. This guarantee is scoped to conversation and analysis provider calls. RAG embeddings remain a separate configured capability and are outside this guarantee.

## Pre-implementation real-PDF benchmark gate

Before changing runtime code, use one real scenario PDF containing selectable-text pages, at least one scanned/text-image page, and at least one map or diagram page. Use direct authenticated `codex exec -i --output-schema` calls as a transport proof of concept; do not build the adapter first.

Record:

- Total PDF pages and the number of pages that the existing PDF repair and page-image/map flows would send for analysis.
- Per-call process startup and end-to-end latency for every analyzed page, including median, p90, p95, maximum, timeout/error count, and total wall time. Separate process startup from model completion where instrumentation permits.
- ChatGPT plan usage/quota immediately before and after the run. Use CLI/provider-reported usage where available; otherwise record the account's displayed before/after quota and label it as an estimate. Do not infer exact token or quota consumption when the service does not expose it.
- Scanned-page recognition quality against a source-checked reference: required text/fields recovered, incorrect values, and unsupported additions.
- Map/diagram quality against a reference: labels/rooms and visible connections correctly identified, missed items, false connections, and invented items.
- Codex CLI version, selected model/reasoning setting, page image dimensions, schema, and whether calls were sequential or concurrent.

The report must state whether one process launch per page is acceptable for a full scenario import. If latency or plan quota is unacceptable, revise the design before implementation (for example, investigate safe batching or a bounded persistent transport). Marco reviews the measurements and any resulting design change before implementation begins.

## Failure and privacy behavior

- Parse exactly one JSON object; reject malformed JSON, duplicate keys, non-object output, and values that fail the supplied input schema.
- Treat missing CLI/authentication, unsupported image input, nonzero exit, timeout, output/input limit, cancellation, and schema mismatch as `None` using the same handled-failure convention as existing analysis providers.
- Never invoke CoC tools or perform application mutations from this adapter.
- Always clean temporary image/schema files. Preserve existing subprocess cancellation and process-group cleanup behavior.
- Do not automatically retry a failed image page by sending it to another provider; the current consumer decides how to handle `None`.

## Testing plan

- Offline unit tests with a mocked transport for text and image requests, including the schema argument, image flag/path, and temporary-file cleanup on success and failure.
- Validate correct output and `None` for malformed JSON, duplicate keys, wrong JSON types, invalid schema, timeout, nonzero exit, cancellation, and output/input limits.
- Test provider registration and configuration acceptance for `ANALYSIS_PROVIDER=codex` without requiring `OPENAI_API_KEY`.
- Add an opt-in authenticated smoke test, disabled by default and explicitly enabled by a test environment variable. It performs one text analysis and one image analysis with the installed CLI and verifies the returned schema. It must not run in normal CI.
- Run the real-PDF benchmark above before implementation, then repeat a bounded sample after implementation to verify adapter parity without silently increasing calls per page.

## Decisions for review

1. Review the pre-implementation PDF benchmark and decide whether per-page CLI startup and ChatGPT plan usage are acceptable.
2. Confirm the analysis adapter should use `ExecTransport` even when conversation uses app-server; image input and schema output are verified on `codex exec`.
3. Confirm the concurrency limit expected for synchronous worker/process callers, given the existing Codex semaphore is event-loop-local.
4. Confirm documentation should state that Codex conversation/analysis need no OpenAI key while optional RAG embeddings remain separate.

Do not begin implementation until Marco approves this spec and the pre-implementation benchmark decision.
