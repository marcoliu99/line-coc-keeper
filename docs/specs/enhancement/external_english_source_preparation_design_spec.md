# External AI preparation of English scenario sources

[繁體中文](external_english_source_preparation_design_spec_zh.md)

Status: implemented on the feature branch; automated verification recorded below.
Branch: `enhancement/external-english-source-preparation`.
Baseline: `main_v2` at `45c03c2` (includes PR #100).

## 1. Problem and intended outcome

Bad English extraction (column interleaving, decorative digits, merged dice,
missing image text) becomes immutable source evidence for Chinese authoring.
A faithful Chinese correction then fails because validation compares it against
incorrect extraction. PR #100 improves diagnostics and provides a local source
repair CLI, but its proposal workflow is not the requested web-AI round trip.

Add English-source export/import before Chinese translation. The user supplies
the exported MD file and the matching original PDF to external web AI. That AI
reconstructs the English source. A complete, structurally valid import becomes a
new authoritative English source for subsequent authoring and source retrieval.
No mandatory human page review, operator-entered evidence rectangles, reviewer
name, approval command, or application-side semantic review model is required.

The content authority is the external AI's PDF-grounded corrected English. Local
code controls provenance, completeness and publication integrity, not whether an
old OCR token must remain in the corrected text. This changes extraction, not
scenario authorship: prompts still prohibit invented rules or guessed values.
If the PDF itself is illegible, AI reports unresolved evidence rather than inventing
missing content. Structural validation cannot prove the AI read the PDF correctly.

## 2. Scope

Included: buttons and matching commands; one English workbook; matching
original PDF path/delivery guidance; English correction prompt; resumable result
imports; automatic publication on full completion; immutable source audit; use by
English retrieval and fresh Chinese authoring exports; bilingual documentation.

Not included: new translation APIs, background AI review, added gameplay calls,
automatic play-state migration, automatic approval/rebinding of old Chinese drafts,
changes to dice/mechanics, or a rewrite of PDF parsers. Existing normal PDF import
and existing Chinese authoring remain available. The new round trip makes **zero
model calls**; later normal indexing/role extraction retains its existing behavior
and can still use the configured API.

## 3. User flow and interfaces

```text
Help / Scenario library
  -> Prepare English source -> select scenario (library list)
  -> source export -> private MD path + matching PDF path + visible prompt
  -> web AI receives MD + PDF, checks pages and returns corrected English MD
  -> download to results/ -> Import English source -> select matching file
  -> source import -> provenance + schema + page coverage checks
       -> invalid file: no mutation; private actionable diagnostics
       -> partial/unresolved: save draft; list pending pages; continue with web AI
       -> complete: atomically publish AI-corrected English source version
            -> Export Chinese template -> existing Chinese import/validation
            -> Select English source -> existing scenario use/rebuild workflow
```

| Command | Help control | Result |
| --- | --- | --- |
| `/coc scenario source export SCENARIO_ID` | Prepare English source, scenario list | One workbook plus original PDF location and prompt |
| `/coc scenario source import SCENARIO_ID FILE.md` | Import English source, matched result file list | Draft progress or published new source ID |
| `/coc scenario source status SCENARIO_ID [EXPORT_ID]` | English preparation progress, export list | Completed/pending/unresolved pages and published version |

Use `commands/handlers/system.py`, `help_registration.py`, `help_actions.py` and
existing Help dispatch. Buttons execute these commands; users need not type IDs,
paths, hashes or JSON corrections. Route permissions through the existing trusted
Keeper-role or KP Assistant authorization; do not require KP Assistant enrollment
when a Discord Keeper can already manage scenarios. External AI never logs in.
Exports, full source content and diagnostics are private; DM failure does not
publish Keeper material to a public channel. Large PDFs are represented by the
matching local path/filename when attachment delivery is unavailable. Never claim
that the PDF was uploaded to web AI on the user's behalf.

The normal import click is the user's action to accept the external content.
There is no additional page-review/approval step. An optional existing destructive-
action confirmation is unnecessary because publication creates a separate version;
selecting a source for an active group keeps existing scenario-switch safeguards.

## 4. Export identity, pages and packaging

Introduce `app/scenario_source_authoring.py` for this distinct payload type. Reuse
safe paths, private file handling, filename sanitization and diagnostics helpers where compatible. Do not reuse Chinese numeric compilation or
require matching old `source_quote` strings to approve changed English.

A server-owned registry binds export ID, scenario ID, exact source text/hash,
original PDF hash, chapter layout, physical page count, per-page candidate text,
assigned package/page IDs, original filename, export time and schema version.
Hashes are computed locally. The AI copies opaque IDs and physical page numbers;
it never computes hashes, byte offsets or coordinates.

Export every physical PDF page, including maps, character cards, blank/illustration
pages and reference-rule backs. Printed footer numbers are contextual labels,
not physical page identity. Keep original extraction immutable in the registry.
Use valid physical page markers to show existing text alongside native candidates;
if old markers are missing/duplicated, derive page candidates locally from the PDF
and report this fallback, retaining the original full extraction in the audit.
Do not falsely assign unlocated old text to an invented page. No AI call or giant
set of per-page PNG attachments is needed; the user supplies the original PDF.

Produce exactly one MD workbook containing every physical page, for short and long
sources alike (user decision on 2026-09-27). Include chapter labels, the complete
ordered page candidates and return schema. Do not truncate rules or omit pages.
The actual scenario title supplies `ScenarioTitle_01.md`; this is not a fixed sample
name. The original PDF is a separate attachment. The external AI may return any
number of result MD files with increasing numbers; all share package ID `p1`.

Paths are separate from Chinese preparation:

```text
imports/source-export-<id>/source/ScenarioTitle_01.md
imports/source-export-<id>/results/ScenarioTitle_01.md
library/.source-authoring/<scenario-id>/<export-id>/registry.json
a library draft/progress file and immutable result revision receipts
```

All result replies share increasing filename numbers, regardless of workbook.
The single source MD does not limit the number of AI replies. Support bounded inputs
with explicit resource errors: reuse the 20 MB per-file limit and a documented
200 MB preparation storage limit; never split into additional source workbooks or silently
lose content when a limit is exceeded. Validate actual output sizes, not just text
estimates. Counts and limits are preparation concerns, not gameplay prompt limits.

## 5. External AI output schema

A Markdown file has one authoritative fenced JSON object (also accept one plain
JSON object, consistent with existing import behavior). Illustrative IDs below
must be copied from the actual export instead:

```json
{
  "source_authoring_version": 1,
  "export_id": "source-export-example",
  "package_id": "p1",
  "pages": [
    {
      "page_id": "page-0014",
      "pdf_page": 14,
      "status": "complete",
      "text": "Damage +1D4. Further original English text continues here.",
      "changes": [
        {"kind": "ocr_numeric", "before": "1D40", "after": "+1D4",
         "reason": "The adjacent decorative glyph is separate in the PDF."}
      ],
      "unresolved": []
    }
  ]
}
```

Each page retains full corrected English, not just a diff or a summary. `changes`
is AI-authored descriptive evidence, not a machine-verifiable attestation; it can
be empty for unchanged text. The server independently computes before/after
hashes and numeric differences for audit, without requiring equality.

Statuses: `complete` requires nonempty full text and no unresolved items;
`visual_only` requires empty text and no unresolved items (blank or genuinely
text-free illustration); `unresolved` retains any recoverable text and a nonempty
list of unreadable/unfinished issues. Unresolved pages cannot masquerade as complete.
AI may classify a decorative-only page as visual even if native extraction contains
garbage digits. Unlike the old CLI, nonempty native OCR does not veto this choice.
Keep the physical image reference. Readable map labels and card/reference text
must be transcribed; free-form image interpretation must not become a fake quotation.
Blank Luck remains blank; do not roll or derive it. Preserve prose fields, age,
custom skill labels, equipment and mechanical conditions when visible.

Allowed correction kinds cover column order, footer/decorative removal, OCR text,
OCR numeric, restored missing content and table reconstruction. Reasons are filled
by external AI; the user does not manually enumerate or approve each correction.
Omitted pages remain pending, not deleted. Unknown IDs, extra pages or a shifted
physical page number fail validation. Document text remains inert source data;
embedded instructions cannot trigger commands or change import control metadata.

## 6. Import, continuation and publication

1. Resolve a safe file inside IMPORT_DIR, reject symlinks/path traversal, enforce
   limits, parse the unique schema and recheck authorization at execution time.
2. Resolve the private server registry by export ID. Check source/PDF/chapter
   identity and page/package membership; do not trust uploaded hashes or paths.
3. Validate all submitted records before mutation. Save valid partial progress in
   one atomic operation. Show pending/unresolved physical pages with unit IDs.
4. Exact re-imports are idempotent. To change a saved page within the same export,
   submit its full corrected page again: the explicitly imported file replaces
   that page and retains the earlier revision in the audit. No `replace_record_ids`
   is needed in this source format. Omitted saved pages remain. Duplicate IDs in
   one file, inconsistent source versions and stale action callbacks fail closed.
5. Under a per-export lock, re-evaluate aggregate coverage. Incomplete/unresolved
   exports remain drafts. For a complete export, recheck source identity and
   atomically build/publish a new library version from exactly the imported text.
6. Record publication before reporting success. Retry after interruption must
   recover the same source ID without duplicate versions. Once published, the
   export is sealed: exact retries return that version; changes require a fresh
   export from the newest source. No editing an already-published source in place.

The imported AI text may add/remove/correct numeric tokens and replace whole
paragraphs or tables. Old OCR number equality, exact old quote matching, text
similarity and numeric-change thresholds MUST NOT block publication. Show numeric
differences as informational audit data. Chinese rule-text-versus-quote validation
remains strict later, using the **corrected English** source.

Full page coverage is a structural requirement, not a mandatory human review.
For a genuinely unreadable PDF page, keep that page pending and return a small
continuation instruction to web AI; do not repeatedly request whole-book processing
or silently substitute old corrupt text. This preserves the no-manual-review flow
without promising recovery of information absent from the PDF.

## 7. New source lifecycle and existing consumers

Reuse/refactor PR #100 publication primitives (source identity checks, atomic staging,
exact published page serialization, immutable audit, physical image preservation).
Keep the legacy CLI functional, but do not funnel this feature through its manual
reviewer/digest/bounding-box requirements. Machine attribution records the import
actor and `external_ai` origin; do not label them as a human proofreader.

