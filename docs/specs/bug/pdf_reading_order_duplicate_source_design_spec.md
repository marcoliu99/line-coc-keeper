# Deduplicate repeated PDF source emission, not repeated prose

## Problem and evidence

Lightless Beacon PDF page 13 (PDF SHA-256
`14437ca4eca3b6c05b22403bdad30bdb8668f1837e0a2d0ee42e49c160d8aa07`)
has one rendered opening line beneath the “South Pier” heading. PyMuPDF's
native text, block/line/span extraction, and text trace each contain that line
once. The PDF page has one content-stream xref (`71`); PyMuPDF does not expose
an individual text-object xref for this line. The relevant source is block 5,
line 1, span 0, bbox approximately `(66, 189, 284, 204)`, 9-point horizontal
body text. Its text trace is sequence 26, type 0, opacity 1, with one matching
character run. A rendered crop likewise shows one copy and no overlapping
text-layer run.

The same 52-character opening occurs **twice** in raw PyMuPDF4LLM Markdown
with `use_ocr=False`: first immediately after the heading (line 3), then at
the start of the continuing paragraph (line 5). The full-document raw chunk
has occurrences at offsets `447–499` and `502–554`; the project whitespace
normalization retains both. Native extraction has only one occurrence. Source
selection chooses the already-duplicated `layout` candidate, and the
decorative-span local repair removes six unrelated margin glyphs but leaves
both copies. The two Markdown occurrences therefore correspond to one PDF
source run; this is **RECONSTRUCTION_DUPLICATION**, first visible at the
PyMuPDF4LLM reconstruction boundary. The internal PyMuPDF4LLM traversal path
that emitted it twice is not exposed and is not assumed here.

This spec follows the decorative-interleave repair. It addresses the residual
duplicate only. It does not authorize a text-equality deduplicator.

## Goal, scope, and non-goals

Allow a later implementation to remove an extra *emission of the same PDF
source evidence* while preserving intentional repeated wording. Limit the
change to the PDF layout/source-composition seam and focused tests. Do not
change OCR, Paddle Layout, MarkItDown candidate selection, vision/maps,
mechanics comparison, or final review policy. Do not rerender or reparse a
modified PDF to replace the whole page. No scenario, page, phrase, font, or
fixed coordinate may appear in a production rule.

## Source-run provenance model

A source run is the smallest PyMuPDF line/span character interval that can be
uniquely tied to texttrace glyph geometry. The identity is **page-local within
one extraction**, not a durable ID across library versions. Required fields
are page index, block/line/span indices, the exact source character sequence
and its offsets within the span, writing direction, bbox, and extraction
order. A unique texttrace binding must additionally identify sequence number,
character interval, and glyph boxes; if texttrace is absent or one source
interval matches multiple trace intervals, automatic dedup is unavailable.
In p13, a single trace run contains several source lines, so the whole trace
bbox or sequence number alone is insufficient: use the matched glyph interval.

Font and font size corroborate the block/span-to-trace binding and region
continuity; they cannot independently identify a source run. A PDF object/xref
may be recorded if the API truly exposes it for the text object, but is not
required and must never be fabricated from a page content-stream xref. Two
different block/span identities remain different even if their text, font,
and bbox overlap.

Normalize page coordinates before comparing independent geometry records.
Quantize coordinates to 0.25 PDF point for a diagnostic fingerprint, while
checking the unrounded boxes with an inclusive maximum 0.5-point difference
per edge and compatible glyph direction. This tolerance can corroborate one
block/span-to-trace match, not merge two different source identities. Missing
boxes, geometry beyond tolerance, or multiple plausible trace bindings fail
closed. Record the page dimensions and the normalization rule with the
fingerprint. Exact source characters remain in the transient ledger; quality
metadata contains only an identity hash, not the prose.

## Layout-to-source alignment and duplicate qualification

