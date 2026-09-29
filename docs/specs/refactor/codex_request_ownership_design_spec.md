# Codex request ownership

[繁體中文](codex_request_ownership_design_spec_zh.md)

Status: implemented. Base: `main_v2` at `07d55a7` (includes PR #140).

## Problem

Conversation requests use a loop-local semaphore while synchronous text analysis uses a separate thread semaphore. Their combined active CLI count can exceed `CODEX_MAX_CONCURRENCY`. Each path independently handles queue timeouts and cancellation. The transport installs game tool instructions even for structured document analysis.

## Interface

`app/providers/codex_request_owner.py` owns one process-wide FIFO admission queue, request deadlines, active tasks and shutdown. Both provider entry points acquire a lease before creating or using a transport. The lease releases on success, failure or cancellation; a queued cancellation never releases another request's slot. The conversation's turn deadline and `CODEX_TIMEOUT` bound both queue and transport time; text analysis runs its async request in its worker thread with the same owner. Shutdown blocks new admissions, cancels admitted and queued requests across event loops, and awaits a thread-safe lease-release acknowledgement after each admitted request has closed its transport. The provider installs a fresh owner only after that shutdown completes.

The transport receives task instructions from its caller: game protocol for conversation, analysis-specific instructions for structured text. Exec and app-server remain transport adapters. No PDF, OCR, map or pre-generated character task changes provider.

## Tests

Use controlled transports to verify one mixed conversation/analysis slot, queued cancellation, shutdown across loops, deadline including queue wait, and analysis prompt isolation. Preserve Codex reasoning-effort logging from PR #140. Run the full CI gates.
