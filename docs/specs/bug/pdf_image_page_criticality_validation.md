# PDF image-page criticality validation

Current result: approved five-P1 fix implemented at `1bcb12b`; real Haunting canary **HARNESS_LIMITED**, not playable/proven. New six-book authorization accepted, eight actual OpenAI requests succeeded. See the latest follow-up below; earlier PENDING/zero-request statements are historical checkpoints. Merge and rollout HOLD.

Baseline: enhancement/pdf-multicolumn-ingestion @ 5b87207a9a47ad2f0473ecda631504c833669a33. Six corpus SHA256 values match the earlier local 245-page extraction. Original book hard-block counts are 23/9/26/7/25/15 (105 pages); 86 pages have image transcription blocking reason. Original source reason totals: 86 image, 19 ordering, 1 mechanics, including one page with overlapping reasons.

## Historical provider-ON status: PENDING (superseded below)

Automatic authorization review rejected the attempted six-book external execution before process launch. No requests, images or text were sent. The stated reason was that prior trusted authorization covered only two map PNGs and the later provider-ON task was not sufficiently explicit about the six-book payload/destination. No other transport, indirect execution, mock result, expanded caps or ledger reset was used. Prepared private harness enters handle_pdf_upload, actual draft/resume and publication; it guards official OpenAI endpoint, explicit SDK retries=0 and existing durable reserve. Region AI repair currently fails this policy; it is not secretly normalized.

Production baseline classification/current disposition/true-false counts/start are null/PENDING in results JSON. They cannot be inferred from provider-OFF metrics. Runtime source policy remains unchanged. Full baseline suite PASS: 2147 passed, 2 skipped (2149 collected), Ruff PASS, mypy PASS (137 files), compileall PASS, diff-check PASS. Green tests do not clear the reproduced review findings.

## Local audit

105 sanitized prior hard-page records include all 86 image targets and physical page, image-gate rationale, native amount, raster union, graphics, map candidate, provider classification (null), unique-source/criticality/required transcription (unknown), expected conservative unknown disposition and pending current disposition. No structurally blank page was proved by native+images+drawings absence. Native empty is not asserted empty source. The unknown classification count is an audit limitation, not 86 proven true source-critical pages.

Dead Boarder seven image targets: p20 has native142/raster0.625668/map; p21/23/25/27/29/31 have native41/raster1.0/character sheets. Camp Sunny: p16/18/20/22/24/26 have native39/raster1.0/character sheets; p28 native78/raster0.668203/map-like image, current map_candidate=false. Local overview reviewed Dead p20–29 and Camp p16–28 (Dead p31 sheet identity is not visually confirmed here). Optionality and whether map labels duplicate safe source remain unknown, not accepted merely by appearance.

The image_only_pages metric counts native-absent pages, explaining image_only=0 alongside short-native raster source gaps. Current admission app/pdf_loader.py adds source_image_transcription_unverified based on requires_image_transcription + no authoritative + not verified illustration without general criticality. This is the exact potential root cause; no provider-confirmed false block is claimed.

Haunting special pages, Beacon p28–42, Scritch p23 mechanics/p24 ordering and Alone ordering remain tracked unchanged. No ordering/mechanics blocker is softened. Visual graphs remain quarantined; map proof is independent of source admission. Minimum remaining TRUE blocker after false-block removal is unavailable until classification baseline; do not substitute the original provider-OFF totals.

Raw PDF images, source and harness outputs remain private outside repository. Only hashes, safe page facts, statuses/reasons/counts are committed. See pdf_image_page_criticality_results.json for per-page records. Merge and rollout HOLD. Existing ordering P1 and merged-draft cleanup P2 are not declared resolved.

## Review findings retained

Spec review reproduced two P1 production-validator gaps: body above spanning heading may be reordered after it; margin/header/footer order is not constrained, accepting footer -> body -> header. Resumed merged upload keeps staged-part entries after a later successful Continue (P2), and separate region AI allowance resets on Continue without durable reservation (P2). Persist the existing separate AI allowance; do not require an architecture redesign or silently debit the layout ledger instead. No findings were fixed in this baseline-first checkpoint. Standards docs review: 0 findings.

## Latest branch integration

Remote PR155 advanced to 37e0bb80eb34d059c487a307c7a863c7890b8522; merged as 17aa388 with both catalog intents preserved. 5b87207a9a47ad2f0473ecda631504c833669a33 and its 2147-pass suite are historical provider-OFF/code evidence. Current candidate baseline is 37e0bb80eb34d059c487a307c7a863c7890b8522; post-merge checks are recorded separately below/results. Semantic discovery now exists; no claim of its absence applies to latest code. Current static network preflight still shows semantic analyze_text(max_retries=0) uses internal compatibility retries, region repair lacks bounded durable dispatch, and eager index/pregen/start calls have full-source/default retry paths. These are network-contract P1, separate from AI allowance persistence P2. No actual provider-ON request was made before or after integration.

