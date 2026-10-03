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
