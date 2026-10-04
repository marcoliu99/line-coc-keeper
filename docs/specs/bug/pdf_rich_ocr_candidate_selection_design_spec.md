# Preserve rich OCR candidates on low-text PDF pages

## Decision and evidence

The regression is source selection, not a missing OCR engine. Since PR92, ordinary `select_text()` word coverage can let a roughly 101-character native/layout remnant veto a 4,233-character MarkItDown candidate on Lightless Beacon physical page 31. Pages 32, 34, 35, 37, 38, 40, and 41 show the same pattern. A richer canonical source with a review warning can be more useful than a tiny canonical remnant with that warning. This design restores that narrow choice without returning to PR30's unconditional OCR selection.

**Superseded design:** mandatory whole-page Paddle corroboration of every newly introduced mechanic was rejected after the eight-page experiment. Strict Paddle comparison confirmed 3/311 candidate mechanics; Tesseract confirmed 0/311. A bounded stat diagnostic found Paddle could see many first-column characteristic values, but MarkItDown table keys and linear OCR text used incompatible representations. Building an all-item cross-format OCR verifier would create another problem rather than repair the source-selection regression. Do not add Tesseract voting, an OCR ensemble, or another engine.

## Scope and non-goals

Future implementation changes only the low-text graphic page's MarkItDown candidate arbitration and compact quality metadata. Keep general native/layout `select_text()` strict, including its 85% coverage check for ordinary narrative pages. Keep `pymupdf4llm.to_markdown(..., use_ocr=False)`, local Paddle OCR, ordinary Tesseract fallback, numeric Paddle verification, Paddle Layout, local repairs, budgets, map/vision flow, page images, index, pregens, review rules, providers, and gameplay unchanged. Rich-candidate selection needs no new external or local OCR call.

## Deterministic baseline strength

`baseline < _LOW_TEXT_THRESHOLD` (currently 200 characters) is necessary but **not sufficient** for rescue. Classify the selected baseline using existing page evidence and deterministic text/geometry rules. A baseline is weak only when its coverage demand is dominated by identified layout artifacts or sparse fragments and every remaining observable source item can be checked. A short rule sentence, clearly present NPC/location name, or bound mechanic is substantive even if the page has fewer than 200 characters. An unclassifiable fragment remains required evidence; uncertainty must not be relabeled an ignorable header.

Only unequivocal artifacts may be omitted from the *coverage comparison*: an isolated folio number, known picture/image markup, whitespace or decorative symbols, and isolated vertically fragmented letters established by their structure. A running header/footer may be omitted only when same-page geometry or cross-page repetition establishes that role. Do not guess by scenario title, publisher wording, or fixed page number. Preserve the original baseline and classification reason in the quality report. Without geometry/repetition evidence, an apparent heading remains required.

Required source items include substantive phrases and complete rule sentences; clearly present NPC/location names; source-bound characteristic/resource/skill label-value pairs; contextual percentages and numeric values; complete dice expressions and modifiers; complete ordered SAN slash expressions; and other observable mechanics. Use existing `numeric_pairs()` and `check_pairs()` for established native pairs. Preserve duplicate occurrences where relevant. A standalone folio is not a game value, but a number bound to a label or sentence is.

If the selected source reaches 200 characters, or a short baseline contains substantive evidence the candidate cannot preserve, retain strict ordinary arbitration. Do not classify a baseline as weak solely because it is short.

## Narrow candidate arbitration

Run only in the existing low-text graphic `pending` MarkItDown path. First run ordinary `select_text()` unchanged. If it accepts the candidate, preserve current behavior. If it rejects because of short-baseline whole-word coverage, including apparent numeric loss caused only by an independently identified folio, classify the baseline and compare the candidate against **required**, not ignored, source evidence. Actual numeric loss, pair mismatch, or mechanics conflict remains a rejection.

Promotion requires all of the following:

1. The baseline is explicitly classified weak, is below `_LOW_TEXT_THRESHOLD`, and the candidate reaches that same threshold. Do not add an arbitrary candidate-to-baseline length ratio.
2. The candidate is the existing same-page MarkItDown result, with no obvious replacement-character corruption or malformed complete mechanic.
3. Conservative deterministic comparison confirms every required baseline phrase and bound value remains present. Do not use semantic similarity or whole-page bare-number overlap to excuse a lost phrase or changed relationship. If preservation cannot be established, retain the old source and review.
4. Every resolved native `numeric_pairs()` item matches the candidate. `STR 60` becoming `STR 50`, `1d6+2` becoming `1d6`, and `SAN 1/1d6` becoming `SAN 1d6/1` reject. Baseline `STR 60` plus candidate `STR 60`, `DEX 70`, `LUCK 50` preserves the known source.

