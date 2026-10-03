# First import / monotonic reparse validation

Code: `062a4943c979e3f2f6670e4eed944d5d2adb0057`; baseline: `2f16c016b9888e9a2026f1fe5ed380a7c675d062`.

All six original PDF SHA256 values matched. The production runs used `handle_pdf_upload`, isolated storage, configured OpenAI `gpt-6-luna`, official `/v1`, zero SDK retries and existing caps (layout: 8 requests / 4 pages). No PDFs, images, source prose, raw prompts/responses or candidate graphs are included here. Sanitized decisions and accounting are in the adjacent results JSON.

| Book | First import | Persisted reload / activation / start | Remaining core review |
|---|---|---|---|
| Haunting | Existing accepted publication retained | PASS / PASS / PASS | None |
| Dead Boarder | READY_WITH_WARNINGS | PASS / PASS / PASS | None |
| Lightless Beacon | IMPORT_PENDING | Not executed | p13 ordering |
| Camp Sunny | READY_WITH_WARNINGS | PASS / PASS / PASS | None |
| Scritch Scratch | IMPORT_PENDING | Not executed | p24 ordering |
| Alone Against the Flames | IMPORT_PENDING | Not executed | p3 ordering |

The five fresh imports quarantined 44 unresolved image reviews; none became a core image blocker. This is **not** proof that all 44 are optional or duplicate. Their uncertain contents were excluded from gameplay authority. Three core ordering reviews remain blocking. Beacon's two image permutations failed geometry validation; Scritch's candidate failed numeric/dice preservation and native alignment; Alone's core order remains unverified. These results do not claim the original PDFs themselves are corrupt. Map/topology availability was not required for admission. No verified map was claimed by these runs.

Fresh five-book publication rate: 2/5, both READY_WITH_WARNINGS. Haunting remains the prior full ordinary-turn acceptance control; this round performed fresh reload/use/start, with import-time calls zero. Dead Boarder and Camp Sunny start also observed zero import-time calls.

Real Dead Boarder `/coc scenario reparse` completed: same scenario, exact canonical source unchanged, 26 verified pages retained, zero upgrades/conflicts/downgrades. Classification cache replay required zero new classification requests. Timeline, characters/resources, checks/luck, combat, map position/facing, route state, KP ownership and assistant continuation identity were preserved. The newly consumed operation ledger remains in private attempt history. No natural improvement occurred; a synthetic integration invokes the public reparse command and upgrades a quarantined page while preserving live state. Separate region, certified-map and source-bound-pregen integration tests cover progressive additions. Conflict, no-improvement and improvement messages are covered.

Accounting across fresh imports, start regressions and real reparse: 137 durable analysis reservations / 137 transports, plus 9 runtime logical dispatches / 9 transports. 143 HTTP 200 responses; 3 transports ended without a recorded HTTP response and consumed their reservation. Hidden retries, refunds, cap increases and guard rejections: zero. The existing report's bounded logical retry counter is distinct from SDK hidden retries. Reparse consumed 11 analysis transports; it did not reset the prior operation's ledger.

Success-with-warnings and pending UX were observed through production entry; pending messages include continue/status/cancel and do not expose internal blocker codes. Clean success, true failure, malformed input and all False-exit regressions remain covered by the full suite. Synthetic public-command tests verify all three reparse outcomes.

Verification: 0 pytest failures/errors, 1 skipped; ruff, mypy (142 app files), compileall and diff-check pass. Standards and Spec reviews have no remaining actionable findings. Reproduced cross-character and unsupported-weapon artifact bugs were fixed before completion. No book-specific production rule was added; no OCR/map/runtime architecture or gameplay rule was changed.

Merge recommendation: READY for this change. Production rollout: HOLD while the three core ordering reviews remain unresolved; they have not been falsely downgraded to enhancement warnings. Process-crash/cross-process durability beyond the existing publication mechanism was not tested; staged-build failure and commit exception rollback were tested. Legacy merged staged-part cleanup and unrelated review cleanup remain outside this round.
