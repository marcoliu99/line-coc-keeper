# PR155 merge and rollout closeout

Closeout baseline: `e85aaf9cb7aac2cb9d98d7bff424007b4fc767ad`. Implementation: `3eb7706246c50f2fcc3900016c038f50f3944e1a`. This closeout changes documentation only; production and tests are unchanged.

## Independent gates

**PR155 MERGE READY.** No remaining reproducible P0/P1 findings. Merge requires correct generic contracts: safe playable-first source composition, quarantine excluded from gameplay authority, strict required core ordering/mechanics, monotonic canonical and certified-map reparse, working bounded Codex opening analysis, revision/live-state preservation and green tests/tooling. It does not require every corpus page/feature to be recovered, all six books published, or a 500-round soak completed. Safe playable core may publish with unresolved non-authoritative content quarantined.

**Production PDF rollout HOLD.** Lightless Beacon p13, Scritch Scratch p24 and Alone Against the Flames p3 remain CORE_PLAYABLE_SOURCE ordering reviews. They are neither resolved nor downgraded by closeout. Safe resolution plus broader real generalization evidence belongs to follow-up validation/a follow-up PR.

Historical `a0c4f7e` 500-round evidence is **historical / superseded robustness evidence**. Its available 7/500 progress is not a current certified-map implementation failure and is not a PR155 merge gate.

## Final review and regression

Standards and Spec independently reviewed `40f95d6...e85aaf9` and current generic authority/persistence contracts. Both: **No remaining reproducible P0/P1 findings.** No production fix was warranted.

The full persistence path is covered: published certified map, reparse candidate, final merged source certificate replay, atomic save/revision, persisted reload/load_context and correction activation. Still-valid old proof is retained independently; source-incompatible proof disables only the map; conflicting verified candidates retain the valid published artifact. Compatible page/room/facing and protected game state survive. Source-boundary, image reviews and corpus ordering gates remain frozen.

Offline closeout repeated actual router scenario use/start on isolated copies of published Haunting, Dead Boarder and Camp Sunny. All reload/activation/start PASS. Current completed found=false analysis caches replayed three times without dispatch; fallback provider boundary used explicitly synthetic text. This is an offline regression, not a new real provider success claim. Import-time calls: 0. Previous unchanged-implementation real reload/activation/start evidence remains valid.

Synthetic isolated Codex `/coc start` traversed the actual opening helper and bounded Codex adapter with an external transport fixture: one adapter dispatch/one synthetic transport, TypeError 0, completed record, found=true opening actually used. Previous real Codex canary remains the external evidence: 1 reservation/1 CLI transport, helper used, no fallback. CLI-internal HTTP packets were not directly measured. Caller/owner deadline, zero retry and cache-version regression fixtures pass.

No new external PDF/provider requests; hidden retries/refunds/cap increases: 0. No imports/OCR/classification/map/topology work was rerun. No book-specific rules added.

## Verification

- Full pytest: **2412 passed, 1 skipped, 152 subtests passed**; 50.38s, no errors/failures.
- `ruff check .`: PASS.
- `mypy app`: PASS, 142 files.
- `python -m compileall app tests`: PASS.
- `git diff --check`: PASS.
- GitHub baseline HEAD: CI checks PASS; optional Linux CPU portability smoke PASS. The main_v2 required-status API reports Branch not protected (404); no separate required-check list is configured there. Final documentation-head checks are verified separately in the final report.

No raw PDF/images, source prose, provider response or credentials are committed. Sanitized results are [alongside this report](pr155_merge_rollout_closeout_results.json). Earlier actual canary details remain in [opening/map validation](../bug/pr155_opening_map_retention_validation.md).
