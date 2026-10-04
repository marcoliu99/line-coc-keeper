# Conservative Paddle three-column native-text reading order

Status: awaiting design review; production implementation has not started.

## Baseline and evidence

Branch: `enhancement/paddle-three-column-reading-order`, created from fetched `origin/main_v2` at `0e87e0670b2ec454609cc5ec63af943c0f736f01` (PR158 merged). The original workspace is untouched; use an isolated worktree.

The preserved `tests/fixtures/paddle_layout/page_05.json` contains native lines, PP-DocLayoutV3 regions, row-wise model order and independently frozen expected text. Baseline `order_native_lines` returns `fallback/not_two_columns`, not the expected complete left/middle/right order. This is a synthetic page, not a real three-column production PDF. Keep its inputs and expected oracle unchanged.

## Goal and limits

Accept only clearly separated three-column pages. Use Paddle's types and coordinates, never its three-column reading-order metadata, to place original PDF lines in complete left, middle, right column order. Titles precede body; footers follow it. Preserve native text exactly once.

Preserve PR158's two-column selection, ordering and safeguards, including cross-column native-line rejection and repair-required fallback. Single-column behavior remains unchanged. No new tools/packages/models, whole-page OCR, OCR/SAN/dice normalization changes, Python migration/CI/mypy version change, new feature flag/setup script, importer refactor, scenario library, reparse, maps/topology, start/RAG/combat/gameplay/providers, or performance optimization.

## Existing interfaces and minimal change

Reuse `NativeLine`, `LayoutRegion`, `LayoutResult`, `order_native_lines` and `reorder_with_paddle`. Keep one layout framework. Add `three_columns` to the typed reason codes; other rejection codes remain bounded and source-free. No persistence/schema or return-tuple changes.

Keep common native/region validation, line mapping and exact-once conservation. Defer existing model-order validation to the two-column branch, where its conditions and result remain unchanged. Add a small three-column branch after body-region grouping; a private geometry helper is acceptable only if it avoids duplicating validation, not a second pipeline.

For three-column classification, missing/duplicate/interleaved body order must not affect output. Model order metadata is optional when constructing regions for this path; retain current two-column requirements. No inference/model lifecycle changes.

```
PDF native lines + PP-DocLayoutV3 regions
  -> existing validity and safe exact-once mapping
  -> two body groups: existing PR158 ordering and gates
  -> three body groups: strict geometry gates, native lines sorted by column/y/x
  -> other/uncertain: existing fallback
```

## Conservative three-column acceptance

1. Validate finite, in-page positive boxes, known types, unique native IDs and nonempty lines. Preserve existing severe-region-overlap checks. Images/charts/seals/header/footer images do not define text columns or supply text.
2. Map native lines using the current coverage logic. Reject unpaired or ambiguous lines and any line touching horizontally separated text regions. Never split or rewrite a cross-column native line. A spanning prefix title must map to its own title region above body, not to body columns.
3. Group assigned `text` regions by overlapping horizontal intervals, sorted by actual x. Exactly three disjoint groups are required; do not divide the page into thirds. Titles, headers, footers, footnotes and page numbers do not define body column count.
4. Retain PR158's geometric scale: both gutters at least 2% of page width, each column region envelope at least 15% of page width. Permit unequal widths and margins. Each of the three columns additionally needs at least three native body lines and vertical extent of at least two median native body-line heights. These thresholds derive from the existing two-column gates plus meaningful repeated vertical content, not fixed fixture coordinates.
5. Require a consistent body band: column top/bottom extents must agree within the larger of two median line heights or 10% of the largest column extent. This deliberately rejects partial-height sidebars and changing column count. Reuse the disconnected-native-horizontal-band veto within each predicted column; do not discover/order extra columns from it.
6. Only `doc_title`, `paragraph_title` and `header` clearly above the body can be prefixes. Interior paragraph headings must fit a single column; interior spanning text/title, mixed two/three-column bands, unsupported sidebars/tables or ambiguous mapping falls back. Footers/numbers/footnotes must clearly lie below body. Do not repair middle spanning content.
7. For three columns only, collect all assigned lines per column and sort by native y, then x, then stable native ID. Emit prefix geometry order, complete left, complete middle, complete right, suffix geometry order. Ignore Paddle order for all three-column ordering decisions. Do not sort region-by-region and reintroduce row interleaving.
8. Before acceptance, require native ID multiset equality, each ID once, and exact unchanged line strings/token multiset. Verify full expected output and complete numeric/percentage/dice/SAN expressions in tests. Any failed check falls back.

