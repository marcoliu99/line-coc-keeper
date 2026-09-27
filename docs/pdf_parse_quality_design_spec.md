# PDF parse quality improvement

## Goal

Preserve complete source evidence for gameplay and external translation. Fix parser
precedence, empty-result replacement, destructive source truncation and summarizing
OCR instructions. Improve conservative column ordering and make uncertainty visible.
No per-turn model stage is introduced.

## Flow

    PDF -> native blocks + optional layout parser
        -> conservative native column ordering
        -> compare candidate coverage and numeric evidence
        -> choose layout or native per page
        -> targeted fallback only for pages lacking usable text
        -> retain page markers, flag possible cross-page continuation
        -> complete source + parse_quality.json + page images/maps
        -> existing chapter window / bounded prompt / RAG

## Contracts

- Keep extract_text's five-element return contract; optional quality_report output
  records parser version, page count, source selection, warnings and continuation
  candidates. The legacy truncated flag is false because source is retained whole.
- Layout text cannot be replaced by an empty alternate result. Missing source words
  or numeric evidence cause a conservative native fallback and a review warning.
- Reorder native blocks only with strong two-column evidence and no spanning body
  block. Ambiguous layouts are flagged, not silently assigned a guessed order.
- MarkItDown processes only requested fallback pages; page numbers map back to the
  original PDF. It does not run on every already-readable page.
- OCR asks for verbatim original-language transcription including blank field labels,
  age, tables and full prose. No translation or invented missing values. Whole-page
  map interpretation remains separate, explicitly labeled derived material.
- Do not delete repeated headers or join uncertain cross-page prose automatically.
  Record probable continuation for review while preserving original page boundaries.
- Never cut library source at MAX_SCENARIO_CHARS. Existing prompt bounds remain.
- Persist quality report with source artifacts; report suspicious pages to the uploader.
  Warnings are heuristics, not proof of semantic correctness or accurate table mapping.
- Close PDF handles and isolate page fallback errors so one failed page does not
  discard readable pages. Unresolved pages remain visible in the quality report.

## Validation

Synthetic regression tests exercise precedence, empty alternates, lost numeric fields,
column order, full-source retention, continuation reporting and persistent diagnostics.
Run a local no-API probe of the supplied scenarios and role cards, report coverage and
warnings without claiming human-level parsing accuracy. Paid OCR is mocked in tests.

## Local verification (2026-09-27)

| Supplied document | Pages retained | Layout/native selected pages | Review pages | Source characters |
| --- | ---: | --- | ---: | ---: |
| The Haunting, trimmed | 27 | 26 / 1 | 2 | 76,144 |
| Dead Boarder | 32 | 29 / 3 | 8 | 76,289 |
| The Lightless Beacon | 43 | 35 / 8 | 13 | 84,312 |
| Doors to Darkness pregens | 11 | 1 / 10 | 10 | 20,742 |

No source was truncated. The Beacon 23 -> 24 continuation was identified as a
candidate. All ten pregen pages triggered numeric-evidence warnings and retained
native text instead of silently accepting layout output. This does not certify
label/value correspondence in the native text. Character-sheet page transitions
also produce false-positive continuation candidates; candidates never alter text.

The probe disabled MarkItDown and whole-page model calls. PyMuPDF4LLM used local
Tesseract where necessary. Raw PDFs and extracted source text remain outside git.
Counts, package versions and limitations: [local report](evaluations/pdf_parse_quality_local.json).

Full isolated suite: 775 passed, one skipped, 15 subtests passed. Mypy: 74 source
files. Ruff passes for all changed files; a pre-existing SIM114 in untouched
app/pregen_extractor.py remains outside this change.

### Remaining limits

Numeric/text coverage checks detect omissions, not swapped table labels or semantic
errors. Complex spanning tables and ambiguous columns are retained with warnings.
Human review remains necessary; automatic region-level table reconstruction and
verified paragraph joins are not claimed by this revision. Large-source preparation
models may still need batching; source storage and prompt limits are now separate.

## Phase 2: block provenance and numeric pairing

Persist page dimensions, rotation, native block/line/word bounding boxes and every
candidate text (native, layout, OCR, vision) in the private parse-quality artifact.
Bind evidence to the PDF SHA-256 and parser version. The gameplay source retains
only the chosen text; alternate candidates are never concatenated into the prompt.

For explicitly recognized stat labels, find an unambiguous nearby numeric word on
the same visual row, stopping at intervening labels/text. Save label/value boxes and
block IDs. No automatic vertical-table guesses or broad nearest-number association.
Compare these source pairs with explicit label/value pairs in candidate text. A
contradicting pair rejects that candidate even when its numeric multiset is unchanged.
Missing/ambiguous pairs remain review warnings, not fabricated values or proof of
correctness. Repeated-label identity and arbitrary skill tables remain limitations.

Tests: same-number swaps, intervening labels, ambiguous/vertical layouts, repeated
labels, empty candidates, complete artifact persistence and no candidate leakage.
This phase prepares targeted OCR with inspectable regions; it does not add paid
region repair or a page-editing UI.

### Phase 2 verification

Native geometry probes on the four supplied PDFs retained 293, 526, 369 and 154
text blocks respectively. The pregen PDF yielded 140 same-row stat candidates
across ten cards. A visual spot-check of PDF page 2 matched all 14 detected fields,
including age 25, STR 90, DEX 65 and HP 16. This is a single-card spot-check, not a
140-field accuracy claim. Other PDFs include unresolved labels in prose and complex
layouts; these are not auto-assigned. Aggregate results contain no source text:
[evaluation](evaluations/pdf_numeric_pair_probe.json).

