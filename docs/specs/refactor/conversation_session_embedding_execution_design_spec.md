# Conversation session and embedding execution

[繁體中文](conversation_session_embedding_execution_design_spec_zh.md)

Status: implemented. Base: `main_v2` at `07d55a7`.

## Re-audit

The registry already centralizes provider lookup, but Assistant handles OpenAI continuation and timeline resets itself, while Executor and Narrator each negotiate dynamic tools, response stages and decision context. SDK tool loops are genuinely different and stay in their adapters. Scenario and memory RAG still duplicate embedding client creation/close, result ordering and incomplete-batch detection. The shared query cache already handles cross-RAG reuse and remains intact.

## Interfaces

`app/providers/conversation_session.py` selects a provider at call time, exposes its model/capabilities, constructs only supported stage options and owns OpenAI continuation identity/reset/callback. Agents retain game-specific tool filters, feedback, prompts and Executor's no-wrapup policy. Correction context and timeline mismatch reset the continuation; non-OpenAI adapters never receive OpenAI-only arguments.

`app/embedding_execution.py` executes complete ordered embedding batches with one synchronous client, bounded timeout, no SDK retries and close-on-all-paths. Missing key, failed call, invalid/duplicate/out-of-range index or incomplete batch returns `None` so each RAG owner applies its existing lexical fallback. Ranking, relevance gates, chapter policy and bilingual evidence remain in their RAG modules; the shared query cache continues to prevent duplicate query embeddings.

## Tests

Test session capability filtering and timeline/correction reset using fake providers. Test ordered results, failed/incomplete batches, close failures, shared query reuse and lexical fallback. Run full CI gates.