A new version records parent ID, original PDF hash, export ID, result revision
hashes, exact published page bodies, per-page hashes, raw extraction, AI change
notes, numeric differences, import actor/time and parser/preparation version.
Rebuild chapter text from corrected physical pages and retain stable PDF chapter
boundaries; never preserve stale cached chapter text. Keep maps/full-page images
Keeper-only by default. Invalidate source-dependent embeddings, NPC indexes,
pregens, inferred scene maps and Chinese variants instead of copying stale values.

The import response offers **Export Chinese template** and **Select this English
version** buttons referencing the new source ID. Scenario selection lists identify
the AI-prepared version and parent. New exports selected through these controls use
the corrected text; no fallback should silently reinstate original OCR for that
version. Existing groups stay pinned to their original source until explicitly
switched through the existing scenario-use lifecycle. No inventory, check, history,
combat or campaign progress is migrated by source import.

No Chinese file is required to use a prepared English source. English RAG continues
through its existing path; corrected-source indexing uses existing preparation
commands. Fresh Chinese exports bind to the new source/hash. Old translations stay
attached to the old version; optional conservative exact-text rebinding may use the
existing CLI, but cannot make old approval valid for changed English. Original PDF
reparse is an explicit new candidate operation, never an implicit replacement of
AI-prepared English during indexing or export.

