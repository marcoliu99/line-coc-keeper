# PDF source readiness and derived-feature quarantine

## Problem and scope

A generated map's missing/invalid/incomplete graph currently blocks publication of otherwise safe scenario source. Separate approved-source readiness from derived feature readiness without modifying OCR acceptance, Docling, map generation/validation/repair/certification, or gameplay movement semantics.

## Contract

Pages retain source `disposition` (`accepted`, `legacy_route`, `needs_review`) and add `publication_severity` (`HARD_BLOCK`, `SOFT_REVIEW`, `NONE`). Source failures alone cause `needs_review` / `HARD_BLOCK`. Map failures retain their exact map status as `derived_feature_warnings`, private provenance and original image, exclude the page from gameplay maps, and produce `SOFT_REVIEW` only when its source is safe. HARD_BLOCK takes precedence if both occur.

Source-blocking failures include unresolved source numeric fields/mechanics, unsafe layout ordering without a safe fallback, required image-only source without independently authoritative transcription, and missing playable source. A graph certificate is not a source transcription. A positively verified text-free illustration needs no OCR; diagnostic OCR noise does not revoke that classification. Unknown image content remains source-blocking, never guessed decorative.

Scenario reports add `scenario_readiness` (`READY`, `READY_WITH_WARNINGS`, `BLOCKED`), `hard_block_pages`, `soft_review_pages`, and per-page `map_status`. `blocked_pages == hard_block_pages`. `review_pages` may include non-blocking diagnostics. The library rejects hard source failures and retains its existing graph certificate guard; unsafe map candidates never enter scene_maps.json or state.scene_maps. Publication report contains sanitized status/warnings; candidate graph/repair provenance remains private.

Safe draft pages (accepted / legacy_route / soft_review compatibility) with matching source, selected-text hash and extraction identity can resume with no graph. An attached gameplay graph still requires a valid image-bound certificate. Soft map failures are not retried by Continue; remaining source hard blocks are retried normally. Increment pipeline identity for this policy change, preserving current OCR/map certificate versions.

Successful first-time upload activates canonical library source with original images and warns that unverified pages' Map Engine is disabled. Deferred upload choices retain the warning too. No continue loop is requested for soft-only failures.

## Verification

Test public extraction/publication/draft/activation seams with safe source + invalid/incomplete/provider-failed map, image-only source failure, soft reuse without provider redispatch, and first-time 30 safe pages + one invalid map. Assert unsafe graph absent in library/runtime while canonical source remains available. Check conflicting mechanics still block and illustrations remain non-blocking. Run pytest, Ruff, mypy, compileall and diff check. Real provider validation is not required to prove this publication policy; prior graph-correctness evidence remains pending.

Private graph provenance is archived as `.ingestion-provenance.json` (0600) in the atomic scenario publication, separately from sanitized `parse_quality.json`. It is never loaded as gameplay context. This preserves evidence after the successful draft is discarded.
