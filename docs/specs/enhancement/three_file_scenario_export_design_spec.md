# At most three scenario authoring files

[繁體中文](three_file_scenario_export_design_spec_zh.md) | [Docs index](../../README.md)

## Status and goal

Category: `enhancement`. Status: **implemented; local verification complete, not yet merged**. Baseline: `main_v2` at `8e32683` (PR #94 merged). Branch: `enhancement/three-file-scenario-export`.

Before this change, the exporter coupled an approximately 8,000-source-character translation batch to one physical MD. The reviewed Haunting export has 19 units and 10 batches, so it produced 10 files. The user requests no more than three files, including long campaigns.

Recommended contract: **one to three exported MD files per export**, never extra empty files. Long scenarios that fit the supported resource envelope also use at most three. Preserve all source units, rules and provenance. Three files does not promise three web-AI replies or three translated result files: translation can require several continuations, which import incrementally without losing prior work.

This change is separate from the turn-safety/latency refactor. No gameplay projection budget or source fidelity rule changes. The implementation and verification notes below record the delivered behavior.

## Design decision: separate packages from work batches

Keep paragraph source units and the approximately 8,000-character logical batches. Bundle these batches into at most three physical packages; do not simply raise `BATCH_CHARS` and turn a campaign into three indivisible translation jobs.

```text
Complete scenario -> trusted source units -> small logical batches
  -> contiguous packaging -> Scenario_01.md / Scenario_02.md / Scenario_03.md (at most)
  -> user uploads package(s) to external AI
  -> AI returns completed units/batches in one importable MD per response
  -> validate and merge into the same export draft
       +-> incomplete: saved progress + missing IDs; continue from same package
       `-> complete: validate dependencies/coverage -> review candidate
                    -> existing preview / approve / select
```

The limit applies to source workbook files listed by export. It does not count existing server-side registry files or import diagnostic reports. Do not hide ten loose workbooks in a ZIP and call that one file. Export does not delete unrelated or previous exports from `imports`.

## Packaging algorithm and resource boundary

1. Build the same ordered trusted source units and logical batch inventories as PR #94. Do not split units at package boundaries or change their source parent, offsets, pages or hashes.
2. Let package count be `min(3, number_of_nonempty_batches)`. Partition batches in order into nonempty contiguous groups. Pick deterministic boundaries nearest each remaining share of estimated rendered UTF-8 bytes, while leaving at least one batch per remaining package. Prefer a chapter boundary when byte balance is equivalent. Do not promise exact equal sizes.
3. Render once per package: copyable instructions/example, package inventory, authoring JSON, and the complete source for every assigned unit. Preserve neighboring context labels; context-only text never counts as translated coverage.
4. Preflight actual rendered file sizes, registry size and total export storage before publishing. Existing limits remain explicit: 20,000 records/units, 10 MB source text, 20 MB parsed JSON/file or aggregate draft, and export retention limits (100 sets / 200 MB). This proposal does not silently increase them.
5. If no valid three-package layout fits the resource checks, fail with a private resource report before exposing an incomplete package set. Never create a fourth file, truncate content or claim success. Very large inputs outside the supported envelope need a separately scoped limit/storage change; arbitrary-length campaigns cannot be guaranteed by file packaging alone.
6. Publish package files and immutable registry as one ready export from the user's perspective. An interrupted build has no success message; stage files are cleaned up or marked incomplete. `files.json` lists only ready packages.

Short input with one batch yields one file; two batches yield two; ten or hundreds of batches yield three when resource checks pass. Old ten-file exports remain importable; re-exporting creates a new independent export ID, with no silent progress migration.

## Filenames

Use `<scenario_title>_<NN>.md`, starting at `01`; pad to at least two digits (`99`, `100`, ...). Source packages are numbered in package order and stop at at most `03`. Translation outputs have a separate sequence starting at `01`, increasing across all packages and continuation responses of the same export; they may exceed `03` when translation requires more replies. Do not restart the output number for each package. The visible export message and workbook instructions must show actual example names derived from that scenario, not generic `scenario-authoring` names, random export suffixes, `_zh` or `_part` suffixes.

Use the library's display title as the prefix, with the stable scenario slug as fallback. Preserve Chinese characters; replace whitespace with underscores and filesystem separators/control characters with safe underscores. Bound filename length without removing the numeric suffix. Registry package IDs remain `p1`/`p2`/`p3`; filenames are display/storage names, never authorization or import identity.

Create each export in its own server-controlled directory under `imports`, preserving the basename while preventing collisions across repeated exports or identical sanitized titles. File listing/Help must support these controlled relative paths, retain root/symlink containment checks and distinguish export instances privately. Do not overwrite an existing export or add random characters to the requested basename. Users place AI results in a separate result location for that export, so a translated `The_Haunting_01.md` cannot overwrite its source workbook. The import UI must clearly distinguish source and returned files. The implementation tests directory-aware selection and source/result separation; changing only `mkstemp` prefixes is insufficient.

The supplied AI instructions must explicitly request these names and continuation numbering. Completion responses tell the user the next output number to request if continuing in a new chat. Numbers are an organizational aid; validated export/package/batch/unit IDs determine actual coverage and replay behavior.

## Authoring v2 and backward compatibility

Introduce an authoring envelope v2, leaving runtime schema v4 and legacy template v3 unchanged:

```json
{
  "authoring_version": 2,
  "export_id": "export-<server ID>",
  "package_id": "p1",
  "batches": [
    {"batch_id": "b1", "records": ["same record objects as authoring v1"]}
  ]
}
```

The record placeholder above illustrates the envelope only; production exports must include actual valid blank record objects and a separate fully valid synthetic example. Each output MD has exactly one top-level JSON block. Other examples use text fences; source text remains indented.

Server registry adds `packages: {p1: [b1, b2, ...]}` and explicit authoring/packaging versions, under the existing checksum. Package membership, logical batch membership and source metadata are server-owned. Unknown or cross-package batch/unit references are rejected. Preserve globally unique record IDs across all packages, and cross-batch dependency references until full validation.

Continue accepting authoring v1 exports and their existing `replace_batch` semantics. Do not reinterpret their batch IDs as package IDs or invalidate them by changing the segmentation constant. Runtime records, approval, active variant selection and PR #94 retrieval behavior remain unchanged.

## Incremental import within a large package

A web-AI response may contain only completed batches or complete individual units within a batch. Unfinished blank records must be omitted from the result and listed in the progress note. A partly translated unit cannot claim coverage. Structural unit coverage still does not prove translation fidelity; retain quotation, numeric, privacy, uncertainty and manual-review checks.

V2 merges records into the draft by logical batch and stable record ID:

- New nonoverlapping records append; identical records replay as a no-op.
- Changed existing records require a per-batch `replace_record_ids` list containing only existing IDs that are supplied in this import. An unlisted conflict fails; omitted previous records are preserved.
- Validate uniqueness, unit ownership/coverage and source-parent constraints on the entire candidate aggregate, not just incoming records. A replacement may make a draft incomplete; it cannot change an approved/selected variant.
- Validate every submitted batch before saving anything. An invalid batch leaves the prior draft/candidate untouched. Refactor a shared preparation/validation step; do not loop over the current `import_batch()` and publish earlier batches before a later failure.
- Persist one candidate draft only after the whole request is valid; publish a review-required variant only on full coverage and successful aggregate validation. Preserve deterministic content identity and crash/replay behavior across the draft/variant write boundary.
- Completion identity uses deterministic batch/record ordering so import arrival order does not create different variants for identical content.

Report completed/total units, per-package status and remaining batch/unit IDs privately. Completion progress is computed from validated saved records, never the AI's claim. Dependencies may remain unresolved while drafting, but full candidate validation must still reject missing required targets.

## Export message and web-AI instructions

Both Help and text export use the same message builder. List **at most three filenames**, package number, logical batch count and progress. Retain the original prompt sentence and append the explicit downloadable-file requirement. The entire block must be directly copyable in the export message:

```text
請依附件內的整備指引完成繁體中文翻譯，回傳可匯入的 Markdown 檔；若需分批，請列出尚未完成的部分。
請實際產生並提供可下載的 .md 檔案。若分次完成，每次都請提供包含本次已完成內容、可直接匯入的 .md 檔，並在回覆中列出尚未完成的 batch_id／unit_id。
檔名請以劇本名為前綴，格式為「劇本名_01.md」，分次回傳時數字依序累加。
```

Add a short explanation: “At most three source files; a long scenario can require several AI responses. Each file contains numbered work batches. Return completed units and continue with the listed unfinished IDs.” Put detailed v2 shape and continuation/replacement examples inside each workbook, not only in bot documentation. Preserve the existing private file-delivery mechanism; this scope adds no browser automation or new platform attachment service.

Every exported workbook must also explicitly request an actual downloadable UTF-8 `.md` file, rather than leaving the artifact instruction only in the bot message. Complete and partial results use the same importable shape with exactly one authoring JSON block per file. Use the scenario title as the filename prefix followed only by an incrementing number: `The_Haunting_01.md`, `The_Haunting_02.md`, `The_Haunting_03.md`. AI translation outputs follow the same naming rule, continuing the output sequence across responses. Identity remains governed by content and the registry. Progress notes belong outside the JSON. Acceptance tests must check the download, `.md` and incremental file-delivery requirements in both Help/text completion messages and actual workbook instructions. These tests verify the bot instructions, not a guarantee of external website attachment capabilities.

The web AI is instructed to preserve package/export/batch/unit IDs, source quotations, mechanics and privacy; never summarize to fit a reply. Subsequent outputs keep the same export/package identity and contain only new completed records or explicit replacements. The user should not need to hand-edit JSON or split the source files.

## Interfaces and verification plan

| Component | Change |
| --- | --- |
| `app/scenario_authoring.py` | Separate unit/batch/package generation, v2 envelope, transactional candidate merge; retain v1 |
| `app/scenario_templates.py` | V1/v2 dispatch, package message/progress, existing diagnostics and candidate save |
| `app/commands/handlers/system.py` | Shared private export/import progress through existing Help/text route |
| `tests/test_scenario_authoring.py` | Packaging, partial merge, compatibility, fault and completeness regressions |
| External preparation references/spec | Update EN/ZH v2 examples and retain labeled v1 guidance |

Required tests: scenario-title filenames, Chinese titles, sanitized separators, repeated-title/export collisions, separate source/result locations, numbering across packages and beyond 99, controlled subdirectory Help/import selection; 1/2/3/10/hundreds of logical batches produce at most three files; exact source concatenation/hashes preserved; nonempty contiguous assignments; stable partitioning including multibyte text; oversized unit/whole input preflight; no fourth file on failure; export interruption exposes no ready partial set; file-list UI contains at most three paths and the visible prompt; package spoofing and cross-batch units rejected; several partial imports within the same package accumulate without replacement; identical replay is a no-op; conflict/explicit replacement; one invalid submitted batch changes nothing; out-of-order packages/dependencies; full coverage alone does not bypass uncertainty/review; v1/v3 imports and v4 retrieval still pass; identical content imported in different orders converges; candidate-save interruption remains idempotent.

The Haunting fixture should move from ten source workbooks to three, retaining all 19 units. Also use a long synthetic scenario with hundreds of logical batches; verify file count and complete source coverage, not just a mocked batch count. Tests use temporary libraries/import directories without live API calls. After approval, run existing authoring/template/help and full regression suites. See implementation verification below.

## Accepted decision

Apply the accepted **at-most-three physical package** policy with small internal batches and resumable v2 import. Keep resource failures explicit and leave old exports usable. The implementation must include importer and prompt changes together; changing only the file-count calculation would make large-package continuation fragile.


## Implementation verification (2026-09-27)

- New exports use authoring v2 and a checksum-bound package registry. Units remain 4,000 characters and logical batches 8,000; ordered byte balancing yields at most three source files. Source filenames use the actual library title (scenario ID fallback), Unicode-safe 120-byte prefixes, and numeric suffixes.
- Files live in `imports/export-<id>/source/`; users save AI responses in that export's `results/`. Publication stages both directories and registry, with rollback on write/publication failure. The ready registry gates Help selection. The import picker also retains flat legacy uploads and rejects source paths, traversal and symlink ancestors.
- V2 imports merge complete records within batches and validate the whole candidate before one draft write. Explicit `replace_record_ids` protects saved translations; deterministic order and variant identity preserve replay safety. V1 `replace_batch` and legacy v3 imports remain supported.
- Both workbook and export DM explicitly request downloadable UTF-8 `.md` files. Workbooks include continuation/replacement examples. The import DM reports unit and package progress, missing batch/unit IDs and the next result filename. Long missing lists are capped in the DM, with the full private `progress.json` path supplied. Next numbering follows the highest correctly named result file in that export's results directory; users continuing in another web chat should supply that number.
- The real local Haunting source was read without modifying the game library and exported into a temporary directory: **19 units / 10 batches / 3 files**, with exact source reconstruction. Files: `The_Haunting_Scenario_trimmed_01.md` through `_03.md`, 44,521 / 45,449 / 55,372 bytes. The title comes from the manifest, not a hardcoded Chinese example.
- Synthetic cases cover 1, 2, 3, 10 and **201 logical batches**, multibyte source preservation, title sanitization/collisions, numbering beyond 99, partial-unit accumulation, reordered packages/dependencies, full-coverage validation, compatibility, Help/command privacy, resource rejection and interrupted publication/save.
- Verification: isolated full pytest suite **964 passed, 1 skipped, 33 subtests passed**; Ruff on changed Python files; `python3 -m mypy app` (83 files). No live translation/API requests or production state writes.

Resource limits are unchanged. File count is bounded, not translation reply count. Automated validation cannot certify translation fidelity, and external web attachment availability still depends on the chosen AI website. New files still require the existing human review/approval workflow before gameplay selection.


## Compatibility fix: AI saves plain JSON as MD (2026-09-27)

Real result files were placed in the correct results directory with matching export/package IDs, but the web AI saved the entire JSON object directly as `.md`, omitting the Markdown json fence. The shared Help/import parser required exactly one fenced JSON block, so the picker omitted all results.

Contract: continue requesting Markdown with one json fence, and additionally accept `.md` whose entire document is one complete JSON object, allowing a UTF-8 BOM and surrounding whitespace. Parse the complete document; do not extract guessed brace ranges from prose. Multiple objects, trailing prose, malformed JSON, top-level arrays and multiple json fences remain rejected. Help and direct import share the parser; registry, package, unit, quotation and review validation remain unchanged.

Tests cover v1/v2/v3 envelopes in both wrappers, plain-JSON Help selection and real imports, BOM/whitespace, rejection of multiple blocks/objects, and stale-source/incorrect-package validation. Copy the user's three actual results and source registry into a temporary directory for verification; do not modify or activate production game data.

Verification: isolated full suite 987 passed, 1 skipped, 33 subtests passed; Ruff and mypy (83 files) passed. The three real plain-JSON results changed from zero to three Help choices. Importing copies reported 7/13/14 quotation or dependency diagnostics at the first invalid batch of each file; no draft or playable variant was created. These are translation-evidence review issues and are not bypassed by the wrapper compatibility fix. Original files and production game data were not modified.
