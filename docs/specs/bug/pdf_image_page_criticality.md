# PDF image-page criticality admission

## Status and evidence gate

Proposed; implementation NOT authorized by a completed baseline. Historical provider-OFF extraction code is 5b87207a9a47ad2f0473ecda631504c833669a33 on enhancement/pdf-multicolumn-ingestion. Latest provider-ON candidate baseline is 37e0bb80eb34d059c487a307c7a863c7890b8522 (merged in 17aa388), integration main_v2=6024adfffad39860de2142e87dbf33e056f4960a. No runtime code changes before the existing-production provider-ON upload baseline and page audit. External dispatch was rejected by automatic authorization review; no payload was sent. This spec records a reviewable design, not implemented behavior or successful admission.

## Problem and goal

Current pdf_loader source admission adds source_image_transcription_unverified whenever requires_image_transcription is true, independently authoritative text is unavailable, and verified_illustration is false. The flag originates from low native text/raster source gap. There is no general page-criticality taxonomy. This is a potential false-hard-block cause, not proof that every flagged page is non-source. image_only_pages=0 does not imply no raster-bearing source: the metric counts native-absent pages, while raster gaps include short native captions.

Determine whether a page contains unique source required for correct scenario execution before treating missing image transcription as a source failure. Source mechanics, unsafe ordering, missing unique source and unresolved genuine criticality remain HARD_BLOCK. Derived graph correctness stays separate.

## Scope and non-goals

Only page-role evidence, source criticality, admission diagnostics, bounded classification, draft identity and safe publication integration. Preserve Paddle/Tesseract acceptance, MarkItDown order, Docling architecture, visual map phases/certificates, source topology, hidden routes, multi-barrier progression and movement. Do not tune caps or publish by confidence. No copyrighted prose, images, raw responses, prompts or credentials in repository artifacts.

## Proposed model

Closed role set: SOURCE_CRITICAL, PURE_ILLUSTRATION, COVER_DECORATIVE, MAP_DERIVED, OPTIONAL_HANDOUT, OPTIONAL_PREGEN, EMPTY_NON_SOURCE, DUPLICATE_SOURCE, UNKNOWN_NEEDS_REVIEW. Criticality is true/false/unknown; provider classification alone cannot authorize publication. Private evidence stores region/span/hash provenance. Public records contain only hashes, page numbers, role, reason codes, counts and safe structural facts.

Provider structured evidence may classify image role and source-bearing regions, mechanics/clue presence and ambiguity. Deterministic code combines native amount/placement, union raster coverage, graphics, trustworthy classification and verified canonical counterparts. Mixed pages retain unique text/mechanics even if mostly decorative. Pregen/handout optionality requires scenario support; character-sheet appearance alone does not prove optional. DUPLICATE_SOURCE requires exact bound counterpart evidence, never an LLM assertion. Entirely empty pages require direct structural/visual evidence; raster containing unknown content is not empty merely because native text is empty.

## Proposed flow

Native/geometry evidence -> page role/criticality -> bounded classification if unresolved -> source-bearing region transcription only when needed -> existing independent mechanics/source checks -> admission. Known non-source pages accept; safe optional/derived assets soft-review and remain excluded from authoritative parsed features if unsafe. Missing authoritative source hard-blocks only true criticality or unresolved genuine unknown. Ordering/mechanics gates remain independent and cannot be cleared by role classification.

Keep blocked_pages == hard_block_pages. Safe accepted/legacy/soft pages resume using matching PDF, selected-text and extraction identity; never cache unknown/hard pages as accepted or uncertified graph as gameplay map. Change pipeline/extraction identity when policy changes. Revalidate library publication gates without relaxing map certificates. Source-criticality extraction failure is unknown, not proof of source criticality; deterministic non-source evidence remains usable when provider/budget is unavailable.

## Production validation

Verify all six original PDF SHA256 values against the 245-page provider-OFF baseline. Enter through handle_pdf_upload, existing durable draft/continue and library save/reload/activation; invoke /coc start only for actual READY scenarios. Production-configured request/page caps remain 8/4 in the inspected environment; max_retries=0, stage-specific timeout and reserve-before-dispatch are mandatory. No extra audit-provider calls outside production. Budget exhaustion is BUDGET_LIMITED and cannot trigger cap reset. One durable reservation permits at most one transport; unsafe unreserved/default-retry paths must be reported rather than silently normalized by the harness.

Compare every previous hard page by reason, actual/expected role, source criticality and disposition. Preserve null/PENDING fields for unperformed provider baseline and uncertainty. Record true/false/unknown image blocks, separate ordering/mechanics and minimum remaining blocker per book. Semantic topology provider must not classify admission. Published books must survive reload/activation/start with uncertified maps excluded.

## Tests after confirmed real failure

Regressions: illustration, cover, truly empty/decorative low-text page, optional handout/pregen, map graph failure, duplicate counterpart; all must avoid whole-scenario block when noncriticality is proved. True unique image, mixed unique mechanics, unknown content, ordering and mechanics remain hard. Provider unavailable/budget exhausted must preserve deterministic known non-source evidence but block genuinely unresolved source. Test native-present low-text graphical page with image_only_pages=0. Test durable accounting, safe resume, no unverified OCR/map authority and publication/start. Measure requests before/after without optimizing away checks. Run pytest, ruff check ., mypy app, compileall, diff-check and independent Standards/Spec review.

## Open blockers

Provider-ON baseline is PENDING because external transmission authorization review rejected the six-book action. Classification/true/false image counts and after-fix admission are not available. Region AI repair currently lacks explicit zero-retry/timeout and a separate durable AI request ledger/reservation retained across Continue. Existing heading inverse ordering and margin/header/footer ordering P1 must be fixed before MERGE READY. Merged-resume staged-part cleanup and AI budget resumability remain P2. No architecture changes can be inferred from this draft spec.

## Approved minimal P1 follow-up (2026-10-02)

User explicitly confirmed five P1 fixes and superseded the old two-image authorization with six-book bounded official OpenAI dispatch. Before any provider canary, reject inverse spanning-heading placement and out-of-geometry page furniture; max_retries=0 bypasses internal compatibility retries. Region repair retains a separate durable AI allowance and crop/request identity, persisted before dispatch and across Continue without refund. Index/pregen/opening extract only bounded source windows with private one-shot reservations; their failed optional metadata cannot create a canonical hard block. Normal successful card units retain their backstory; no OCR/map/topology/gameplay architecture changes. Add cache identity for stricter candidate ordering validation.

Run five P1 regressions and full checks before Haunting-only handle_pdf_upload -> durable continue -> save/reload/activation/start/one turn. Do not expand to other books until the canary succeeds. Provider failures/budget/unknown genuine source remain accurately blocked; only real-evidence-confirmed false admission blocks may receive minimal correction. Preserve mechanics/order controls. Network reservations and actual SDK transports are observed independently.
