# Postmerge ingestion and combat integrity remediation

[繁中](postmerge_ingestion_combat_integrity_design_spec_zh.md)

Status: backlog; interview in progress. Runtime implementation is not authorized by these answers. Design premise: PR155 and PR156 are both integrated into main. The real documentation base is main_v2 at 6024adf (PR156 merged); PR155 was still open at 1c93712 when checked. This document does not claim a combined checkout was executed.

## Source and evidence

Operator review: /Users/marcoliu/Downloads/pr155_pr156_full_code_review.md, dated 2026-10-01, pinned PR155 cf62607 and PR156 897409d. Recheck findings against current heads before treating them as regressions. At PR156 1f89f6b, isolated probes confirm R156-01 (postcombat major-wound CON becomes stale on retry) and R156-02 (malfunctioned owned firearm reaches PLAYER_ROLL). The second probe covers declaration, not a complete malfunction/repair flow. PR155 1c93712 was statically checked: the later native-anchor changes do not alter the R155-01–11 paths; R155-06 is partially repaired by PR156 replacement guards. This is not a fresh dynamic or combined-tree validation. The old merge-conflict observation is integration history, not a remaining postmerge runtime defect.

## Confirmed decisions

- Q1 A: staged delivery. First repair check lifecycle, accepted asset preservation, stale publication and deterministic reading order. Then complete source-version/lifecycle migration, remaining domain boundaries and release verification. Each stage has executable acceptance and explicitly tracked remaining findings; no partial fix is presented as complete.
- Q2 A: a running game stays on its explicitly selected scenario version. Another group's reparse does not update it. Switching versions is explicit and coherent across text, images, maps and NPC/character source evidence.

- Q3 superseded: the operator will delete old scenarios before rollout, so historical scenario/source-version migration is out of scope. The exact cleanup boundary (library only versus all referencing games, drafts and checkpoints) still requires confirmation. No deletion has been performed by the agent. Newly created data still requires immutable references, safe retries and lifecycle invalidation.
- Q4 A: finish current combat, player checks and Luck before changing selected source version. Outstanding continuing obligations require explicit compatibility handling; this does not authorize clearing them. Details for future obligations remain the next decision.
- Q5 A: newgame/new-scenario transitions invalidate the old import worker and its activation binding while retaining the draft, accepted assets and diagnostic evidence. Reuse requires explicit reimport/admission under the new context, never automatic rebinding or stale authoritative publication.
- Q6: excluded at the operator's request. No Python compatibility work, version-requirement change or additional runtime matrix is part of this remediation. Existing verification environments remain as they are.

## Preserved contracts

Keep ADR0003 whole-battle provisional resources, bot Keeper settlement/rollback, original dice receipts, due ownership and future obligations. Keep presentation distinct from authority (ADR0002). Do not add a fixed per-turn model judge, automatically clear combat, guess missing source history, or conflate successful extraction/publication with activation in a game. OCR remains import-time work; gameplay consumes validated imported evidence.

## Shape and acceptance to refine

A logical scenario retains its stable identity while published versions remain immutable. Runtime and rollback consumers must refer to a selected version rather than a moving latest pointer. Initial historical-source migration is excluded per the revised Q3; rollout cleanup scope and future revision retention remain open. Publication eligibility must be checked before shared-source side effects; successful game-state commit and failed derived-image refresh must be reported separately.

Tests must exercise restart/retry/failed-save and real repository boundaries. Keep review categories confirmed, already_fixed, not_reproduced and blocked explicit. Test totals quoted in the external review are historical, not validation for this future work.

## Open interview frontier

Future obligations during a same-scenario version update; retrospective incidental correction boundaries; supported malfunction/repair scope; source retention/cleanup. Detailed recovery and publication gates must respect the decisions above. Final shared-understanding confirmation is required before runtime changes.
