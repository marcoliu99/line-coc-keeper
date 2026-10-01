# Postmerge integrity implementation task graph

Canonical [design](postmerge_ingestion_combat_integrity_design_spec.md) / [繁中](postmerge_ingestion_combat_integrity_tasks_zh.md). All tickets are planned and blocked on final implementation authorization. No GitHub Issues and no runtime work started.

```text
T0 integrated baseline
  ├─ T1 injury lifecycle ─┐
  └─ T2 layout/assets ───┴─ T3 import lifecycle/preflight/identity
                              └─ T4 immutable source versions
                                  ├─ T5 transition/history/obligations ─┐
                                  └─ T6 map/card/NPC boundaries ──────┴─ T7 weapon/correction
                                                                          └─ T8 integrated validation
                                                                              └─ T9 review/release gate
```

| Ticket | Dependencies | Ownership/scope | Acceptance |
|---|---|---|---|
| T0 | authorization | Actual main/integrated PR155+156 baseline; spec/catalog alignment | Pin heads; retain both specs; classify all findings with evidence, no fake combined run |
| T1 | T0 | combat_flow/resources/models, owned check integration | R156-01 purpose-specific checks; exact retry/restart and transaction failure, original RNG |
| T2 | T0 | pdf_layout/adapters, loader/draft snapshots | R155-01/02 symmetric geometry and complete accepted assets; failed checkpoint/downstream recovery |
| T3 | T1,T2 | import orchestration/drafts/system lifecycle | R155-03/05/11 fresh eligibility before side effects; inert drafts; late workers; one offloop identity |
| T4 | T3 | library/version refs/models/context/templates/facts/checkpoints/images | R155-04 immutable complete revisions and pinned all consumers; two-group interleaving, no old migration/pruning |
| T5 | T4 | activation/transition/history/provider context, continuing source refs | R155-06 all entry gates, current source update safety, future compatibility, rollback lineage, one opening |
| T6 | T4 | scene_map, loader publication, pregens, scenario_index/combat alias boundary | R155-08/09/10 malformed graph safe failure, complete source coverage, exact safe NPC lookup |
| T7 | T5,T6 | combat weapon state/guards, natural correction admission | R156-02 stable-instance fault/clearing, aliases; R155-07 whole-claim review without fixed judge |
| T8 | T7 | Public/repository integrated regressions + bilingual validation | Full checks, real available corpus/CPU smoke, every finding status/evidence; no invented results |
| T9 | T8 | Two-axis review, fixes, docs/catalog completion | All issues closed; full final checks; clean-start precondition documented; no unrequested deletion/deployment |

Deliver separately reviewable checkpoints: T1–T3 containment; T4–T5 coherent new-source lifecycle; T6–T7 domain boundaries; T8–T9 validation. Shared-file changes serialize through dependencies; implementations may split commits without weakening the full contract. Production rollout waits for ALL scoped tickets/acceptance (Q12). Operator performs the one-time old-data reset, not an implicit runtime bypass of combat guards.