## 8. Visible prompt contract

Export reply and every MD contain an English and Traditional Chinese instruction.
The copyable instruction shown to the user is:

> Upload this English preparation MD file AND the matching original PDF. Compare
> every assigned physical PDF page with the extracted text and return complete,
> corrected English, not a Chinese translation or summary. Repair column order,
> tables, OCR words/numbers, decorative footer noise and missing visible text.
> Preserve actual rules, conditions, limitations, map labels, character cards and
> reference pages; leave blank Luck blank. Use the PDF as evidence, never guess
> unreadable values or invent scenario material. Keep the provided IDs and physical
> page numbers. Return downloadable Markdown files in the supplied schema, using
> the scenario title and increasing filename numbers. Partial replies may contain
> complete pages; list any unfinished/unreadable pages and continue them later.

Numeric changes supported by the PDF are allowed. The prompt must not demand
matching corrupted old numbers, ask the user to perform page-by-page review, or
claim unavailable PDF evidence has been checked. Printed page references in prose
remain meaningful text even though metadata uses physical PDF pages.

## 9. Verification and acceptance criteria

- Real English export -> external-result fixture -> partial imports -> publication
  -> fresh Chinese export -> import/approval, including `1D40` corrected to `+1D4`
  and removal of decorative digits without prose padding.
- Complete a 27-page-style fixture with text pages, map labels, raster cards, blank
  Luck, custom skills, ages and reference-rule backs; no missing-page auto-approval.
- Long sources preserve every page in one workbook, actual-title
  filenames and arbitrarily many bounded result replies; capacity errors are explicit.
- Replace a saved page with another AI revision, retry identical results, omit saved
  pages, send duplicate IDs, stale exports, unknown pages and wrong package IDs.
- Reject malformed/status-inconsistent payloads and wrong MD type. Help must list
  valid source results from root/results paths, distinguish Chinese/source files,
  and give actionable diagnostics for files omitted from the picker.