The candidate's **new** stats, skills, prose, and other values require no independent Paddle vote. A weak baseline without them cannot certify them, but inability to certify is not a contradiction. Promotion means `source_preserved=true`, **not** `candidate_fully_verified=true`; review may remain. Long unrelated text that drops a substantive baseline item still rejects. If the baseline has no required item, same-page provenance, explicit weak classification, candidate-length floor, and basic corruption checks are the minimum. Record the lack of independent content verification and retain review.

When accepted, select the existing MarkItDown candidate as canonical and set `method=markitdown`. Keep native/layout/MarkItDown candidates, page image, historical warnings, and downstream `pending`/vision/map behavior. Do not change `_page_requires_review()` or automatically clear warnings. A useful canonical source with `review=YES` is an intended outcome. When rejected, retain the old source and review path.

## Quality metadata and compatibility

Add or adapt compact `rich_candidate_selection` evidence: `attempted`, `status`, `reason`, `baseline_strength`, baseline/candidate character counts, required/preserved source item counts, numeric/mechanics conflicts, `source_preserved`, and `candidate_extra_content_verified=false`. Statuses include `accepted`, `not_low_text_source`, `strong_baseline`, `candidate_too_short`, `source_content_loss`, `numeric_loss`, `pair_mismatch`, `mechanic_loss`, and `insufficient_source_evidence`. Do not store complete source prose or duplicate OCR text, or call new candidate mechanics verified. Old quality reports without this field remain readable; there is no DB, API, publication, or gameplay schema change.

The former `rich_ocr_validation` and mandatory Paddle branch are **superseded**. The rich-candidate-only verifier dependency was removed without changing independent pre-existing Paddle/Tesseract paths.

## Tests and real validation after implementation approval

Synthetic tests: (1) an evidenced folio/running header and isolated vertical letters absent from a rich character-sheet candidate still allow selection; (2) baseline `STR 60` plus candidate `STR 60 / DEX 70 / LUCK 50` accepts without Paddle; (3) `STR 60` to `STR 50` rejects; (4) `Damage 1d6+2` to `1d6` rejects; (5) `SAN 1/1d6` to `1d6/1` rejects; (6) a substantive short Keeper sentence omitted by the candidate rejects; (7) a >200-character narrative baseline retains strict coverage; (8) long unrelated prose missing `STR 60` rejects; (9) a short unclassifiable heading without artifact evidence remains required; (10) acceptance preserves review history and does not mark new content verified; (11) ordinary Paddle/Tesseract fallback, numeric verification, images, and map trigger remain unchanged. Except for an explicit too-short rejection, synthetic candidates include neutral form content to meet the 200-character floor, so each test exercises its intended gate. Fixtures use synthetic text only.

For the private 43-page Lightless Beacon PDF, verify SHA-256 `14437ca4eca3b6c05b22403bdad30bdb8668f1837e0a2d0ee42e49c160d8aa07` before replay. Report sanitized old/new method and character counts, candidate length, baseline strength, required/preserved item counts, conflicts, decision, and review for pages 31, 32, 34, 35, 37, 38, 40, and 41. Inspect regression pages 3, 6, 13, 16–19, 25, and 27–30. Do not force eight promotions: if page 31 still rejects, identify the exact baseline item and why it is substantive or ignorable rather than adding another OCR verifier. Compare page images, scene maps, MarkItDown/vision calls, parse time, and rich-candidate Paddle verifier calls (expected **zero**). Any natural `pending` removal follows existing behavior; do not add a performance shortcut. Run full pytest, ruff, mypy, compileall, and diff-check after implementation.

## Tradeoff for review

A weak baseline is incomplete evidence, not complete ground truth. A richer same-page candidate can improve usability without proving every new value correct; retaining the review warning exposes that uncertainty. The hard boundary is **no contradiction of observable required source**, including numeric, dice, SAN, pair, and substantive prose evidence. If artifact classification or source preservation is ambiguous, do not promote.

## Follow-up: vertical glyph and mechanics syntax safety

Vertical letters are reconstructed as semantic source only when their words belong to one narrow text block containing no unrelated lines, share a font and compatible size, align in x, rise monotonically in y, and have bounded inter-glyph gaps. Separate blocks, columns, or caption/body regions cannot be joined. A vertical sequence may instead be classified as decorative only when the same PDF text span recurs on at least three distinct pages in an outer margin, uses an isolated display font substantially larger than the body font, and its source words bind to that span's coordinates. No book title, page number, word, or font name decides the outcome. Unproved vertical sequences remain required/ambiguous and fail closed.

Mechanics syntax is extracted before ordinary word punctuation normalization. Complete dice expressions, modifiers, ordered SAN loss, and percentages retain their operators during source comparison. Case and whitespace may normalize; `1d6+2` and `1d6-2`, or `1/1d6` and `0/1d6`, cannot compare equal. Ordinary prose punctuation remains under the existing source comparison policy. Promotion still preserves review warnings and never certifies newly introduced candidate content.