1. Segment the selected PyMuPDF4LLM output into bounded paragraph/line
   ranges. Match a suspected repeated range to source-run characters exactly,
   permitting only deterministic whitespace and line-wrap normalization.
   Preserve punctuation and all mechanics operators. Search locally around
   source-order anchors rather than throughout the page. Build candidate
   mappings to the ledger; text equality merely proposes a mapping.
2. Use neighboring *uniquely mapped* source runs to establish each output
   range's column/region, preceding and following source order, and paragraph
   continuity. The source run's PDF geometry determines its region; output
   strings have no PDF bbox of their own. Reject an alignment that would cross
   a column, heading/body, caption/body, sidebar, footnote, or unrelated
   paragraph boundary. No semantic similarity or LLM call is used.
3. Qualify a duplicate only when two distinct output ranges both map uniquely
   to **the same source-run identity**, raw spans and texttrace contain that
   run once, native extraction has one corresponding occurrence, rendered
   evidence has no second semantic region, and no distinct source run could
   explain either output range. The alignment and region assignments must be
   unique. Two identical PDF blocks, intentional repeated columns, or
   overlapping PDF text objects are **not** a single emitted source run.
   The runtime must rule out a second text-painted region through texttrace;
   rendered crops are checked in real-PDF validation. If image-only content
   could contain a second semantic copy and that cannot be ruled out, retain
   review and decline automatic dedup. Rendering is corroboration, never a
   substitute for the source ledger.

## Deterministic survivor and fail-closed rule

For each possible survivor, virtually remove the other range and test the
source-run sequence on both sides. Keep the occurrence whose surrounding
uniquely mapped runs continue the same source paragraph in geometric reading
order, including the next source line and its region. An isolated emission
that interrupts this continuity is the removal candidate. On p13 the later
range continues into the next PDF body line, whereas the earlier range is an
orphan prefix after the heading; this is evidence for that page, **not** a
general keep-later rule. If both possible survivors pass, neither passes, or
neighbors cannot be uniquely mapped, do not edit. Never default to first or
last occurrence.

The edit removes only the proved extra output character range and its locally
attached separator, without changing neighboring content. It is not a global
paragraph/line cleanup, `text.replace`, identical-string hash filter,
semantic dedup, fuzzy match, or whole-page reparse. Minor formatting
differences never justify dedup without the same unique provenance proof.
An ambiguous source match, multiple layout matches with no unique survivor,
or an unresolved decorative insertion in the alignment window leaves the
selected text and review unchanged.

## Processing order and preservation

Choose **decorative repair → source-duplicate dedup → final preservation**.
Decorative repair first removes only independently bound margin glyphs; they
otherwise corrupt exact output-to-source alignment. It emits its own evidence
record and validates its local edit. Duplicate alignment then uses the
*post-decoration* text and offsets; it never reuses pre-repair offsets. If
decorative repair fails or leaves uncertain glyphs in the duplicate window,
dedup fails closed there. Both transformations retain separate records.

After the proposed dedup, run one final source-bound preservation pass over
the composed result and retain each transformation's local validation. Every
**distinct** source-run identity must remain represented in its proper region
and reading order. Compare mechanics multiplicity to the source ledger, not
the duplicated layout: a legitimate repeated stat block from two source runs
must retain two occurrences, whereas two emissions of one proved run may
become one. Check stat/value and skill/value bindings, dice count/faces and
`+`/`-` modifiers, SAN slash order, percentages, damage, names, and other
meaningful numeric tokens before committing the edit. If any check fails,
restore the pre-dedup selected text, keep warning history, and retain review.

The optional quality record has `status` (`duplicate_source_emission_repaired`,
`duplicate_source_emission_ambiguous`, or a specific failed-preservation
reason), source-run identity hash, post-decoration output ranges, kept range,
removed range and character count, unique source/trace/neighbor match flags,
and preservation result. These booleans are evidence, not a probabilistic
confidence score. Preserve `ambiguous_columns` and final review policy;
successful dedup does not certify the rest of the page. No complete source
prose is duplicated in metadata. There is no persistent schema or gameplay
state migration; old quality reports without this optional record remain
readable.

