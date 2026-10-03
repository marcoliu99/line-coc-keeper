# Map connectivity reliability follow-up

## Scope
Continue PR155 from 7f3e109. Only map extraction/validation and map-provider regressions. Existing two-phase extraction, targeted patches, certificate, source/publication hard-soft policy, OCR, Docling and gameplay remain unchanged. No real Haunting/Beacon requests or use of their fifth requests.

## Changes
Closed traversal/compass tool enums and sanitized provider diagnostics already exist: preserve them. Tighten map evidence traversal and compass validation to canonical schema values only; descriptions belong in evidence. Phase1 generation AND location audit use inventory timeout (30s). Connectivity, targeted repair and final image audit use their respective settings (60s). Generic image timeout is unchanged; every dispatch reserves durably before a single zero-retry request, including timeouts.

## Tests and trust boundaries
At public map analysis/provider/graph-builder seams, simulate Phase1 under30s and Phase2 above30s/below60s without sleeping. Actual OpenAI SDK transport stub times out during Phase2: assert three total stage reservations/transports, exactly one for connectivity, failure type/status/timeout preserved, no repair or final audit. Cover connection error, missing/invalid tool and malformed JSON diagnostics without source data. Exercise two missing locations plus invalid scoped edge with a patch preserving unrelated edge, certificate replay, out-of-scope patch rejection, and unresolved entry remaining incomplete/playable. Final audit only runs after error-free build. Do not regenerate inventory/graph or loosen validation.

## Verification and remaining evidence
Run pytest, ruff, mypy, compileall and diff check; standards/spec review against 7f3e109. Store only sanitized counts/status/errors/timing. Real Haunting/Beacon correctness remains pending until separately authorized validation with new budgets.
