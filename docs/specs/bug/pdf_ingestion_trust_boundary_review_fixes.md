# PR155 trust-boundary review fixes

## Goal and scope
Fix reproducible F1–F6 on enhancement/pdf-multicolumn-ingestion. Canonical source corruption blocks publication; unsafe derived maps disable only Map Engine. No OCR, map extraction, gameplay or publication architecture redesign.

## Changes
- F1: inspect final selected source for observable corrupted dice, percentages and signed mechanics; hard-block unresolved corruption, never rejected challenger history. Bump extraction identity to invalidate previously unsafe caches.
- F2: compute clipped raster rectangle union area, not maximum or summed image area. Large raster coverage with insufficient body text requires independent image transcription; small decoration with normal prose does not.
- F3: library read boundary loads only structurally valid, replay-certified maps bound to archived PDF and page image. Missing/legacy certificates quarantine maps while scenario text remains usable.
- F4: connectivity and patch traversal type/basis use door/open_passage/stairs/one_way enums and canonical 18-direction compass enum. Descriptions remain evidence.
- F5: context-local sanitized image failure diagnostics preserve provider, stage, error type, HTTP status, timeout and timing without prompt/image/key/raw response or exception body. Preserve dict-or-None adapter interface.
- F6: all MarkItDown compatibility SDK clients disable hidden retries and use configured PDF image timeout. One reservation permits at most one transport request, including failures; no refunds.
- Map timeouts are stage-specific: inventory 30s; connectivity, repair and audit 60s. Retries remain zero; durable reservation precedes dispatch.

## Verification
Regression tests at extraction/publication, library activation, map analysis and actual SDK transport seams. Cover clean canonical source with rejected challengers, repaired dice, prose corruption, tiled/overlapping/decorative rasters, legacy and current certified maps, closed schemas, timeout/401/429/invalid JSON/missing tool diagnostics, privacy, one transport per reservation. Run pytest, ruff, mypy, compileall and diff check, then standards/spec review against 9d331bb.

## Non-goals and remaining evidence
No real Haunting/Beacon requests this round. Raw evidence stays private. P3 cleanup deferred. Production rollout remains hold until separately authorized real map/image-only validation succeeds.
