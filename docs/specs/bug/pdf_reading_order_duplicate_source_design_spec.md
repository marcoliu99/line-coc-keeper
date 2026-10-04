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

## Source-bound design

1. Build a page-local source ledger from PyMuPDF block, line, span, texttrace,
   bbox, direction, and extraction order. Give each observable source run a
   stable **page-local identity** derived from structural provenance, not its
   words alone. When an individual PDF object identifier is unavailable, the
   ledger records that limit; it must not invent an object ID. Keep distinct
   source runs distinct even when their text is identical.
2. Align each suspect Markdown occurrence to that ledger using exact source
   characters (allowing only documented whitespace/line-wrap formatting),
   neighboring unique source lines, column/region membership, and monotonic
   source order. String equality is only a candidate match. A duplicate is
   eligible only if two output ranges uniquely map to **one** source identity,
   no second source run supports the wording, and the surrounding source
   geometry identifies which output range continues the actual paragraph.
   Heading, body, caption, sidebar, footnote, and column boundaries constrain
   the alignment. A repeated heading and body line remain separate when the
   PDF has two source runs.
3. Make a bounded local edit that removes only the extra emitted range. Never
   delete a repeated paragraph globally or choose a survivor by length or
   semantic plausibility. Whitespace or punctuation differences cannot
   trigger fuzzy dedup without independent, unique provenance. If the two
   candidates cannot be assigned uniquely, or a range crosses a structural
   boundary, keep the original layout text and review state.
4. Verify the proposed result against the source ledger and the unmodified
   selected text: every distinct source run must still be represented in its
   proper region; nonduplicate names, substantive phrases, heading/body
   order, stat/value bindings, dice operators, SAN expressions, and meaningful
   numeric tokens must survive. A duplicate mechanics block fails closed
   unless a single source identity is proved. Preserve existing warning
   history and `ambiguous_columns`; successful dedup alone does not clear
   review. Store compact diagnostic metadata (status, source-run identity or
   hash, output ranges, removed character count, failure reason), never a
   second full text copy.

There is no persistent schema or gameplay-state migration. Older quality
reports without this optional metadata remain readable. The design is
deterministic and local; no LLM or provider call decides identity.

## Safety cases and tests

Synthetic fixtures must exercise the production source-selection path and
the local edit, not only a matching helper:

| Source/output evidence | Required behavior |
|---|---|
| One source span emitted twice into one layout region | Remove only the extra emission after unique source/neighbor alignment. |
| Two different source blocks with identical text | Keep both. |
| Two visually overlapping PDF text objects | Dedup only when structural equivalence and one semantic source can be proved; otherwise retain/review. |
| Same sentence intentionally repeated in two columns | Keep both. |
| Running header and body use the same heading text | Keep both; region identity differs. |
| Duplicate stat/mechanics block | Fail closed unless source identity, complete operators, and bindings are proved. |
| Minor whitespace/punctuation differences | Never fuzzy-dedup on text similarity alone; provenance remains mandatory. |
| Ambiguous occurrence-to-source or survivor alignment | Make no edit; keep review. |
| Real sidebar, caption, footnote, vertical semantic text | Preserve even if wording matches nearby prose. |

Replay the hash-pinned page 13 and then the 43-page saved-candidate corpus
without external calls. Require one continuous rendered/source-bound opening,
no unrelated canonical-text change, no mechanics loss, and unchanged warning
and review policy. The residual decorative repair remains independently
tested. Run targeted tests, full pytest, ruff, mypy, compileall, and
`git diff --check` at implementation time. If provenance is insufficient,
leave the duplicate and report **HOLD**, rather than deduplicating by content.

## Tradeoff and review gate

PyMuPDF4LLM output currently provides no span IDs. The implementation must
prove a unique alignment back to PyMuPDF source geometry and neighboring
source order before editing; some genuine extraction duplicates will remain
unrepaired when that proof is unavailable. This is intentional. This spec is
ready for implementation review, but contains no runtime change.
