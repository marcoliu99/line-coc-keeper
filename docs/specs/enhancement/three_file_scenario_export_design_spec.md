# At most three scenario authoring files

[繁體中文](three_file_scenario_export_design_spec_zh.md) | [Docs index](../../README.md)

## Status and goal

Category: `enhancement`. Status: **proposed; packaging policy confirmed, implementation pending**. Baseline: `main_v2` at `8e32683` (PR #94 merged). Branch: `enhancement/three-file-scenario-export`.

The current exporter couples an approximately 8,000-source-character translation batch to one physical MD. The reviewed Haunting export has 19 units and 10 batches, so it produces 10 files. The user requests no more than three files, including long campaigns.

Recommended contract: **one to three exported MD files per export**, never extra empty files. Long scenarios that fit the supported resource envelope also use at most three. Preserve all source units, rules and provenance. Three files does not promise three web-AI replies or three translated result files: translation can require several continuations, which import incrementally without losing prior work.

This change is separate from the turn-safety/latency refactor. No gameplay projection budget or source fidelity rule changes. This document is design only.

## Design decision: separate packages from work batches

Keep paragraph source units and the approximately 8,000-character logical batches. Bundle these batches into at most three physical packages; do not simply raise `BATCH_CHARS` and turn a campaign into three indivisible translation jobs.

```text
Complete scenario -> trusted source units -> small logical batches
  -> contiguous packaging -> p1.md / p2.md / p3.md (at most)
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
```

Add a short explanation: “At most three source files; a long scenario can require several AI responses. Each file contains numbered work batches. Return completed units and continue with the listed unfinished IDs.” Put detailed v2 shape and continuation/replacement examples inside each workbook, not only in bot documentation. Preserve the existing private file-delivery mechanism; this scope adds no browser automation or new platform attachment service.

Every exported workbook must also explicitly request an actual downloadable UTF-8 `.md` file, rather than leaving the artifact instruction only in the bot message. Complete and partial results use the same importable shape with exactly one authoring JSON block per file. Suggest preserving the source filename with `_zh_partNN.md`; identity remains governed by content and the registry. Progress notes belong outside the JSON. Acceptance tests must check the download, `.md` and incremental file-delivery requirements in both Help/text completion messages and actual workbook instructions. These tests verify the bot instructions, not a guarantee of external website attachment capabilities.

The web AI is instructed to preserve package/export/batch/unit IDs, source quotations, mechanics and privacy; never summarize to fit a reply. Subsequent outputs keep the same export/package identity and contain only new completed records or explicit replacements. The user should not need to hand-edit JSON or split the source files.

## Interfaces and verification plan

| Component | Change |
| --- | --- |
| `app/scenario_authoring.py` | Separate unit/batch/package generation, v2 envelope, transactional candidate merge; retain v1 |
| `app/scenario_templates.py` | V1/v2 dispatch, package message/progress, existing diagnostics and candidate save |
| `app/commands/handlers/system.py` | Shared private export/import progress through existing Help/text route |
| `tests/test_scenario_authoring.py` | Packaging, partial merge, compatibility, fault and completeness regressions |
| External preparation references/spec | Update EN/ZH v2 examples and retain labeled v1 guidance |

Required tests: 1/2/3/10/hundreds of logical batches produce at most three files; exact source concatenation/hashes preserved; nonempty contiguous assignments; stable partitioning including multibyte text; oversized unit/whole input preflight; no fourth file on failure; export interruption exposes no ready partial set; file-list UI contains at most three paths and the visible prompt; package spoofing and cross-batch units rejected; several partial imports within the same package accumulate without replacement; identical replay is a no-op; conflict/explicit replacement; one invalid submitted batch changes nothing; out-of-order packages/dependencies; full coverage alone does not bypass uncertainty/review; v1/v3 imports and v4 retrieval still pass; identical content imported in different orders converges; candidate-save interruption remains idempotent.

The Haunting fixture should move from ten source workbooks to three, retaining all 19 units. Also use a long synthetic scenario with hundreds of logical batches; verify file count and complete source coverage, not just a mocked batch count. Tests use temporary libraries/import directories without live API calls. After approval, run existing authoring/template/help and full regression suites. No new runtime behavior has been implemented or claimed tested in this spec change.

## Review recommendation

Approve the **at-most-three physical package** policy with small internal batches and resumable v2 import. Keep resource failures explicit and leave old exports usable. The implementation must include importer and prompt changes together; changing only the file-count calculation would make large-package continuation fragile.
