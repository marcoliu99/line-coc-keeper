# External authoring numeric review

Status: implementation authorized by the supplied CLI task (2026-09-27).

## Problem and scope

Exported instructions omit two distinct numeric contracts. Approval displays only
three warnings, hiding later records. Legacy PDF extraction can interleave columns,
footers and decorative glyphs with mechanics. A numeric checklist cannot prove
translation fidelity or distinguish those classes of source content.

This change updates future workbooks, shared numeric tokenization, field diagnostics
and private approval reports. Existing export registries and production drafts are
immutable. No automatic translation, numeric waiver or extra gameplay API call is
introduced. Private scenario text and correction drafts are not committed.

## Contracts

* Each rule field compares token Counters against its unique exact source quote,
  including repeated occurrences and reference pages inside that quote.
* Each record compares the source token set against public/kp prose and structured
  rule text. Metadata, quotes, uncertainty and other records do not satisfy it.
* Numeric tokens allow Chinese adjacency; dice case and horizontal spaces around
  a dice modifier are normalized. Values, repetitions, percentages and decimal
  spelling remain strict. Chinese number words are not converted or guessed.
* All independent field mismatches are collected. Approval recalculates every
  record and emits a private report including missing tokens and source contexts.
  The full report retains all issues; only the exception preview is bounded.
* Percent-only notation disagreements (identical values and counts after removing
  percent signs) persist as explicit review-required draft warnings, so corrections
  across packages can proceed. Approval rejects every such warning. Actual value
  or count mismatches still reject import; no numeric waiver is introduced.
* Import success remains distinct from approval. First submissions do not contain
  replacement IDs. Corrections name only existing IDs in that specific batch.

## Source repair and human review

The report is a worksheet, never an authorization to remove content. Record source
hash, source ID/span, exact extracted text, physical PDF page/crop, corrected text,
classification, reason, reviewer and date. Distinguish (1) translation omission,
(2) cross-unit relocation, (3) meaningful references, (4) layout artifacts,
(5) corrupt numeric tokens, and (6) uncertain visual descriptions/cards.

For (1)-(3), translate full meaningful passages in each unit, with dependencies as
needed; do not use a naked numeric list. For (4)-(6), inspect the PDF and prepare a
new extraction candidate with its audit trail; retain the original source and draft
as an archive. Re-import/reparse the PDF through the existing library workflow,
inspect parse_quality and compare the candidate with the worksheet before use.
Export anew after the source/chapter hash changes and rebind translations to the
new units and quotes. Reparse alone is not proof of repair; if it retains the error,
keep approval blocked pending a reviewed parser/source correction. There is no
in-place registry patch or uploaded `ignore_numbers` override.

The existing PDF parser already separates columns and retains evidence/quality
warnings. This task does not add speculative automatic footer removal or apply a
production source migration. The supplied 19-record draft needs source review,
including maps and character cards, before final approval.

## Flow

    export + literal inventory (not a repair instruction)
      -> external complete translation + numeric self-check
      -> first import / explicit existing-record replacement
      -> source binding + per-field Counters + per-record coverage
      -> approval revalidation of ALL records
           -> clean: record reviewer and approve
           -> issues: private full report + source repair worksheet
                -> translation correction: same export, explicit replacements
                -> source repair: reviewed extraction, NEW export, rebind

## Verification

Synthetic export/import/approve tests cover repeated numbers, dice spacing,
Chinese words/adjacency, extra page references, metadata exclusion, layout noise,
more than three records, full diagnostics beyond the preview cap, and first versus
replacement imports. Local r1-r19 audit compares the actual draft to its immutable
registry and original PDF; unresolved items must be reported explicitly.

## Implementation verification (2026-09-27)

* Full isolated suite: 1008 passed, 1 skipped, 33 subtests passed.
* `python3 -m ruff check app tests`: passed.
* `python3 -m mypy app`: passed (84 source files).
* The private 19-record audit produced 13 source-grounded translation corrections
  in three replacement packages. All three imported into a copy of the saved draft.
  Final approval correctly remained blocked by 21 source/visual review issues.
* The original PDF is available. Layout repair, map/image verification and omitted
  character-card reference rules still require a reviewed source candidate and a
  new export. No production source, draft, approval or existing export was changed.
* Existing approved variants are not automatically migrated/revoked. This change
  revalidates when approval is requested; it adds no gameplay review request.

## Follow-up: executable source repair (authorized 2026-09-27)

Implement an administrator CLI, `python3 -m app.scenario_source_review`, with
`prepare`, `check`, `publish`, and `rebind` operations. These are preparation tasks,
not player actions or automatic background translation. No paid APIs are used.

* Prepare snapshots source/PDF/chapter identity in a private server registry,
  exports physical-page images, original extraction and local native candidates,
  and an editable Markdown JSON proposal. Every physical page is represented.
