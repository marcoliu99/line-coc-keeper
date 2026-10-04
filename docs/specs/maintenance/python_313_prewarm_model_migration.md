# Python 3.13 prewarm lifecycle and prepared-model migration

## Problem and goal

Python 3.13 lacks `ProcessPoolExecutor.terminate_workers()`. Scenario RAG prewarm must remain optional and must stop a stuck child without blocking Bot shutdown or the asyncio event loop. Prepared OCR and layout models also need a safe way to move from a cache to a stable production directory.

## Scope and non-goals

Use one `multiprocessing.Process` for scenario prewarm, start it in a worker thread, and stop it through public `terminate`, `kill`, and `join` APIs. Add one explicit model-copy CLI. Do not change `get_index`, RAG retrieval, PDF extraction, OCR or layout inference, dependency versions, or gameplay.

## State and file contract

No database or gameplay-state schema changes. The parent tracks the process and an async completion waiter. A finished waiter joins and closes the process handle, then removes the worker from the active set. Cancellation leaves unfinished child ownership with `shutdown_prewarm`; the start/stop lock and terminal stopping flag prevent a late spawn after shutdown or a close race. A normal exit reports `rag.prewarm.completed`; an unsuccessful exit reports `rag.prewarm.failed`. Shutdown keeps its grace period and `rag.prewarm.shutdown_degraded` event. If even `kill` does not finish, cancel the waiter so shutdown stays bounded.

The migration CLI requires both destination paths. Its default sources are `~/.cache/line-coc-keeper/paddleocr` and `~/.cache/line-coc-keeper/paddle-layout`; source overrides are explicit. It reuses `app.pdf_ocr.models_ready()` and `app.pdf_layout.model_ready()`. It validates all paths and sources before writing, copies only named model directories to sibling staging directories, validates again, then renames each staged directory into place. A partial old destination is backed up and restored if publication fails. Overlapping paths are rejected. Complete destinations are no-ops. Sources remain unless `--remove-source` is explicit; even then only the named model directories are removed. The CLI never downloads models or edits `.env`.

## Validation

Test responsive asyncio scheduling during a deliberately slow process start; normal completion and handle release before shutdown, wrapper cancellation, grace timeout, termination, kill fallback, and bounded shutdown after failed kill. Test complete/incomplete sources, dry-run, idempotence, partial destination replacement, rollback, path overlap, source retention, and explicit deletion. Run full pytest on Python 3.13 and 3.14, plus ruff, mypy, compileall, diff-check, and Python 3.13 GitHub CI.

## Tradeoff

One prewarm process is spawned per optional build instead of reusing a pool. This preserves the previous single-worker limit and provides the same public lifecycle API on Python 3.13 and 3.14. Model publication is atomic per destination directory; the two model families are not a combined transaction.