Thresholds are intentionally conservative and may reject otherwise readable layouts. Any calibration must preserve explicit negative cases, unequal widths and original two-column outputs; do not loosen gates merely to make page_05 pass. If evidence requires a material design change, report it before implementation diverges.

## Integration, setup and logging

Use existing `PDF_PADDLE_LAYOUT_ENABLED`, PP-DocLayoutV3 CPU model and `scripts/setup_paddle_layout.py`. Runtime remains local-cache-only. Missing models/packages, initialization/inference errors and repair-required pages preserve the legacy path. Python 3.14 fallback remains as merged; live tests may use existing Python 3.11/3.13 environments without changing repository versions.

Keep existing metadata-only logs and initialization/per-page inference timings. Accepted three-column pages use `reason=three_columns`; never log PDF text or raw error payloads. No performance optimization.

## Regression plan and public observation seams

Use vertical TDD at `order_native_lines`, the real `pdf_loader.extract_text` integration with only the external model boundary stubbed, and scoped public `reorder_with_paddle` live smoke. No private-function assertions, model downloads or external API calls in CI.

- First assert unchanged page_05 input yields exact independently expected text and IDs: red on baseline, green after implementation. Demonstrate that changing/removing its body order does not change the three-column result.
- Add ordinary three-column, title+three-column, image+three-column and clearly unequal-width cases; assert exact full left/middle/right output.
- Negative cases: two columns plus short/narrow label; tiny-height third group; middle spanning title/body; upper two/lower three bands; cross-column native line (left/middle, middle/right, all three); missing/duplicate/ambiguous lines or regions; severe overlap; four groups.
- Header before body, footer/page number after body; image does not become a fourth column.
- Preserve `1d6`, `1d10`, `1d4+2`, `50%`, `+20`, `-10`, `SAN 1/1d6` exactly, while verifying their column order rather than token presence alone.
- Re-run all PR158 fixtures and fault cases: ordinary/title/complex two-column text byte-for-byte matches baseline; single column unchanged; merged short/staggered native bands still fallback; unavailable model/package/init/inference, incomplete mapping and repair-required fallback remain intact.
- Update only obsolete assertions that page_05 itself must be rejected; retain genuinely ambiguous three-column negative cases.

Run `pytest`, `ruff check .`, `mypy app`, `python -m compileall app tests`, `git diff --check`. Record actual commands/results, SHA and timing. Live CPU smoke reuses the same preserved subset/model with runtime network blocked. At most 2–3 existing real three-column corpus pages may be used if readily available; no whole-book scans/imports. If none are found, explicitly report zero real three-column evidence pages.

## Delivery

After approval, implement/test/commit/push, align with latest main_v2, open a non-draft PR targeting main_v2 and trigger Codex review (skip duplicate trigger if automatic). For each relevant correctness finding: reproduce, test, minimal fix, all checks, push and re-review. Do not expand scope.

Final report covers the user's 37 items: baseline/final/branch, classification and exact ordering, page_05 before/after, positive/negative fixtures, unchanged two/single-column behavior, text/number/dice/SAN conservation, no new tools/model/OCR/gameplay, all checks, real evidence count/timing, scope and MERGE READY/HOLD. MERGE READY requires passing CI and no unresolved correctness findings.

Review decision: approve this small shared-validation/three-column geometry branch and public test seams before production code is changed.
