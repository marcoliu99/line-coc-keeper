# Codex Analysis Provider Design Spec

## Goal

Allow deployments configured with `LLM_PROVIDER=codex` to use Codex OAuth for both player conversation and document/scenario analysis. Once this capability is implemented and verified, `ANALYSIS_PROVIDER=codex` must be valid and the deployment must not need an OpenAI API key for those paths.

## Current behavior

Conversation providers are registered in `app/providers/registry.py`. Codex is available for `run_conversation`, but `ANALYSIS_PROVIDERS` deliberately excludes it. Analysis consumers use the separate capability for text and image tasks, including scenario indexing/comparison/intro, pregen extraction, scene maps, PDF repair, and Keeper-side structured extraction. `app/config.py` rejects `ANALYSIS_PROVIDER=codex` while Codex is the conversation provider.

The Codex conversation provider already invokes Codex CLI through the configured `exec` or `app-server` transport and validates structured JSON decisions. It does not expose the synchronous `analyze_text` and `analyze_image` functions expected by the analysis consumers.

## Scope

- Add Codex structured text analysis and image analysis capabilities through the existing Codex CLI/OAuth transport.
- Register Codex as an analysis provider and accept `ANALYSIS_PROVIDER=codex`.
- Preserve the current analysis provider interface and result validation/fallback behavior.
- Update `.env.example` and provider/config documentation to explain a Codex-only setup and the `codex login` requirement.
- Add deterministic tests for structured output parsing, valid/invalid schemas, text and image request handling, timeout/error behavior, and provider selection.
- Confirm that Codex-only mode does not require or read `OPENAI_API_KEY` for conversation or analysis.

## Non-goals

- Changing the CoC conversation agent loop, tools, or game rules.
- Removing support for OpenAI, Anthropic, or Gemini analysis providers.
- Automatically falling back to a paid API provider when Codex CLI fails.
- Replacing or redesigning `exec` and `app-server` transports beyond what is required to support analysis calls.
- Migrating unrelated tasks that do not currently use the analysis provider registry.

## Interface and data flow

Keep the current consumer contract: `analyze_text(text, tool, prompt_text)` and `analyze_image(png_bytes, tool, prompt_text)` return a parsed tool-argument dictionary or `None` on a handled provider/validation failure, matching the existing providers' conventions.

```text
analysis consumer
  -> ANALYSIS_PROVIDERS[ANALYSIS_PROVIDER]
  -> Codex analysis adapter
  -> Codex CLI transport with one allowed structured result
  -> JSON/schema validation against the requested tool
  -> parsed dictionary returned to existing consumer
```

The adapter must not execute CoC business tools. The supplied analysis schema is an output contract only. Image bytes must be passed through the Codex-supported image input path without writing secrets or image contents into logs. The implementation must document any Codex CLI limitation on image input or structured output before claiming image-analysis parity.

## Safety and failure behavior

- Apply configured timeout, concurrency, input-size, and output-size limits.
- Validate JSON strictly, reject duplicate keys and unexpected fields, and validate returned arguments against the caller-provided schema.
- Treat timeout, nonzero process exit, malformed output, unsupported image input, and schema mismatch as analysis failure using existing consumer semantics; do not silently switch providers.
- Keep stderr and diagnostics free of prompt bodies, image data, credentials, and full environment values.
- Ensure cancellation and subprocess cleanup follow existing Codex transport behavior.

## Configuration

After implementation, support:

```dotenv
LLM_PROVIDER=codex
ANALYSIS_PROVIDER=codex
```

Codex CLI must be installed and authenticated with `codex login`. `OPENAI_API_KEY` should be optional in this mode. Other provider keys remain optional unless another explicitly configured feature uses them.

## Testing plan

- Unit tests for text/image adapter request construction and strict tool-argument parsing.
- Unit tests for malformed JSON, invalid schema, duplicate keys, timeout, subprocess failure, and cancellation.
- Config/registry tests proving `ANALYSIS_PROVIDER=codex` resolves to Codex and Codex-only configuration does not require API keys.
- Consumer tests for representative text and image analysis dispatches.
- Mock Codex transport in automated tests; do not incur live API usage in the unit suite.
- Before switching the deployment `.env`, run an opt-in authenticated smoke test for one text analysis and one image analysis if the installed Codex CLI supports images, then run relevant provider and application tests.

## Tradeoffs and open decisions

- The existing conversation transport methods are asynchronous, while analysis consumers currently call synchronous provider methods. The implementation should avoid blocking the event loop or changing every consumer in this change. Prefer a bounded synchronous adapter around the existing CLI transport only if it is safe in all current call contexts; otherwise propose a narrowly scoped async-compatible migration in the implementation plan before changing consumer interfaces.
- Codex CLI image input support and structured-output parity must be verified against the installed CLI version. If image analysis cannot be supported safely, keep `ANALYSIS_PROVIDER=codex` disabled until the capability has a tested path rather than silently routing images elsewhere.
- An authenticated live smoke test may be needed to verify OAuth and CLI behavior, but normal tests must remain offline and deterministic.
