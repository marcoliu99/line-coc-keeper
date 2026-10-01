# Source-backed hidden map topology

Status: implemented; verification and two-axis review recorded in the validation companion. PR #155, enhancement/pdf-multicolumn-ingestion.

## Goal and authority

Preserve the existing two-phase visible extraction pipeline. Vision may authorize only door, open_passage, stairs and one_way edges with image traversal evidence. Canonical scenario source may authorize hidden_passage, secret_door and conditional_route. A visible wall rejects vision-only traversal; it does not negate a proven source-backed hidden route. No provider requests are needed for this change.

## Data model

Keep ordinary visual room exits unchanged. Add an internal source_topology collection to the merged map, separate from ordinary exits. Each directed route has existing from/to room IDs, a closed route type, authority=scenario_source, visibility=hidden, availability=undiscovered or blocked, and optional explicit condition. Evidence includes physical source page, exact source span offsets, span SHA-256 and full canonical source SHA-256. No raw source quotes are published in parse_quality. Visual edges retain their visual authority; hidden kinds never enter Phase 2 tool schemas.

## Source-bound extraction and deterministic merge

A dedicated import-only module receives final selected, source-safe canonical pages, after source integrity gates. It conservatively recognizes explicit route assertions and exact endpoint names from validated inventory. Extraction supports documented explicit connection/secret-door sentences, with fixtures for their accepted grammar; ambiguous assertions or unmatched endpoints produce private diagnostics, never inferred rooms or routes. Rejected OCR, unverified AI prose, vision descriptions and runtime narration are not inputs. The extractor returns immutable candidates bound to source spans. Deterministic merge replays evidence against exact canonical source and checks unique routes, supported types and existing endpoints before attaching source_topology. Extraction failure remains a derived warning, without changing source publication severity.

## Audit and certificate

Image audit and targeted visual repair continue operating on the visual graph alone. They cannot remove source routes. The merged certificate binds the existing visual certificate/graph hash, hidden-topology hash, full canonical source identity/hash, and merged graph hash. Verification deterministically re-extracts/replays source candidates, rather than trusting stored hashes alone. Visual-only certificates retain their existing validation contract. A hidden certificate requires exact canonical source at verification; missing or changed source fails closed for Map Engine.

Publication and library read use the exact full scenario.txt source, not an activation chapter window. Draft reuse must not trust a source overlay bound to an earlier book-wide selection: reuse certified visual evidence only, then reconstruct and certify the overlay after all final canonical pages are selected. Invalid overlays are quarantined; canonical source remains playable. Private provenance retains spans and proof; public reports contain only hashes, statuses, errors and counts.

## Runtime boundary

Hidden routes remain internal in source_topology and never enter rooms[].exits. Ordinary movement, visible exits, /coc where and public Keeper map projections use visual exits only. Add defensive rejection/filtering for source-authority or hidden metadata injected into ordinary exits. Only an explicit authorized KP outcome can activate source routes; undiscovered and blocked routes are unavailable to normal movement. Existing visual movement semantics remain unchanged.

## Tests and verification

Use production builder/certificate/publication/cache/runtime seams: reject visual wall; accept source-backed hidden route despite a wall; reject missing/altered evidence; preserve unrelated visual graph; suppress hidden route in public exits/prompts and normal movement; invalidate on source changes; replay valid visual certificate; preserve Beacon visible stairs. Add source input exclusion, ambiguous endpoint, condition, span tampering, draft reconstruction, library read and sanitized-report tests. Run pytest, ruff check ., mypy app, compileall app tests and git diff --check. No real map verification claims without a new real run.

## Limits

Conservative deterministic extraction does not promise to discover every prose-described secret route. Unrecognized routes remain absent with diagnostics where detectable. Source-backed endpoints must already exist in the validated location inventory; creating invisible rooms is outside this change. Canonical source safety, OCR, Docling, scenario readiness and gameplay discovery mechanics are unchanged.

## Approved scope extension: conditional barriers

The follow-up implementation request adds breakable_wall, blocked_passage, sealed_door and collapsible_barrier as source-only kinds. Their default availability is blocked; visibility is visible unless source explicitly says hidden. Conditions have a world_state key (deterministically scoped to the route evidence) and expected=true, plus exact explicit source condition text when present. Source states no implicit STR tier, HP, armor or tool requirement. Progression policy is retryable for explicit breakable barriers; fail_forward requires an explicit necessary-for-progress assertion. Failed attempts never permanently close a route.

Keep static source topology/certification immutable. Persist route outcomes separately in GroupState, bound to route identity and current timeline. An explicit authorized KP transition after the existing action/check/damage workflow records discovered/opened/failed, actor and optional consequence under the state lock. Narration is never a transition. Expose this narrow confirmation through /coc route; player requests are rejected. A failed result increments attempts, preserves availability and remains retryable. Only an opened barrier or discovered hidden route becomes available. Existing direct room-name movement must also respect unavailable source routes. Direction movement uses source compass only when explicitly stated; unknown compass is never guessed. Public exits omit undiscovered routes and include available routes after an authorized transition. Blocked visible routes return a generic actionable interaction without invented mechanics. Add condition metadata hash to the source certificate extension; the visual certificate remains unchanged.


## Implemented source grammar and runtime confirmation

The deterministic extractor currently supports affirmative English whole sentences of the form `A/The/There is a [two-way] [hidden] <route kind> connects/links <exact inventory label> to <exact inventory label>` or `leads/runs from ... to ...`. Optional `If/When <condition>,` preserves the source condition. An exact `to the east` (or supported cardinal/intercardinal/vertical direction) suffix supplies compass; absent direction remains empty. `; it is necessary for progress` justifies fail_forward. Two-way must be explicit; no reciprocal traversal is guessed. Repeated labels across floors cannot authorize endpoints. Unmatched supported assertions quarantine the map as incomplete, without source blocking. Other prose languages/forms are not interpreted by guessing.

`/coc route <page> <route-id> opened|discovered|failed [adjudicated consequence]` is restricted to the current KP and revalidates the published map/source certificate before persistence. IDs are available in private map artifacts; no public hidden-route listing is introduced. This confirmation follows ordinary action resolution; there is no automatic link from an arbitrary skill roll or narration to a barrier. The immutable certificate remains valid after runtime outcomes because outcomes live separately in GroupState. Timeline and graph identity mismatches disable old availability receipts.


## Availability follow-up: multiple source barriers

All matching source barrier conditions constrain a directed connection. An available source route must not bypass another blocked barrier on the same endpoints and compass. Apply the same gate to activated source-route projection, named movement and directional movement. Existing visual traversal in a distinct, unblocked direction remains available. Add a regression for sealed_door plus blocked_passage: no activation and either single activation remain blocked; both activations permit traversal. Source extraction, certificates and publication severity remain unchanged.

Unknown route compass cannot establish a distinct alternate path; matching blocked endpoint barriers remain constraints until their outcomes are available.
