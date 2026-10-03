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

## Real sequential production results

All six SHA256 match the prior corpus. The five new books used fresh independent storage and original caps with `gpt-6-luna` at the authorized OpenAI destination. Every upload went through `handle_pdf_upload`; Continue was used only while durable production budget allowed useful work.

| Book | Hard review pages | Image / ordering / mechanics | Final UX | Publication / reload / activation / start |
|---|---:|---|---|---|
| Dead Boarder | 6 | 6 / 0 / 0 | IMPORT_PENDING | Not executed: source admission withheld |
| Lightless Beacon | 18 | 17 / 1 / 0 | IMPORT_PENDING | Not executed: source admission withheld |
| Camp Sunny | 6 | 6 / 0 / 0 | IMPORT_PENDING | Not executed: source admission withheld |
| Scritch Scratch | 16 | 14 / 2 / 0 | IMPORT_PENDING | Not executed: source admission withheld |
| Alone Against the Flames | 11 | 2 / 9 / 0 | IMPORT_PENDING | Not executed: source admission withheld |

The requested NOT_PLAYABLE_TRUE_SOURCE_BLOCK category here means withholding by an unresolved canonical-source safety gate, **not** proof that every image contains unique required information. Of 45 image review pages, 35 remain UNKNOWN and 10 are provider SOURCE_CRITICAL candidates. Neither group is automatically proof of unique required source. No confirmed false-block count or unique-required count is claimed. No new admission heuristics were added to force playability.

Dead Boarder and Camp Sunny cached classifications identify pregen material, but the cited canonical context did not establish explicit alternative-character permission; these pages are pending necessity review, not declared mandatory pregens. Lightless Beacon map p16 remains soft. Scritch Scratch p8/p24 retain ordering gates; p23 final selected source is accepted and has no detectable corrupted mechanics. Alone retains nine ordering gates. All map/topology failures remain feature warnings.

124 matched reservation / transport attempts; 123 HTTP 200, one MarkItDown transport without HTTP response. No hidden retries, refunds, caps increases, or prevented unauthorized dispatches. Final pending messages have saved draft evidence, human-readable page issues and continue/status/cancel; no internal source reason code leaks.

Haunting remains the historical real accepted control (publication/reload/use/start/two turns, zero import-time runtime calls). Current synthetic production publication/start regression passes, including invalid map quarantine and optional-feature success warnings. This round did not rerun its image classification or ordinary turns.

Generic admission defect: not established. UX fixes cover explicit outcomes, sanitized failures and postcommit notification errors. Full suite and tooling pass; raw images/prose/provider responses remain private. MERGE and ROLLOUT HOLD until unresolved image necessity and ordering evidence can be assessed; the five new books are not proven playable.