## Safety cases and tests

Synthetic fixtures must exercise the production source-selection path and
the local edit, not only a matching helper:

| Source/output evidence | Required behavior |
|---|---|
| One source span emitted twice into one layout region | Remove only the extra emission after unique source/neighbor alignment. |
| Two different source blocks with identical text | Keep both. |
| Two visually overlapping PDF text objects | Keep/review: two raw objects are not one emitted source run; any future source-object dedup needs a separate proof and design. |
| Same sentence intentionally repeated in two columns | Keep both. |
| Running header and body use the same heading text | Keep both; region identity differs. |
| Same mechanics block from two source regions | Keep both, including two `SAN 1/1D6` occurrences. |
| One mechanics source run emitted twice | Remove one only after unique source and survivor proof, then source-ledger mechanics preservation. |
| Minor whitespace/punctuation differences | Never fuzzy-dedup on text similarity alone; provenance remains mandatory. |
| Ambiguous occurrence-to-source or survivor alignment | Make no edit; record `duplicate_source_emission_ambiguous`; keep review. |
| Real sidebar, caption, footnote, vertical semantic text | Preserve even if wording matches nearby prose. |

Include a p13-shaped fixture with one raw span emitted twice, an orphan prefix
and a paragraph-continuing copy; prove the survivor is chosen by source order,
not output position. Test the inverse ordering too. Include a synthetic
decorative insertion followed by duplication, and an ambiguous decorative
repair that blocks dedup in its window. Tests must assert the warning and
review state remain conservative.

Replay the hash-pinned page 13 and then the 43-page saved-candidate corpus
without external calls. Require one continuous rendered/source-bound opening,
no unrelated canonical-text change, no mechanics loss, and unchanged warning
and review policy. The eight PR170 rich-candidate pages must retain their
selection behavior; pages 3, 18 and 29 must not change. The decorative repair
remains independently tested. Run targeted tests, full pytest, ruff, mypy, compileall, and
`git diff --check` at implementation time. If provenance is insufficient,
leave the duplicate and report **HOLD**, rather than deduplicating by content.

## Tradeoff and review gate

PyMuPDF4LLM output currently provides no span IDs. The implementation must
prove a unique alignment back to PyMuPDF source geometry and neighboring
source order before editing; some genuine extraction duplicates will remain
unrepaired when that proof is unavailable. This is intentional.

## Implementation record

`pdf_quality.repair_duplicate_source_layout()` now checks one raw span and
painted trace run, unique native/layout occurrences, same-block neighboring
source lines, and a unique paragraph-flow survivor. `pdf_loader.extract_text()`
calls it after decorative repair. An uncertain alignment or failed mechanics
preservation leaves the selected source and review unchanged. The new focused
tests cover distinct source blocks, columns, headers, overlays, both survivor
positions, ambiguity, and legitimate versus duplicated mechanics.
An ambiguous or failed-preservation decision adds its own review-required
warning; a repaired duplicate does not clear existing warnings.

The hash-pinned 43-page saved-candidate replay changed only page 13's selected
text: 3,620 to 3,566 characters after decorative repair, with the duplicate
opening reduced from two occurrences to one. Review pages (23), warnings
(66), and the eight rich-candidate selections were unchanged; no mechanics
loss was observed. This replay did not rerun external vision or map analysis.

Final-review hardening: a second source region may be split across raw spans
while unrelated sidebar content interrupts native extraction order. A native
substring count and a single full-span count do not establish unique source
provenance. The repair now enumerates bounded source-fragment paths, retaining
each block/line/span identity, character interval, and fragment order. Layout
ranges qualify only when that ledger has exactly the one trace-bound source
sequence; any competing path or search ambiguity leaves the selected text
unchanged. The matching form ignores formatting only to discover competing
paths, never to authorize a replacement on text similarity alone.