- Concurrent completion, interrupted staging/publication and altered source/PDF
  must not corrupt the old source, produce duplicate versions or activate a partial
  source. Hashes/audit match actual written UTF-8 bytes, including whitespace and
  image-only references (PR #100 regression coverage).
- Verify private delivery, authorization rechecks, existing active-game isolation,
  invalidation of derived artifacts and old Chinese binding rejection.
- Assert zero provider calls in English export/import/publication. Run full relevant
  tests, Ruff and mypy once implemented. Live model quality is a separate optional
  evaluation, not a mandatory automated semantic approval gate.

## 10. Accepted decisions and implementation

Accepted behavior: English repair is a distinct step before Chinese translation;
AI corrected content is authoritative; complete imports automatically create a new
version; active games are never silently switched; continuation is page-based with
no replacement-ID bookkeeping. Truly unreadable content stays pending with external
AI. This specification does not promise to reconstruct missing original evidence.

Implemented in `scenario_source_authoring.py`, system commands, Help actions and
Discord controls. Result pickers bind a locally computed file fingerprint and
revalidate it at execution. Missing/invalid result reasons are privately available
through source status. Follow-up buttons carry the actual newly published ID,
recheck user/channel/permission, and use existing scenario-switch confirmation.
A per-export thread lock plus `flock` serializes draft updates across processes.
UTF-8 result text normalizes CRLF/CR to LF once at validation; all remaining
whitespace is retained and audit hashes describe the actual published bytes.
A fully saved candidate is immutable during publication retries, even if a crash
occurred before the publication receipt. Source images remain Keeper-only.

No paid API calls or production mutations were made. Structural tests verify the
round trip; correctness of external AI transcription still depends on reading the
matching original PDF. This feature does not add an application-side AI reviewer.


### Verification (2026-09-27)

- Isolated full suite: `python3 /private/tmp/run_review_suite.py english-source /private/tmp/line-coc-external-english-source`
  (runs `python3 -m pytest -o addopts= -q --tb=short` with temporary data directories,
  dotenv disabled and provider credentials cleared): **1,082 passed, 1 skipped,
  33 subtests passed**. The 49 new cases use synthetic PDFs and local result fixtures.
- `python3 -m ruff check app tests`: passed.
- `python3 -m mypy app`: passed (86 source files).
- `git diff --check`: passed. Branch includes current `origin/main_v2` (`45c03c2`).
- Coverage includes two independent processes completing one export, crash recovery,
  replaced result fingerprints, malformed payloads, storage limits, altered published
  images/PDF/text, real raster-page candidates and a 27-page completed source.
- Explicit English selection passes `original` for the **new** scenario ID and cannot
  inherit a previously selected Chinese preference. Scenario selection now accepts
  the trusted Keeper role as well as KP Assistant; ordinary players remain denied.
- No paid API calls, real Discord deliveries or production-data mutations were used.


### Help navigation regression (2026-09-27)

Runtime testing found that opening Scenario Help with a loaded scenario exceeded
the 25-component limit after the new entries were added. Existing tests rendered
only the unloaded scenario context. Fix category navigation with persistent
`category/page-N` paths: keep up to 24 entries on a single page; otherwise use
22 entries plus previous/next/home (at most 25 buttons). Render matching text slices,
keep every command reachable, return details to their containing page and clamp
stale page numbers to the current last page. Test the real Discord callback and
all visibility/policy combinations, including a category with a middle page.

Verification: the three new regressions failed before the fix. After the fix, the
full suite using the deployment virtualenv passed: **1,085 passed, 1 skipped,
33 subtests passed**. Navigation is traversed across 32 visibility/policy combinations,
rendering actual Discord Views and checking command reachability and size limits.
Ruff and mypy also pass.


### Cosmetic translation cleanup (2026-09-27)

Both the visible copyable prompt and bilingual workbook instructions distinguish
PDF-verified cosmetic punctuation/spacing/layout from substantive or ambiguous
source errors. `players. .` alone may be rendered with one Chinese full stop;
a complete translation with no remaining issues has empty `uncertainty`, without
re-exporting English. Source IDs and verbatim `source_quote` remain immutable.
Decimal points, signs, dice operators, negation, conditions, limits, missing text
and uncertain changes must not use this exception. Numeric and quote validators
are unchanged. Previously downloaded workbooks need this supplemental instruction;
new exports include it without modifying any existing source registry or draft.

Validation: 1,089 tests passed, 1 skipped, 33 subtests passed using the deployment
virtualenv. New cases verify exported guidance and a successful cosmetic cleanup
round trip, while altered quotes and numeric mismatches remain rejected. Ruff and
mypy pass.