Phase 2 full suite: 782 passed, one skipped, 15 subtests passed; mypy checks 74 files.
Changed-file Ruff checks pass. Numeric pairing currently covers explicitly supported
single-token stat labels. Arbitrary skill names, vertical table reconstruction and
same-label entity disambiguation are not certified. Provenance is retained for those
cases so later local repair can use the original coordinates.

## Phase 3: vertical tables, open skill labels and local OCR repair

- Pair supported labels with values below them only when at least two aligned
  columns establish a table. Require a unique nearby value per column and reject
  intervening text/competing cells. Vertical means header-over-value tables, not
  rotated vertical writing.
- Extract arbitrary multiword skill labels immediately preceding percentage values
  from native word rows, preserving label/value boxes. These are evidence candidates,
  not an assertion that every percentage in prose is a skill.
- Parse explicit Markdown header/value tables when checking candidate pairings.
- Crop suspicious native blocks for local Tesseract only. Limit attempts per document
  (default eight) and record skipped regions; retain each crop's coordinates, original
  text, OCR text and decision. No new paid region calls.
- Accept a replacement only if its labels/numbers/word coverage are preserved, all
  available resolved pairs match, it removes an observed corruption marker, and the
  original block occurs uniquely in the selected page. Otherwise keep a review
  candidate. Never replace a whole page with a crop or guess unsupported OCR values.
- Image-only pages retain the existing page OCR path. Rotated/ambiguous blocks and
  exhausted budgets are explicit review outcomes, not silent successes.

### Phase 3 verification and limits

Full suite: 788 passed, one skipped, 15 subtests passed. Mypy checks 74 source
files and changed-file Ruff checks pass. Synthetic regressions cover vertical
header/value alignment, swapped Markdown cells, intervening text, open skill names,
percentage swaps, bounded crops, ambiguous replacement targets and numeric retention.

A real local Tesseract call repaired a deliberately damaged synthetic text layer
while preserving the visible source, intact numbers, header and footer. Native
geometry probes on the supplied PDFs found 127 open skill-label candidates on the
pregen cards in addition to the previous 140 stat candidates. No vertical-table
candidates were found on those four PDFs; vertical support is verified by synthetic
fixtures rather than claimed as a measured improvement on those documents.
See [local evaluation](evaluations/pdf_region_ocr_probe.json).

Open skill extraction requires explicit percentage values and word-separated labels;
it does not infer every number in narrative prose as a skill. Vertical recognition
requires a supported stat-label grid, unique aligned cells and no intervening text.
Automatic crop replacement is restricted to observable replacement-character damage
with intact surrounding evidence; numeric corrections and uncertain table repairs
remain inspectable candidates. This intentionally avoids silently overwriting
source with unverified OCR. Image-only pages use the existing page OCR path.

## Phase 4: AI-assisted import repair without a human Keeper dependency

After local OCR, group unresolved numeric regions on each page into a cropped
vision request using the configured provider's existing analyze_image interface.
Bound calls per import, retain crop/hash, response and validation decisions. The
model must transcribe visible original-language evidence, distinguish blank from
unreadable, and never infer mechanics. Candidate context is untrusted source data.

Reuse a validated transcript only at a unique source location. Preserve established
numeric pairs and intact source numbers. Blank/unresolved Luck cannot be assigned
by this repair stage; players retain the existing Luck-roll flow. Failures do not
abort other pages or require KP Assistant. Unresolved markers remain in the source
for downstream extraction; affected character fields must not receive constructor
defaults. Manual explicit role-card values can resolve those fields through the
existing reconciliation flow. Store all decisions in parse_quality.json; gameplay
reuses the imported result instead of a fixed extra model call each turn.

### Phase 4 execution and failure behavior

Up to eight page-group requests per import, each containing up to eight suspect
blocks and nearby image context. This is import-time paid vision using the configured
provider; there is no new per-turn call and no human-KP login requirement. Tests
mock provider responses and do not spend API credit. The artifact records request
counts (provider dispatch attempts, not guaranteed billable calls), crop hash, raw
response, per-region decisions and unresolved labels. Repeated gameplay reuses the
imported artifact; explicit reparse may issue new repair requests.

Accepted text must preserve known pairs, original numbers and most intact words;
new numeric tokens must belong to requested unresolved non-Luck labels. Duplicate
region IDs, unreadable/blank responses and unexplained values are rejected. This
checks evidence consistency, not proof that a model read every pixel correctly.
Blank Luck alone does not trigger a repair call; mixed-region responses cannot
fill blank/unresolved Luck. Existing player Luck-roll behavior remains in place.

Named character pages carrying unresolved core-attribute markers remove those
attributes from extracted pregens. The constructor refuses those specific incomplete
cards instead of inserting 50; other characters/pages remain usable. Explicit manual
role-card values clear the affected flags and remain authoritative on re-extraction.
The page/name association is conservative: multiple characters on a marked page may
need clearer source separation. This is not a blanket guarantee for every NPC
mechanic or unidentified entity; unresolved markers remain available to the Keeper.

Verification: 799 tests passed, one skipped, 15 subtests passed; mypy 75 source files.
Coverage includes provider failure/budget exhaustion, readable/blank/unreadable
responses, blank Luck, contradictory additions, duplicate responses, import-to-source
integration, constructor-default prevention and repeated manual reconciliation.
No live vision quality benchmark was performed in this phase.