Post-merge verification on 17aa388: pytest 2181 passed / 2 skipped; Ruff PASS; mypy PASS (138 source files); compileall PASS; diff-check PASS. Later changes are sanitized docs only.


## Approved minimal P1 follow-up — 2026-10-02

Baseline `fb3e8b746157e59c678476f549efecafc01bea33`; implementation `1bcb12b700cfbacfbd8576f4521e0635319a6655`. All six corpus hashes still match. The implementation changes only the five approved defects: inverse spanning-heading rejection; margin geometric order; direct SDK dispatch when text analysis explicitly sets zero retries; a separate checkpointed region-AI allowance/crop dedup; and bounded optional index/pregen/opening text windows with private durable one-shot records. Ordering evidence identity now includes validation version 2. No OCR, map, topology, movement or publication-gate rewrite. Optional incomplete cards/storage failures degrade to no optional metadata; the strict direct pregen parser remains unchanged.

Transport regressions use real OpenAI SDK with HTTP MockTransport, including success, 429, timeout, connection failure and unsupported-parameter error. They check configured timeout, reserve-before-transport, no refund, failed-crop replay suppression, no hidden retry and bounded payloads. Geometry tests reject body/heading inversions and header/footer/margin misplacement while retaining valid heading/two-column fixtures.

Real production entry was `app.legacy_commands.handle_pdf_upload` using isolated private state/library/draft. Explicit user approval authorized this six-book scope, superseding the old two-map restriction. The execution accepted that scope; HTTP 200 confirms an endpoint response at `https://api.openai.com/v1`, configured `gpt-6-luna`, rather than granting authorization. No full PDF/full scenario dispatch or extra audit calls. Production caps remained layout 8 requests/4 pages, region AI 8, semantic discovery 4.

Actual requests: 2 ordering (p16/p28), 1 MarkItDown (p1), 1 region repair (p4), 4 bounded semantic windows. All eight HTTP responses were 200. Reservations: layout 8 vs transports 3; separate region 1/1; semantic 4/4; total 13/8. No hidden SDK retries. Five consumed MarkItDown reservations did not reach transport: the private observation guard wrongly prohibited legitimate SDK-client reuse after its first request. This is a validation-harness defect, not evidence of a production provider/accounting defect. The private guard was corrected to bind each transport to a fresh reservation identity; **not rerun**. Original guard and all evidence remain private. No refund, cap increase, ledger reset or new draft to regain budget.

Haunting import returned false after 294.094s. Hard pages changed 23 -> 21: both real ordering challengers accepted, no ordering/mechanics hard reasons remain. All 21 remaining reasons are image transcription unverified (p1/2/5/17/24/34–49). Soft review p4. Maps p24/p34 NOT_ANALYZED, zero gameplay maps. Semantic discovery proposed one candidate, certified zero, awaiting canonical source. This does not establish true/false source-criticality: production page-role classification did not run, and the harness impaired image verification. The 21 records remain UNKNOWN_NEEDS_REVIEW with null true/false findings; cover/pregen appearance is not substituted for provider/evidence binding. No admission code was weakened.

| Book | Previous hard pages | Current hard pages | Publication | Activation/start/turn | Playability | Primary limitation |
|---|---:|---:|---|---|---|---|
| Haunting | 23 | 21 | No | Not run | Unproven, harness limited | 21 unresolved image pages; classification missing |
| Dead Boarder | 9 | Pending | Not run | Not run | Pending | Haunting canary gate |
| Lightless Beacon | 26 | Pending | Not run | Not run | Pending | Haunting canary gate |
| Camp Sunny | 7 | Pending | Not run | Not run | Pending | Haunting canary gate |
| Scritch Scratch | 25 | Pending | Not run | Not run | Pending | Haunting canary gate |
| Alone Against the Flames | 15 | Pending | Not run | Not run | Pending | Haunting canary gate |

No successful publication/reload/activation/start/ordinary-turn smoke is claimed. Continue was not dispatched because shared image/layout requests were exhausted. The remaining five books were not run as explicitly required by the canary gate. Their prior safety controls are unchanged; the minimum remaining *true* blocker per book remains unknown.

Verification: **2199 passed, 1 skipped, 152 subtests passed (39.17s)**; Ruff PASS; mypy PASS (139 files); compileall PASS; diff-check PASS. Standards and Spec reviews: zero remaining actionable findings in the minimal fix. No new local-suite regression. Deferred P2 merged-resume staged-part cleanup remains; region-AI Continue allowance reset is fixed. P3 cleanups deferred. No new P0/P1 found in this patch, but the acceptance P1 gate (at least one real playable scenario) remains unmet. **PR155 MERGE HOLD; production rollout HOLD**.

Sanitized receipts, exact changed files and per-page unknown audit are in `pdf_image_page_criticality_results.json:minimal_p1_followup`. All images, raw responses, candidate source/graphs and full private evidence stay outside repository.
