# Prevent margin decoration from interleaving with PDF body text

## Problem and goal

Lightless Beacon physical page 13 is a core narrative page. With PDF SHA-256
`14437ca4eca3b6c05b22403bdad30bdb8668f1837e0a2d0ee42e49c160d8aa07`,
the current source is 3,631-character PyMuPDF4LLM layout text and has
`ambiguous_columns`. Its first body lines contain isolated margin glyphs inside
ordinary words and sentences. This is a real reading-order error; four
source-bound SAN pairs still match, so a numeric-only check cannot detect it.
The goal is to keep independently evidenced decorative margin glyphs out of
the selected body stream, without suppressing genuine text or changing the
review policy.

This branch starts at PR170's `fix/pdf-rich-ocr-candidate-selection` head.
The triage background is the report in the separate documentation branch
`maintenance/lightless-beacon-warning-triage`:
`docs/reports/lightless_beacon_review_warning_triage.md` (commit `b3346cd`).

## Reproduction and root cause

The failing, provider-free reproduction is
`/private/tmp/reading_order_p13_assert.py`: it calls the production
`_pymupdf4llm_page_chunks()` path and asserts that margin glyphs are absent
from the body text. It currently fails. This script is diagnostic, not a
repository test or a production dependency.

The page is 594 × 774 PDF points. PyMuPDF texttrace shows a distinct span at
approximately x=26.9–58.9, y=57.9–201.9, with a 24-point display font and
six vertically spaced glyphs. The adjacent left-column body begins at x=66
and uses approximately 9-point prose. PyMuPDF blocks place the glyphs in
separate margin blocks from the body (body begins at block 4). Both texttrace
and rendered page show the span is pictorial border art, despite its PDF text
mapping to letters. Its normalized signature recurs at the same margin
position on 18 distinct pages; the font is not used in the central body.
This conclusion uses position, isolation, size, repetition, and rendering,
not a font-name or word-specific rule.

Native extraction lists these glyphs separately before the prose and reports
`ambiguous_columns`; PyMuPDF4LLM's `use_ocr=False` layout reconstruction
interleaves them into prose at overlapping y positions. The loader then
selects that layout source because its word/numeric coverage checks do not
verify sequence. Paddle Layout falls back as unavailable in this replay and
is not the origin. The failure therefore enters at the layout parser's
reading-order merge and survives source selection; it is not a mechanics
pair error.

An exploratory in-memory removal of the evidenced margin span makes the
interleaved glyphs disappear and preserves all four bound SAN pairs and the
observed dice tokens. **That output also omits some unrelated body words.**
Therefore redacting a PDF and adopting the new Markdown wholesale is not a
safe fix. The implementation must operate on the existing selected source
and prove it removes only the mapped glyphs, or retain the original source
and review warning.

## Scope and non-goals

Change only the PDF source-composition seam and focused tests. Reuse the
existing repeated-vertical-span evidence in `pdf_quality.block_evidence()`
and `bind_repeated_vertical_evidence()`. Do not alter OCR engines, provider
calls, Paddle Layout, rich-candidate arbitration, image/map analysis, or
`_page_requires_review()`. Do not address handwritten handouts, the credits
page, or other warning-count cleanup. Never key a rule on a scenario, page,
font name, mapped glyph string, or fixed coordinate.

## Design

1. After repeated-span evidence is bound, identify only a margin span meeting
   the existing **combined** decorative criteria: repeated on distinct pages
   with matching normalized geometry, outer margin placement, large display
   size relative to body, and font isolation from the body. Additionally
   require a distinct text block, no overlap with a body line's bounding box,
   and a unique source-to-layout correspondence for each injected glyph.
   Vertical orientation alone, page-edge position alone, font alone, or
   repetition alone is insufficient. Ambiguous cases remain selected and
   review-required.
2. Build a bounded character alignment between the selected layout's affected
   local text window and the source-bound body lines at the same y range.
   The source lines must come from non-decorative PDF blocks; their geometry
   supplies column membership and excludes cross-column/caption matches.
   Permit layout's existing whitespace and deterministic line-hyphenation
   differences, but require a unique monotonic alignment around each glyph.
   Remove only the characters proved to belong to the decorative span.
   Attached single letters inside a prose word require the same unique
   alignment; a broad regex deleting letters is forbidden.
3. Validate the proposed text against the unmodified selected source and
   non-decorative native evidence. No non-decorative phrase, source-bound
   label/value pair, dice operator, SAN expression, percentage, or heading may
   disappear or change. The number and order of removed glyphs must match the
   qualified trace. If any validation fails, keep the old candidate and its
   review state. Successful removal changes only canonical text and adds
   bounded diagnostic metadata (status, span count, removed glyph count,
   reason); it never stores a second full text copy or marks new content
   verified.
4. Preserve existing warning history. `ambiguous_columns` can remain if the
   page is still geometrically ambiguous. Final review is determined by
   existing policy, not by a new warning exception. Re-evaluate low-text
   pending only if this targeted sanitation changes whether a page has usable
   selected text; no automatic vision/map suppression is introduced.

There is no persistent schema or library migration. The new diagnostic field
is optional and absent from old quality reports. The fix must not feed derived
vision text into canonical source validation.

### Why meaningful edge content survives

Sidebars, captions, headings, footnotes, handout labels, and genuine vertical
text are retained unless **all** decorative evidence and a unique local
alignment prove otherwise. A real vertical heading normally has a distinct
semantic block and lacks repeated, isolated pictorial margin signatures;
if a page happens to resemble the decorative pattern, ambiguity makes the
sanitizer decline. Existing block/line order and text outside the matched
local window are unchanged.

## Tests and acceptance

Write a synthetic fixture at the production source-selection seam that
reproduces two-column prose plus an isolated decorative margin span. It must
fail before the fix and pass after, proving a clean body order rather than
merely the absence of a warning. Cover normal left-then-right columns,
margin art, genuine sidebar/callout, heading/body, caption, semantic vertical
text, and mechanics (bound stat/value, dice `+`/`-`, SAN slash order, and
percentage). Also test an ambiguous alignment and a candidate that loses a
real body word: both must fail closed. The latter is required by the
exploratory ablation result.

Replay the hash-pinned real page 13 and then all 43 pages with the previously
saved MarkItDown candidates and no new external calls. Report selected
method, short before/after excerpts, warning/review state, pair checks, all
source changes, and any new/missing warnings. Check the eight PR170 rich
candidate pages without forcing promotion. Success requires continuous core
prose, zero injected margin glyphs, no observable mechanics corruption, and
no unexpected page-level source/review change. If the real core prose remains
misordered, the implementation is **HOLD** even if unit tests pass.

Run targeted reading-order and rich-OCR tests, full pytest, ruff, mypy,
compileall, and `git diff --check`. No PR is opened in this branch until the
user requests one.

## Tradeoff and open review question

Source-grounded surgical removal is deliberately stricter than rerunning a
parser on a redacted PDF. It can leave a page in review when layout output
cannot be aligned uniquely; that is preferable to dropping valid words. The
implementation review should confirm that the local alignment truly
identifies each injected glyph without interpreting ordinary letters as
decoration. The only unresolved design question is whether the existing
repeated-span evidence needs one additional generic structural check for a
particular fixture; such a check must be justified by geometry before code.
