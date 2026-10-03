# PR155 source boundary regressions

Baseline: `4288029d499c98c6847dde90a14f6b3510456377`. Supplied review covered older `a0c4f7e`; assess every finding against current code.

## Task graph

A (F1/F2): Require complete aligned source units for duplicates, preserving polarity and scope. All optional pregen decision paths must honor mandatory selection and complete coverage.

B (F3): Valid PDFs without an initial native-text preview proceed to production extraction; absent preview cannot trigger similarity matching.

C (F4–F8): Inspect current playable-source composition, optionality, saved-PDF reparse, publication boundaries and retry cache. Implement reproducible remaining defects after A/B; retain already implemented behavior.

D depends on A/B/C: integration, full verification, Standards and Spec review.

## Invariants

A substring inside a negated/conditional instruction is not a duplicate. A bound quote proves provenance, not optionality. Preview absence is not unreadable PDF. Candidate evidence is excluded from gameplay authority. A title or placeholder alone is not playable core. Failed provider attempts remain consumed but an explicit operator retry may reserve a fresh attempt within remaining caps; successful evidence is reusable only for its exact input. Candidate publication must not overwrite current authority before revision/confirmation checks. Reparse must preserve published source and game state on failure, conflict or weaker evidence.

No book-specific rules, OCR/model changes, new provider or retry transport layer. Synthetic regression fixtures only; private corpus evidence stays private. Existing PR155 is the delivery target.

## Implemented outcome

F1/F2 now use complete normalized sentence units and one mandatory-selection/coverage guard. F3 treats an absent preview as unavailable similarity evidence, while unreadable PDFs still fail. F4 composes the selected safe source before downstream consumers; titles/placeholders cannot substitute for unread raster body. A limited positive canonical dependency fast path binds required image assets to the final selected source, page image identity and region. A generated dependency remains unresolved unless authoritative recovery or complete counterparts for **all** observed fragments remove it. Historical native candidates, incomplete fragment coverage and coarse cover classifications cannot override an explicit canonical requirement.

F6 supports saved-source reparse with an explicit scenario ID and honestly labels legacy publication provenance. F7 validates confirmation/revision/lease before mutation, stores immutable content-addressed revisions and pins running games; source correction preserves chapter and gameplay state. F8 distinguishes successful evidence from consumed failed attempts. Explicit Continue/reparse can reserve a fresh bounded attempt; a cached classification retry never restarts OCR or map recovery.

F5 remains a P2 recall limitation: optionality wording recognition is not complete semantic discovery. Unknown ancillary content can be quarantined alongside a trustworthy playable body; no claim is made that arbitrary natural-language required dependencies are proven or discovered. Source hashes and schema checks establish identity, not semantic truth.

See [validation](pr155_source_boundary_regressions_validation.md). No new external provider requests were made for this patch.
