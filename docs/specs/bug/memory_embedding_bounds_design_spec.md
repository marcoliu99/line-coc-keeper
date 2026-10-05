# Bounded long-term memory embeddings

[繁體中文](memory_embedding_bounds_design_spec_zh.md) | [Docs index](../../README.md)

## Status and goal

Category: `bug`. Status: **implemented**. Source: finding CS-005 of the 5-player / 500-turn Camp Sunny validation (Workstream D). Based on `main_v2` at `aa79f22`.

At the end of the run all eight long-term memory chunks lacked embeddings, and BM25 kept retrieval working without anyone noticing. One chunk was ~18,400 characters (~19,000 tokens). The provider's rejection payload was not captured, so overlength is the likely cause, not a proven sole one. The cause in the code is certain on one point: every log trim became exactly one chunk and one embedding input, however long, and a failed embedding left no diagnosis and no second attempt.

## Contract

1. **Bounded before it is sent.** A trim is packed, in order, into parts that each fit `MEMORY_EMBEDDING_MAX_TOKENS` (default 6000; the default model accepts ~8k), measured with the embedding model's tokenizer rather than characters (`app/memory_chunking.py`). A message longer than a part is cut at sentence ends, then by measured length. A trim that fits stays one chunk, stored exactly as before.
2. **Meaning is kept.** The parts of a trim are stored as ordered children of one parent (`parent_id`, `part_index`, `part_count`), in the same transaction as the log trim, with their own vectors, the timeline and revision, and the provenance of the messages each holds. Replaying the commit finds the parent and stores nothing. A search folds the parts of one memory into a single result, in order, scored by its best part, so fragments are not read as separate facts; the row carries the corrections (`superseded_by`) and message provenance of every part folded into it.
3. **A failure is diagnosed, never quoted.** `rag.embedding_fallback` now carries the provider, operation, input count and size (bytes, an upper bound on tokens), status class and code, the provider's error code and type, whether retrying can help, and the fallback chosen. The provider's message text, which can echo a credential, is never recorded. The same diagnosis is stored on the chunk.
4. **Lexical-only memory is not a steady state.** Each search logs `memory.embedding_gap` when chunks lack vectors. Each maintenance pass first gives up to `MEMORY_EMBEDDING_BACKFILL_LIMIT` (4) such chunks another chance, each at most `MEMORY_EMBEDDING_MAX_ATTEMPTS` (3) times and never again after a refusal that retrying cannot fix (a 4xx other than 408/409/429, or exhausted quota). A missing embedding key is configuration, not a refusal: it is not counted as an attempt, so memory stored before a key was configured is embedded once one is. A chunk stored before parts existed and too long to embed is split in place during that retry. The work happens outside the state lock and is applied under it to the chunk as it then is; correction annotations and appends take the same lock, so none of them overwrites another.
5. **BM25 stays.** A chunk without a vector is still found by keyword, and a later success restores vector retrieval.

## Contract kept

The stored shape of a chunk that fits, the search interface, the idempotency of a trim and the BM25/cosine blend are unchanged. Existing stored memory needs no migration: its gaps are filled by the retry.

## Enforcement

`tests/test_memory_embedding_bounds.py`: packing (order, losslessness, provenance, sentence cuts, token versus character measure); nothing longer than the limit reaches the provider; parts are traceable and replay-safe; folded search results; each provider status classified without leaking its message; a later success restores vector retrieval; a refusal is not retried and a retryable failure only up to the cap; the per-pass limit; splitting a legacy chunk; no overwrite of a concurrent change; maintenance never stores one unbounded chunk.

## Not covered

No real embedding call was made, so whether the real provider rejected the 19k-token chunk for length is still unconfirmed; the bound removes that cause either way. Splitting a message across parts can separate a corrected excerpt from its receipt in `mark_superseded_receipt`. A legacy chunk split during retry gives each child the whole chunk's provenance list.
