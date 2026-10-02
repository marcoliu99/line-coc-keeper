# PDF image-page criticality validation

Baseline: enhancement/pdf-multicolumn-ingestion @ 5b87207a9a47ad2f0473ecda631504c833669a33. Six corpus SHA256 values match the earlier local 245-page extraction. Original book hard-block counts are 23/9/26/7/25/15 (105 pages); 86 pages have image transcription blocking reason. Original source reason totals: 86 image, 19 ordering, 1 mechanics, including one page with overlapping reasons.

## Provider-ON status: PENDING

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
