# Validation — PDF import result UX and generalization

## False-exit audit

| Exit | User state | Explanation / next step |
|---|---|---|
| Unsupported extension | IMPORT_FAILED | Only PDFs supported; upload PDF |
| Stale revision, before parsing / similarity staging | STALE_STATE | State changed; reopen Help |
| Pending pregen Luck | WAITING_FOR_CHOICE | Complete `/coc luck roll` first |
| Existing replacement choice | WAITING_FOR_CHOICE | Resolve previous upload buttons first |
| Existing similar scenario, including locked race | SIMILAR_SCENARIO_PENDING | Reparse or cancel |
| Import lease conflict / stale owner | OWNERSHIP_CONFLICT | Existing draft / processing / cancelled explanation |
| Preview unreadable | IMPORT_FAILED | Not enabled; check source and reupload; no exception body |
| Similarity detected | WAITING_FOR_CHOICE | Explicit waiting, reparse/cancel |
| LayoutReviewRequired | IMPORT_PENDING | Actual checkpoint saved; page explanations; continue/status/cancel |
| Extraction ValueError | IMPORT_PENDING / IMPORT_FAILED | Pending only with checkpoint pages, otherwise failure |
| Postpublication stale revision | STALE_STATE | PDF not applied; reopen Help |
| Replacement-choice race | WAITING_FOR_CHOICE | Complete earlier choice, then upload again |
| Unexpected exception before commit | IMPORT_PENDING / IMPORT_FAILED | Actual saved pages determine summary; re-raise preserved |
| Unexpected exception after commit | IMPORT_SUCCESS_WITH_WARNINGS | Attempt-local commit marker; do not mistake existing same-PDF activation for this attempt |

Bool semantics and exception propagation are preserved. Similarity/replacement choices remain existing production controls, not admission state changes. Failure summaries avoid raw exception data. Real corpus results will be recorded separately; missing results are not success.
