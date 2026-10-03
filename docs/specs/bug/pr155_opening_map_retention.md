# PR155 opening analysis and map retention

Baseline: `40f95d6a4de9c5745f72424bcdea6dc711593a11`.

## Scope and invariants

A: Codex general analyze_text accepts the existing bounded caller contract (timeout, max_retries=0). Effective deadline is the minimum of caller and request-owner bound; no retries added. Source-analysis implementation version invalidates stale contract failures, preserving old consumed records and replaying completed current-version evidence. Safe diagnostics distinguish absence, failure, contract error and replay.

B: Reparse preserves a still-valid published map and all supporting private provenance independently from text selection. Replay against final merged source; source-incompatible maps are disabled with artifact conflict, never re-certified or scenario-blocking. Conflicting verified candidates keep compatible published authority. Existing atomic publication and active-game preservation remain unchanged.

Tests at user-specified public seams: scenario_intro through Codex transport, source_analysis durable replay, scenario_library save/load_context and correction activation. Red then minimal green; full suite once; Standards/Spec review after implementation.

Non-goals: PDF admission, source-boundary rules, OCR, map extraction/certificates, topology discovery, gameplay rules, image reviews, corpus ordering gates. No six-book provider rerun. Real isolated Codex start uses synthetic source; published corpus reload/start regression does not re-import pages. Private raw evidence remains outside repository.

## Implemented evidence

Codex honors keyword bounds; analysis alone uses CLI request/stream retries zero through an OpenAI OAuth config alias (CLI 0.159.3 reserves built-in provider IDs). Runtime transport defaults are unchanged. Analysis cache identity includes implementation version 2, provider/model, source/window, schema and prompt. Failed attempts remain consumed. Source and opening observability records safe statuses/types rather than request prose.

Certified map provenance is independently selected, replayed against final merged source and persisted with original image/PDF identity. Incompatible certificates are never reissued: only map artifact/position is disabled; source/game remain active. Conflicting verified candidates retain valid published maps. Rejected reparse maps cannot replace them; first-publication certificate gate remains strict.

See [validation](pr155_opening_map_retention_validation.md) and sanitized results. This patch does not change admission, source-boundary decisions or corpus ordering gates.
