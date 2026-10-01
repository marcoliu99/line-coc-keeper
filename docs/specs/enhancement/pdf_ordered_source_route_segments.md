# Ordered source-backed route segments

Status: proposed. PR #155; enhancement/pdf-multicolumn-ingestion.

## Goal and scope
Preserve source-defined sequential obstacles. One barrier is one state transition. Do not modify visual Phase 1/2, OCR, Docling, source publication policy, ordinary visual movement, or existing secret-route authority rules.

## Schema
Retain source_topology directed edges for existing single-barrier routes. Add source_route_chains containing route id, ordered segments (id/from/to/barrier_id/route_kind), independent barrier metadata, and canonical source evidence. Each segment projects one source_topology edge with its own condition key and parent route/barrier identity; never project an end-to-end shortcut. Preserve explicitly reciprocal single-barrier semantics.

Add source_transit_nodes outside the visual inventory, containing stable source_transit_<hash> id, kind=source_transit, player_label="", source evidence, and no invented story name. A transit exists only when an affirmative canonical assertion explicitly describes an enterable intermediate space. Named intermediate locations must bind uniquely to canonical inventory. Unsupported or ambiguous chains fail closed with private diagnostics, without blocking scenario publication. No vision or narration inputs authorize chains.

## Extraction contract
Extend the existing conservative deterministic English assertion grammar with an explicit ordered-chain form documenting origin, first barrier, enterable intermediate space, second barrier, and destination. Preserve exact physical page, Unicode span and SHA-256 evidence. Do not infer chains from adjacent walls or separate unrelated assertions. Unrecognized prose never authorizes topology. Synthetic canonical-source fixtures demonstrate the grammar; they do not claim real Corbitt PDF validation.

## Runtime
Persist each barrier receipt separately, bound to timeline, map, graph/certificate, route, barrier and source identities. Opening A changes only A; failed B retains opened A and leaves B retryable. Derived progress is the longest consecutive opened prefix, never the authority. Validate the current location and preceding segment availability before an action on a deeper barrier. Preserve existing permission and fresh-state mutation boundaries.

Extend /coc route <page> <route-id> <barrier-id> opened|discovered|failed [consequence], retaining old single-route syntax. Hidden discovery is separate from opening: first-layer discovery does not reveal downstream barriers or transit; later layers require physical reachability and their own supported discovery. Movement/public projections use only individually available segments and never expose the entire hidden chain. No inferred difficulty, tool, HP, armor, threshold or one-shot restriction. Narration and ordinary rolls cannot transition barriers.

## Certificates and visual separation
Bind ordered segments, barrier metadata, transit nodes, source evidence, ordering and merged graph hashes. Verify by deterministic canonical-source replay, including transit evidence. Strip every source overlay when reusing independently certified visual proof. Image audit still sees only visible inventory and connections; source breakable walls may coexist with visible solid walls. Changes to ordering, barrier count, source spans or transit evidence invalidate certification and persisted receipts.

## Tests and verification
Vertical TDD at extraction/certification, runtime persistence, movement/projection and command seams. Include two-wall Corbitt-shaped canonical fixtures; independent A/B opening, B failure after A, final completion, unreachable B rejection, transit evidence/no-name cases, incremental hidden visibility, certificate reorder and source identity invalidation, single barrier, secret door and Beacon visible topology regressions. Run pytest, ruff check ., mypy app, python -m compileall app tests and git diff --check; then independent Standards and Spec reviews. Record results and remaining unsupported source grammar explicitly.