* Each proposed page has text, a review note and PDF evidence rectangles. Missing
  text, review notes or evidence blocks publication. Raster pages need transcription;
  a native candidate alone is never certified complete. Explicit `image_only` is
  allowed only with an image reference and a review note (no invented spatial text).
* Check reports the exact old/new numeric counts and a digest covering the complete
  proposal and trusted source identity. Changes are not applied by checking.
* Publish requires an operator-supplied reviewer and the checked proposal digest,
  revalidates source/PDF/chapter identity, and atomically creates a NEW library ID.
  Original source, exports and active game selection are never overwritten. The
  audit stores before/after page text, evidence, numeric differences and reviewer.
  Source-dependent indexes, role-card objects and inferred maps are invalidated;
  original PDF/page images remain available. Regenerating those derived objects is
  a separate operation. New source publication is not Chinese-template approval.
* Rebind creates a new ordinary authoring export. A saved record is reusable only
  when it has one source unit whose exact text uniquely matches one new unit.
  Recompile quotes, replace source/record/unit IDs and drop old relation IDs. Reuse
  records with relations/dependencies only as manual reference, never detach rules
  silently. Changed/ambiguous/multi-unit records remain in a private migration
  report; new records retain their pending translation markers. Reuse is never
  automatically imported or approved. User edits only the results copy.
* Tests cover stale/tampered snapshots, changed proposals, missing physical pages,
  bounds and evidence, numeric audit retention, idempotent new-source publication,
  preservation of originals/active data, and conservative translation rebinding.

    prepare -> page images + proposal
      -> page-by-page PDF review -> check + digest
      -> publish(reviewer, digest) -> new scenario + immutable audit
      -> rebind(old export) -> fresh workbooks + reusable result drafts + report
      -> normal import -> full numeric review -> explicit template approval

### Source repair implementation verification

Implemented `app/scenario_source_review.py` and the bilingual
[operator guide](../../guides/scenario_source_review.md). Reviewed source exports
now retain physical-page boundaries; legacy source blocks/registries are unchanged.
Evidence-image hashes are checked as well as source/PDF/manifest/proposal identity.

Full isolated command:
`python3 /private/tmp/run_review_suite.py source-review-final2 /private/tmp/line-coc-turn-safety-spec`

Result: **1030 passed, 1 skipped, 33 subtests passed**. Ruff passed; mypy passed
for 85 source files. The synthetic end-to-end tests exercise separate source
publication, audit persistence, idempotent retries and quote/ID rebinding. Fault
injection verifies cleanup and rejection if the source changes during rendering.

A private copy of the supplied 27-page PDF was prepared and checked. Font and
coordinate evidence supports a 15-page layout repair candidate; the corrupt die
is separated from the decorative glyph without changing its value. No real-source
publication or approval was performed: map labels, raster cards/reference rules
and final page reading still require review. No paid APIs were called.

## Revision: external AI checks the original PDF (2026-09-27)

The user chooses web Gemini/ChatGPT with both the exported MD workbooks and the
original PDF. The export message and each workbook must explicitly request both
attachments and require the external AI to compare the physical PDF pages with
the extraction and translation. Check columns, footers/decorative glyphs, tables,
map labels, character cards, reference rules, dice, percentages, dates, prices,
limits and conditions. Blank Luck stays blank. Missing/unreadable PDF evidence
must be reported; the model must not claim to have checked unavailable pages.

This replaces human page-by-page checking as the requested authoring workflow.
It does not add an application-side AI review stage or require a human KP login.
The existing administrator source-repair CLI remains available, but is not a
required authoring step. Its earlier manual instructions describe that optional
utility, not the user's preferred workflow.

For this prompt-only revision the import schema remains unchanged. Correct
translations where the immutable source supports the correction. For extraction
errors, report unit ID, physical PDF page, exact extracted text, proposed correct
text, reason and unresolved evidence outside the import JSON; do not fabricate
source quotes, change IDs or pad prose with OCR noise. Affected units remain
unfinished. Applying those external source corrections and rebinding their
translations still requires a separate implementation; this prompt does not
make correction reports directly importable or waive numeric validation.

Verification: exercise the real export and private command/help reply tests to
confirm both outputs carry the PDF requirement; retain the normal numeric
validation and export/import round-trip coverage.

## PR #100 review correction: exact published page provenance

Preserve reviewed text verbatim, including leading/trailing whitespace. A shared
page serializer supplies the published body and the quality hash. The check and
audit retain the submitted transcription in `after` and record `published_text`
explicitly, including the generated reference for image-only pages. Proposal
digests still bind the raw proposal; even a whitespace change after checking
requires a new check. Regression tests compare actual UTF-8 source bytes, audit
text, per-page hashes and whole-source hashes for multiple pages, whitespace,
image-only pages and repeat publication.
