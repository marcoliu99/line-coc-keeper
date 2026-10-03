# Source-backed hidden/conditional topology validation

Branch: enhancement/pdf-multicolumn-ingestion. Review baseline: 1347c3e. Implementation: 49b7e9b; blocking review fix: df5ec9c. Date: 2026-10-02.

## Changed files

- app/pdf_source_topology.py
- app/map_routes.py
- app/pdf_map_analysis.py
- app/pdf_map_evidence.py
- app/pdf_loader.py
- app/scene_map.py
- app/models.py
- app/scenario_library.py
- app/commands/handlers/map_handler.py
- app/commands/router.py
- app/legacy_commands.py
- app/keeper.py
- tests/test_pdf_hidden_topology.py

The English/Chinese spec, catalog and this sanitized validation evidence accompany the production changes.

## Production behavior verified

25 new regressions exercise production source extraction, visual graph build/audit/certificate replay, PDF loader, publication/library read, private drafts, GroupState persistence, real command routing and map runtime. Image inference and the subsequent AI turn are stubbed; results are regression evidence, not real PDF vision validation.

- Source routes distinguish hidden_passage/secret_door from blocked barriers (breakable_wall, blocked_passage, sealed_door, collapsible_barrier and explicit conditional_route).
- Source authority requires final canonical source, exact inventory endpoints, physical page/spans and full-source/span hashes. Private challenger prose and wall adjacency cannot authorize source routes. Vision schemas remain visual-only; wall/hidden source-kind proposals are rejected.
- Hidden routes default hidden/undiscovered; barriers default blocked. Conditions preserve explicit source text and use a route-scoped world-state key. No skill difficulty, HP, armor or tool requirement is invented. Fail-forward requires an explicit necessary-for-progress assertion; failed outcomes remain retryable.
- Synthetic Corbitt-named source routes survive an image audit reporting a solid wall because the audit only validates visual graph evidence. They remain unusable until an authorized state transition. Explicit two-way barriers share a condition and open in both directions.
- Ordinary visible exits and /coc where omit undiscovered source routes. A KP discovery makes them available. Named and directional attempts do not move through blocked barriers. Narration alone cannot open them.
- /coc route page route-id opened|discovered|failed [adjudicated consequence] is KP-only, rechecks full library source/map certification and persists timeline/graph-bound outcomes. It follows the normal action/check/damage workflow; there is no automatic transition from an arbitrary roll. Static topology and certificate are never mutated by gameplay availability.
- The certificate extension binds visual hash, source topology hash, condition metadata hash, canonical source hash and merged hash. Deterministic source replay rejects tampering even when graph hashes are recomputed. Changed library source quarantines the map while canonical scenario text still loads.
- Resume discards source overlays, reuses only visual proof and rebuilds against final book source with zero added provider requests. Ambiguous/unmatched supported source assertions quarantine only the map as incomplete; scenario remains READY_WITH_WARNINGS.
- Beacon-named visible Service Room/Lamp Room/Lantern Gallery fixture preserves normal stairs/door movement and visual-only certification.

## Review

Standards: 0 documented breaches; optional P3 certificate TypedDict remains deferred. Spec: a reproducible P1 was found where an ordinary visible door bypassed a matching source-sealed barrier. df5ec9c fixes visual-exit projection plus named/directional gates, including failed/opened persistence. Final Spec review: 0 remaining actionable findings. Alternate visible travel cannot authorize an undiscovered route in another direction.

## Checks

pytest: 2147 passed, 2 skipped, 10 dependency deprecation warnings, 152 subtests passed in 29.98 seconds. Ruff: PASS. Mypy app: PASS (137 source files; existing annotation-unchecked note). Compileall app tests: PASS. Git diff --check: PASS. Latest origin/main_v2 is an ancestor; no unresolved conflicts.

## Limits and rollout

This round dispatched zero provider image requests. The Haunting p7 and Beacon p16 were not rerun, and these synthetic regressions do not certify their real maps. Production rollout remains HOLD pending real map/source evidence and existing rollout gates. Extraction is conservative English grammar with exact known inventory endpoints; it does not claim arbitrary-language/prose coverage or create invisible rooms. Raw PDF images, provider outputs and actual candidate graphs remain outside repository evidence.


## Availability follow-up (1723a31; review base 7fa3db6)

Reproduced a second P1: an activated source route could bypass another blocked source barrier on the same connection. The existing barrier gate now also applies to activated source exits and named movement. Unknown route direction cannot establish an alternate path around a known blocked endpoint barrier. Four added regressions cover either single activation versus both, actual library/KP persistence with failed/opened outcomes, unknown compass, and a genuinely available alternate direction. Final Standards and Spec follow-up reviews: zero new actionable findings. Full checks above reflect this follow-up. No additional provider requests.
