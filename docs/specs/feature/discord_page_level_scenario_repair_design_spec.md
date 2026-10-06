# Page-level scenario repair from a Discord upload

[繁體中文](discord_page_level_scenario_repair_design_spec_zh.md)

Status: **backlog** (design only; nothing is implemented). Base: `main_v2` at `3fbec39`.

## 1. Problem

Scenario ingestion has two paths today:

1. PDF upload parses the whole PDF and creates a new library entry, applied as a new scenario or as a correction.
2. `scenario*.md` upload treats the whole Markdown file as the complete scenario source. It does **not** patch selected pages into the loaded PDF scenario.

`app.scenario_source_review` already has a safe administrator workflow (`prepare → edit proposal.md → check → publish`) that binds work to an immutable PDF source snapshot, validates physical page numbers, reports numeric changes, publishes a derived scenario instead of overwriting the original, and records an audit. But it needs filesystem/CLI access and a proposal that covers every page, so it does not fit the normal Discord flow:

```text
⚠️ 第 2、4、6、7、8、10、14、16、17 頁有解析品質待核對項目
```

The Keeper has an externally reviewed Markdown file for just those pages and wants to upload it.

### Desired experience

Like a `role_*.md` upload: the Keeper (or anyone) uploads one file and the bot merges it.

```text
1. Fill in the page-repair template (docs/references/scenario_page_repair_template.md)
   from the original PDF, with ChatGPT or another reviewer.
2. Upload repair_<name>.md to the conversation.
3. The bot checks it, replaces only the listed physical pages of the loaded PDF scenario,
   publishes a new immutable scenario version, and, when that scenario is still the one being
   played, switches the running game to it without resetting anything.
4. The conversation reply says which pages were replaced; the KP gets, by direct message, exactly what changed on each page including every number that changed (the detail is never put in the conversation, see section 10).
```

There is no export command and no hash to copy, and nothing to compute. The template names every field: the scenario title and page count (both shown in the bot's load message), and for each repaired page its physical page number, the complete corrected text, the page kind (`text`, `map` or `image`) and a short review note. Everything else, including hashes and number changes, is worked out by the bot. No PDF OCR is rerun.

## 2. Goals

### 2.1 Functional goals

The implementation MUST:

1. Recognize Markdown attachments whose filename begins with `repair_`.
2. Treat the file as a **page patch**, never as a complete scenario.
3. Apply it to the PDF-derived scenario that is loaded in the conversation, and reject it when the page count differs.
4. Replace only the explicitly listed physical PDF pages, each with its complete corrected text.
5. Compute, deterministically, every number that changed on each replaced page and report it privately to the KP and in the audit, never in the conversation reply.
6. Reuse the original PDF bytes and page images.
7. Create a new immutable scenario library entry and never overwrite the parent.
8. Record parent/child provenance and an audit.
9. Clear parse warnings only for pages that were explicitly replaced and keep warnings on untouched pages.
10. Preserve the running game when applying the repair to the active scenario.
11. Be idempotent: uploading the same file again must not create more versions.
12. Remain compatible with the existing `scenario_source_review` CLI workflow.

### 2.2 Non-goals

Version 1 MUST NOT:

- accept diffs, line-number patches or search-and-replace;
- fuzzy-match replacement text or guess which scenario or page a file is "probably" for;
- mutate the parent scenario in place or change the original PDF;
- replace map graph topology through the repair file (the existing map pipeline and `map_*.yaml` own it);
- treat a free-form Markdown document as a repair;
- rewrite translation variants automatically;
- rerun OCR over the whole PDF;
- offer an export command or require the Keeper to copy hashes from the bot.

## 3. Existing architecture to reuse

`app/scenario_source_review.py` (page split, full-page review, immutable derived publication, audit), `app/scenario_library.py`, `app/trusted_scenario_source.py` (`publish_derived`), `app/services/scenario_lifecycle.py` (`_repair` is the running-game semantics for a source correction), `app/services/scenario_ingestion.py`, `app/commands/handlers/uploads.py` and `app/scenario_numbers.py`. The new feature must not invent a second meaning of "repair".

## 4. Architectural decision

```text
Discord upload router
        ↓
scenario_repair.handle_repair_upload()       app/services/scenario_repair.py
        ↓
scenario_page_repair.parse_markdown_bytes()  app/scenario_page_repair.py
scenario_page_repair.check()
scenario_page_repair.publish()
        ↓
scenario_lifecycle.activate_repair_version()
```

Parsing and publication logic does not live in `discord_bot.py`.

## 5. Attachment routing

In `app/commands/handlers/uploads.py`, add `repair_*.md` before the generic Markdown comparison path:

```text
PDF → scenario*.md → repair_*.md → map_*.yaml → role_*.txt/.md → generic .txt/.md compare
```

Exactly one repair file per message in v1; more than one is rejected with a specific message. A repair file never falls through to `handle_scenario_compare_upload()`.

## 6. Permission model

Anyone in the conversation may upload a `repair_*.md`, the same as a PDF or `scenario*.md` upload today. The check is the existing scenario-lifecycle policy:

```python
permissions.may_manage_scenario_lifecycle(state, user_id)
```

which is true for everyone by default and is restricted to the KP Assistant when `SCENARIO_LIFECYCLE_KP_ONLY` is on; the refusal is `permissions.kp_only(...)`. Open upload is safe because a repair is page-bound, reports every number it changes, creates a new immutable version and leaves the parent untouched and selectable.

`handle_uploads()` does not receive the uploader today. Add `user_id` and the uploader's display name (`message.author.display_name`, resolved by the transport) to its signature and update the caller in `discord_bot._handle_message()`; the service never reaches back into Discord, so the audit metadata is never fabricated. The authoritative reviewer identity is the Discord user ID and display name, the conversation, the request ID and a timestamp, never a field inside the file.

**Private delivery port.** The numeric report needs `delivery.send_dm()`, which lives in `app/discord_transport/delivery.py`. The service does not import it: `discord_bot` builds a `send_private(user_id: str, text: str) -> Awaitable[None]` callback from a new raw operation `delivery.send_dm_message()`, which sends exactly one message: it applies no display-alias expansion and no chunking, and raises if the text is longer than one Discord message (so a page can never turn into several sends or be silently cut by `_chunk_text()`'s ten-chunk cap). The report quotes scenario text, not character display names, so skipping `shown()` loses nothing; if a transformation is ever wanted it must run **before** pagination. The callback is built from that operation instead of `send_dm` and passes it through the router and `handle_uploads()` into `services/scenario_repair`, the same way existing command handlers receive their delivery callbacks. The service raises or returns the failure of that callback to its delivery-state logic (section 10) and nothing else. Tests inject a fake callback that records messages or raises.

**Load confirmation shows the page count.** `target.page_count` is promised as visible in the bot's load message, but `_pdf_upload_confirmation_text()` prints only the title and character count today. It gains a `page_count` parameter (the number of physical page markers in the stored text) and prints it next to the title as `共 N 頁（實體頁數）`; both the immediate and the deferred upload paths pass it. A test builds the confirmation for a 27-page text and checks that `27` appears, and the template's instructions point at that line.

## 7. Repair file format

Markdown containing exactly one fenced `json` block (the `authoring.parse_markdown()` convention). Only these keys are valid, anywhere:

```json
{
  "repair_version": 1,
  "target": { "title": "The Haunting Scenario trimmed", "page_count": 27 },
  "patches": [
    {
      "page": 10,
      "text": "THE BASEMENT\n\nROOM 1: Storage\n...",
      "page_kind": "text",
      "review_note": "Checked against PDF page 10; repaired two-column order and the dice expression."
    }
  ]
}
```

- `repair_version` is `1`.
- `target.title` is the title of the loaded scenario, as the load message shows it (`已載入劇本《…》`), and `target.page_count` is the PDF's physical page count. Together they are the check that the file was made for this PDF: a different scenario that happens to have the same number of pages is rejected by its title. **Known limit, accepted by the project owner:** two PDF scenarios with the same displayed title and the same page count (for example two revisions of one module) cannot be told apart by these two fields, so a repair prepared for one revision is accepted against the other. The file carries no source fingerprint on purpose: the reviewer works from the PDF and the load message, and neither shows a hash. The exposure is bounded by what the design already does: the numeric report goes privately to the KP and shows what changed on each repaired page, the audit records the PDF SHA and the page hashes, the overlap notice names earlier repairs, and a wrong repair is corrected by uploading a corrected file.
- `patches` has 1 to 100 entries, one per page, in any order.
- `page` is the **physical PDF page**, 1-based, never the printed book page.
- `text` is the **complete** corrected text of that page and replaces the whole page. It must not contain a physical page marker (`library.PAGE_MARKER_RE`); the system owns the markers. Leading and trailing whitespace is trimmed.
- `page_kind` is `text`, `map` or `image`.
- `review_note` states what was checked against the page and what was corrected; it cannot be empty.

Unknown keys, a duplicate page, a page outside `1..page_count` and a wrong type are rejected. The file is UTF-8 or UTF-8 with BOM, CRLF is normalized, the size is bounded by the authoring file-size limit, and no YAML or other permissive parser is used. A template ships with the format (see "Template and help").

### 7.1 `page_kind`

- `text`: normal prose, rules or handouts. `text` must not be empty.
- `map`: a page that is mainly a floor plan or diagram. Transcribe the readable labels and do not invent room descriptions; low text volume is not a parse failure, so this kind may clear a `low_text` warning. The map graph itself is never edited here.
- `image`: only when the page truly has no readable text. `text` must then be empty, and the kind is rejected when the PDF page has native readable text (the existing `scenario_source_review.image_only` rule). The published body is the existing image placeholder.

## 8. Full-page replacement, lossless

Do not support partial edits. The merge splices only the selected page bodies:

```python
spans = locate_page_body_spans(parent_text)      # lossless offsets of each physical page body, all taken from the ORIGINAL text

pieces, cursor = [], 0
for patch in sorted(patches, key=lambda p: p.page):          # ascending source offsets
    start, end = spans[patch.page - 1]
    pieces += [parent_text[cursor:start], published_body(patch)]   # untouched text up to the span, then the replacement
    cursor = end
pieces.append(parent_text[cursor:])
candidate = "".join(pieces)                                  # one pass: no offset is ever recomputed or invalidated
```

`published_body(patch)` is the text actually written into the page: `patch.text` for `text` and `map` pages, and for `image` pages (whose `text` is empty) the existing image placeholder produced by the public `scenario_source_review.published_page_text()` (`[SOURCE_IMAGE page_N.png: reviewed image-only page]`), never the empty string. The candidate, the digest, the audit hashes and the numeric report all use this published body. All offsets come from the original text and the result is built in one left-to-right pass over the copied gaps and the replacements, so a replacement that is longer or shorter than the page it replaces cannot shift a later span, and the order of the patches in the file does not matter (they are sorted by page first). Mutating the parent text in place while reusing offsets computed once is not allowed. A test patches three non-adjacent pages with longer, shorter and empty-line-padded replacements, in shuffled file order, and compares the result with an independently built expected text; a further case patches an `image` page and asserts that the published body is the placeholder, not an empty body.

A page body is the text between its marker line and the next marker, with exactly one leading newline and, when a next marker follows, exactly one `\n\n` separator removed. Do not reuse a splitter that strips every body and rebuilds every marker: an untouched page published by the existing source-review workflow deliberately keeps its reviewed leading or trailing whitespace. The merge is deterministic, byte-preserving for untouched pages, and uses no fuzzy matching.

## 9. Binding to the loaded scenario

The repair applies to the PDF-derived scenario the conversation has loaded (`scenario_library_id`). It is rejected when:

- no scenario is loaded, or the loaded scenario has no PDF source (a Markdown-only scenario has no physical page identity);
- `target.title` does not match the loaded scenario's title (compared exactly as the load message shows it, apart from letter case, runs of whitespace collapsed to one space, and the suffixes the library adds to derived versions such as `[page repaired]` and `[source reviewed]`, so a repair file keeps working against a repaired child; punctuation is significant, so `Scenario: Alpha` and `Scenario Alpha` are different titles and nothing is matched fuzzily);
- `target.page_count` differs from the PDF's page count;
- a patch's `page` is outside the PDF.

The parent's identity (scenario id, content hash, PDF SHA) is captured by the server when the upload is accepted and re-checked before publication and again before activation, so a source that changed in between is never half-applied.

### 9.1 Overlap with earlier repairs

The file carries no baseline hash, so an older repair file can be uploaded again after a newer repair edited the same page: repair A and then repair B both change page 10, and uploading A while B is loaded would put A's page 10 back. A new correction of an already repaired page looks exactly the same as a stale file, so the check does not reject it; it makes the overwrite visible instead. The bot walks the loaded scenario's lineage up the universal `source_review.parent_scenario_id` chain (every derived child has it, repair or full review, so a full-review node between two repairs is passed through and does not end the walk), and reads `source_repair.pages` only on nodes whose own audit kind is `page_repair` and whose `source_repair.scenario_id` equals their id, and when a patched page was changed by an earlier repair in that lineage the reply names the page and the earlier repair: `第 10 頁先前已被另一份修復改過（<scenario id>），這次的內容覆蓋了它`. The audit records the same. Putting the earlier text back is a deliberate action with the same notice: upload the earlier file again, and the idempotent check above recognises a file that is already applied.

## 10. Numeric and mechanics report

Source repair is exactly where OCR-corrupted mechanics can enter the canonical source, so every changed number is surfaced instead of trusted. For each replaced page the bot computes

```python
old = scenario_numbers.mechanics_contexts(old_page)   # ordered list of (context, token)
new = scenario_numbers.mechanics_contexts(new_page)

removed, added = ordered_diff(old, new)               # difflib opcodes over the two sequences
```

and lists them in the audit and in a **private report**, for example `第 10 頁：移除 damage 1d40、18；新增 damage 1d4`. A page whose numbers and contexts are unchanged says so.

The report echoes the old source: removed tokens with their context are the numbers and labels of the original page. Because anyone may upload (section 6), putting it in the conversation reply would let a player submit a valid patch of arbitrary text and read back Keeper-only values such as monster HP, SAN losses and damage rolls. So the detailed report is sent by direct message (`send_dm`) to the KP Assistant only, and the reply in the conversation is a non-sensitive acknowledgement: the repaired page numbers, the new version id, how many of those pages had number changes, and that the KP has the detail. When no KP Assistant is registered the detail stays in the audit only and the reply says so. Delivery cannot be assumed: `delivery.send_dm()` raises when the KP has DMs closed. The report is therefore written to the audit first with delivery state `pending`, then sent; the repair itself is never blocked or rolled back by a failed DM, but the reply says that the private report could not be delivered and asks the KP to open their DMs and have the same file uploaded again. Uploading the same file reaches the already-applied answer, which first reads the audit of the lineage entry whose `patches_digest` equals the file's (the loaded child's own audit for D2, an ancestor's for F): a stored report that is not `delivered` is reserved, sent again and then marked delivered, and only after that does the reply say the repair is already applied. The audit file belongs to `trusted_scenario_source`, so the resend goes through five public functions added there in phase 1 and never touches `source_review.json` directly: `read_audit(scenario_id)`, `reserve_report(scenario_id, patches_digest, token, lease_seconds, recipient_id)`, `advance_report(scenario_id, patches_digest, token, pages_sent, recipient_id)`, `mark_report_delivered(scenario_id, patches_digest, token)` and `release_report(scenario_id, patches_digest, token)`. **The report is sent in pages, never as one DM.** `delivery.send_dm()` splits text with `_chunk_text()`, which keeps at most ten 1,900-character chunks and silently drops the rest, so a long report would be cut and still look delivered. The service therefore splits the report itself into pages of at most 1,800 characters (a numbered header `(i/N)` on each, split on line boundaries and never inside a token). Every report line is bounded so that this always works: each token and each context word is shown cut to 40 characters with a trailing `…` (the audit keeps them whole), so one entry is at most a few hundred characters, and as a last guard any line that is still longer than 1,700 characters is hard-wrapped at that length and marked `↩`, so no report can be unsendable and stay `pending` forever and sends each page with its own `send_private` call, which therefore always fits one chunk; there is no cap on the number of pages. Before sending each page the sender calls `advance_report(..., pages_sent)` with the number of pages already sent. In one step under the lock it checks that the caller's token still holds the reservation, **renews the lease** (new expiry = now + `lease_seconds`) and stores the count; if the token was lost it returns false and the sender stops before sending anything. `lease_seconds` is at least twice the Discord request timeout, so a single send cannot outlive the lease it was started under, and a long report keeps its reservation page by page instead of being reservable by a concurrent re-upload after a fixed time. The same call is made again **after each successful send** with the count increased by one (this second call also renews the lease), so after the final page the stored count equals the page count before `mark_report_delivered` runs; the pre-send call only renews and checks ownership. The call therefore stores how many pages went out, so after a failure or a crash the next reservation resumes at the first unsent page instead of repeating the whole report, and `delivered` is set only when `pages_sent` equals the page count. The delivery state also stores the `recipient_id` of the KP it is being delivered to, because progress is only meaningful for one recipient: `reserve_report` is given the current KP Assistant's id, and if the stored recipient differs (`/coc kp transfer` made another user the KP between attempts) it resets `pages_sent` to 0 and records the new recipient, so the new KP receives the complete report and never only its tail; `advance_report` takes the current recipient id too and returns false when it no longer matches, so a transfer in the middle of a multi-page send stops the old sender before its next page and the new reservation starts again from page 1. The delivery state of a report is `pending`, `sending` (with the reserving `token` and a lease expiry) or `delivered`. `reserve_report` runs under `scenario_library.publication_lock()` and moves `pending`, or a `sending` whose lease has expired, to `sending` for the caller's token; it returns false when another caller holds a live lease, and **the report is sent only by the caller whose reservation succeeded**, so two concurrent re-uploads send it once and the loser answers that delivery is in progress. After `send_dm()` succeeds the sender calls `mark_report_delivered` (only for its own token); if the send fails it calls `release_report` so the next re-upload can retry at once; if the process dies between sending and marking, the lease expires and the report may be sent a second time (at-least-once only across a crash, which is accepted for a private notice). Every write is a temporary file and a rename. The lineage walk uses the library's public `source_manifest()`. A test makes the first DM fail, then uploads the file again and checks that the report arrives once; an overlong-line test feeds a single mechanics token and a single context word of several thousand characters and checks that the report is cut, paginated within the limit and delivered; an alias test sets `CHARACTER_DISPLAY_ALIASES` to an expanding alias, sends a page containing it and checks that exactly one message goes out and none is cut; a transfer test delivers some pages to KP A, runs `/coc kp transfer` to KP B, retries, and checks that B receives every page from page 1 and that A's earlier pages do not count; an oversized-report test builds a report of more than 19,000 characters, fails the send midway, re-uploads and checks that every page arrives exactly once and in order and that the state becomes `delivered` only after the last page; a concurrency test starts two re-uploads together and asserts that only one reservation succeeds and the DM is sent once; a third test makes repair A's DM fail, applies a non-overlapping repair B, uploads A again and checks that A's report is delivered once from A's own audit entry and not B's. The refusal messages are written the same way: they name the problem and never quote the page text. Nothing is rejected for a numeric change: the file is the reviewer's correction, and the report is what lets the Keeper see that a `1D4` became a `1D6` before the next session. A wrong repair is corrected by uploading a corrected repair file, which applies with the same correction semantics; the parent stays in the library for provenance and for deliberately starting a fresh game, but selecting it from the library (`/coc scenario use`) starts a new timeline and is not an undo.

`mechanics_counts` is a stricter tokenizer than `scenario_numbers.counts`, which discards standalone signs and separators (`counts("Bonus +10%") == counts("Bonus -10%")`, and `SAN 1/1d6` and `SAN 1 1d6` have the same token counts). A mechanics token keeps an optional sign (`+`, `-`, `−`) written directly before the number, **including when it is glued to a word** (`STR+10` and `STR-10` are the tokens `+10` and `-10`), and joins numeric operands separated by `/`, `-`, `–` or `−` into one token (`1/1d6`, `1-3`). Spaces and tabs inside a token are not significant. A hyphenated label such as `A-10` yields the token `-10`; this only matters when that text changes, because identical text produces no change. Page-wide token counts cannot see values that move between mechanics (`HP 10, SAN 40` → `HP 40, SAN 10` has the same tokens), so each token is paired with its context: the up to two words (letters or CJK characters) immediately before it on the same line, casefolded. `HP 10, SAN 40` is the pairs (`hp`, `10`) and (`san`, `40`). A multiset would still miss the same label repeated, as in a stat block (`Rat / HP 10`, `Ogre / HP 20` with the two values swapped), so the pairs are kept **in order of appearance** and the report is an ordered diff of the two sequences (`difflib.SequenceMatcher` opcodes): everything inside a replaced, deleted or inserted block is listed as removed and added, with its context. Swapped values change the sequence and are reported. A page whose numbers only moved (a two-column reading-order repair) is reported as changed too, which is deliberate: the Keeper sees that those numbers moved and can check that they still sit next to the right labels. The existing `counts` is left unchanged for its other users.

The report proves only that the changed tokens are known, not that the change is right; the external review remains the evidence.

## 11. Validation phases

Any failure aborts the whole repair and nothing is published.

- **A, envelope:** file name, UTF-8, schema version, exact keys, patch count.
- **B, target:** a PDF-derived scenario is loaded, and `page_count` matches.
- **C, pages:** page range, uniqueness, no injected page markers.
- **D, contents:** non-empty `review_note`; `text` rules for the page kind; the `image` rule against native text.
- **D2, already applied:** if the loaded scenario is itself a repair child (its manifest has a `source_repair` **whose own `scenario_id` equals the loaded scenario's id**; the CLI source-review workflow deep-copies the parent manifest, so a full-review descendant can carry a stale copy of the repair's `source_repair`, which is therefore removed by `scenario_source_review.publish()` in phase 1, together with the copied `artifacts` marker and `artifacts_generation` pointer (the full-review publisher writes root-level index and pregen files and no generation directory of its own, so such a child reads as generation `legacy` and a reader never follows a pointer to a generation that does not exist), and the stale `source_repair` is and ignored by id mismatch for descendants published before), rebuild the candidate for these patches against that child's recorded parent and compare its digest with the child's `source_repair.candidate_digest`. A match means this same file was the last repair applied, so reply `這份修復已經套用，沒有重複建立版本。` and stop: nothing new is published (the only extra work is the report resend above and the `inherited` artifact retry of phase 2 in the implementation phases). No match means the file differs from the last repair and continues against the loaded scenario. A file that was applied earlier in the lineage and is uploaded again after a later, non-overlapping repair does not match here either, but its candidate is then identical to the loaded scenario, and phase F answers that case the same way instead of treating it as an invalid repair.
- **E, candidate:** build the candidate by splicing only the listed pages.
- **F, invariants:** the physical markers are still `1..N` once each; untouched page bodies are byte-for-byte unchanged (raw bodies as in section 8); the number of replaced pages equals the number of patches; the candidate digest is deterministic. A candidate whose text is identical to the loaded scenario is not an error, and it is a no-op **only when the parse-quality metadata is also already in place**: every requested page already has exactly the proposed text *and* its parse-quality row already records `operator-reviewed-discord` with the same `selected_sha256` and `page_kind` (section 13). A repair that changes only the metadata (for example a low-text page whose labels were extracted correctly, submitted as `page_kind: map` to clear its warning) is **published** as a metadata-only child: its text equals the parent's, its quality rows and `review_pages` follow section 13, its `candidate_digest` covers the metadata so it gets its own id, and the parent's derived artifacts are copied verbatim (nothing was rebuilt because no text changed; marker `inherited`, so activation installs no artifact). When both text and metadata are already in place, reply `這份修復已經套用，沒有重複建立版本。` when the loaded scenario is a repair lineage member and `這些頁面的內容已經與修復檔相同，沒有變動。` otherwise, and publish nothing. This is also what answers a file applied earlier in the lineage. For that answer the bot computes the file's `patches_digest` and walks the lineage up the chain through `source_review.parent_scenario_id` (written by `publish_derived()` for every derived child, repair or full review) to the entry with the same `patches_digest`, using `source_repair` only on nodes whose own audit kind is `page_repair` and whose `source_repair.scenario_id` equals their id (a full-review node in between is simply passed through, and its audit is never read for a repair report): that entry's own audit, not the loaded child's, is what the private-report resend below reads, so a report stranded by repair A survives a later non-overlapping repair B.

## 12. Publication

Never update the parent directory. Use the immutable derived-source pattern of `scenario_source_review.publish()` through `trusted_scenario_source.publish_derived`.

- Derived ID: `<parent-prefix>-repair-<candidate_digest[:16]>`. If it already exists and its audit digest matches, return it as an idempotent success; if it exists with different content, fail.
- `candidate_digest` covers the parent identity, the normalized patches (sorted by page) and the candidate text. `patches_digest` is the SHA-256 of the normalized patches alone (sorted by page, independent of any parent); it identifies "this repair file" across the lineage and is stored in `source_repair` and in the audit.
- The manifest gains `source_repair` (version, its own `scenario_id`, parent id and content hash, candidate digest, repaired pages, reviewer user id and display name, uploaded file name, time).
- The identity that `trusted_scenario_source.publish_derived()` verifies on an existing destination (`candidate_digest` and `parent_scenario_id`) is written where its verifier reads it, in the audit and in `manifest["source_review"]`, exactly as for a source-review child; `source_repair` carries the repair-specific detail above. Both are written, so the helper's idempotent `target.exists()` path returns the existing ID instead of rejecting the destination as changed, which is what makes an ordinary retry and the stale-activation recovery work.
- The audit is stored in the existing audit slot (`source_review.json`) with `kind: page_repair`: the conversation id and request id (both passed explicitly by the upload route into `publish`, never recovered from the observability context, which is empty when logging is off), parent and candidate digests, before and after content hashes, the PDF SHA, the reviewer, and per page the before and after SHA-256, the review note, the page kind and the removed and added numeric tokens. Full before and after texts are optional; the page hashes plus the immutable parent and child recover the diff.

## 13. Parse-quality update

Do not replace the parse-quality history with one clean result. Untouched pages keep their existing quality rows and warnings. A repaired page gets `method = "operator-reviewed-discord"`, `warnings = []`, `selected_sha256` of its new body and its `page_kind`. The top level becomes `version: "source-repair-v1"` with `parent_parse_quality_version`, `repaired_pages`, and `review_pages` reduced by the repaired pages. After repairing pages 2 and 4 of `2、4、6、7、8` the load message lists `6、7、8` only.

## 14. Derived artifacts

A repair changes page text and nothing else, so the child keeps what comes from the PDF's pictures and invalidates only what was extracted from the old text:

- **Invalidated:** `indexes` (NPC and location index) and `pregens`, which were extracted from the text; they are rebuilt below.
- **Copied from the parent unchanged:** `scene_maps` (the map topology comes from the page images, which did not change, and section 15 says a repair never edits it), the page image files with their original bytes and resolution, and the image-asset metadata: which pages were marked public handouts or maps, with their type and chapter classification. The one exception is each asset's `description` for a **repaired page**: `scenario_library` derives it from the page text and `search_images()` searches it, so it is regenerated from the corrected text, while visibility, type and chapter stay the parent's. Otherwise image lookup would keep matching removed terms and miss the corrected ones. The derivation is currently inside the private `_build_image_assets()`, which other modules must not call (private-name rule, and it would trip SLF001), so phase 1 gives `scenario_library` a public `image_asset_description(text, page)` that returns the description for one page (the same page-range extraction and 500-character cut `_build_image_assets()` uses, which is changed to call it so there is one definition) and the repair publication path calls only that for each repaired page; the type, visibility and chapter fields are never recomputed. A later `/coc scenario use <repaired-id>` therefore loads a child with working room navigation and the same readable maps.

`trusted_scenario_source.publish_derived()` is not enough for this: it re-renders every page at 110 DPI, replaces the image assets with `kp_only_image_assets` and writes empty `scene_maps`. The repair publication path copies the parent's `images/`, `image_assets` and `scene_maps` instead (a `reuse_parent_assets` mode of the helper, with the existing source-review callers unchanged).

For the running game, continuity matters: keep the current player characters, claimed pregens, HP, SAN, Luck, inventory, timeline, room positions and the **active chapter** (`active_chapter_id` and `context_chapter_ids`), and keep the runtime `scene_maps`. Mark the derived source artifacts for rebuild and rebuild them against the new source hash. The NPC and location index and the pregen pool are rebuilt **inside the upload flow and awaited**, before the activation lock is taken, so a normal first upload cannot reach activation while they are still `pending` and nothing depends on a retry that never comes; only the RAG prewarm may stay asynchronous. The rebuild is guarded in two layers. Committing the rebuilt artifacts to the **library entry** is guarded by the child's own manifest and source hash (and its `artifacts` marker moving from `pending` to `ready` as one compare-and-set), so it works on the first upload while the parent is still the active scenario and a slow rebuild for an old hash never overwrites a newer repair. Only **installing** the rebuilt artifacts into the running game's state requires the child to be the active source at that moment.

## 15. Map pages

For `page_kind: map` the bot may record that low text is expected and the visible labels were reviewed, and clear that page's text-quality warning. It never changes the room graph, adjacency, entry room, secret edges, `visual_basis` or map coordinates. A wrong topology uses the existing map repair path.

## 16. Applying to the running game

Add a lifecycle API instead of abusing `pending_pdf_upload`:

```python
async def activate_repair_version(
    conversation_id: str,
    parent_scenario_id: str,
    repaired_scenario_id: str,
    *,
    authorized: Callable[[GroupState], bool],
    expected_revision: int,
    expected_timeline: str,
) -> LifecycleResult:
```

Under the conversation lock: the actor is still authorized; the active `scenario_library_id` equals the parent; the active source hash still equals the parent's; `timeline_id` is unchanged; `state_revision` satisfies the repair transaction policy; there is no other pending scenario submission or pending pregen Luck decision; the child's `artifacts` marker is `ready` or `inherited` (`pending` is refused). `ready` installs the child's rebuilt NPC and location index and pregen pool into the running game. `inherited` installs **no** derived artifact: the game keeps its current runtime index and pregens exactly as they are (they may have been rebuilt separately and are the only ones known to be good), and only the source text and library id switch. A fail-soft empty extraction therefore never replaces anything in the running game; and `resource_bridge.guard_replacement(state)` permits replacement. Load the repaired child with the current active chapter id: `scenario_library.load_context()` defaults to the first chapter when none is given and `scenario_activation.install_context_fields()` would then overwrite both fields. The repair child keeps the parent's chapters, so the ids still resolve. Apply with correction semantics. Do **not** call `_new_upload()`, reset `game_started`, clear the timeline, wipe characters, reset rooms or clear campaign history. `scenario_library_id` and `scenario_text` change in one transaction, never one before the other.

**Artifact refresh of an already-active child.** The activation transaction above requires the active scenario to be the parent, so it cannot serve the later `inherited` → `ready` refresh (the child is by then the active scenario). That case has its own guarded transaction, `scenario_lifecycle.refresh_repair_artifacts()`, which changes **only** the runtime NPC and location index and pregen pool. It loads the child with the **currently active chapter id** (`state.active_chapter_id`, as the activation transaction does), because `scenario_library.load_context()` defaults an omitted chapter to the first playable chapter and would otherwise replace the running indexes with the first chapter's slice; a test refreshes a game that is on a later chapter and checks that the installed index is that chapter's. Under the conversation lock it requires: the actor is still authorized; the active `scenario_library_id` equals the child; the active source hash equals the child's content hash; `timeline_id` is unchanged from the value captured before the rebuild; `state_revision` satisfies the repair transaction policy; the child's marker is now `ready`; and `resource_bridge.guard_replacement(state)` permits it. It does not touch the scenario text, chapter, characters, claimed pregens, HP, SAN, Luck, inventory, timeline or rooms; pregens a player already claimed stay claimed and only the unclaimed pool is replaced. When any condition fails nothing is installed and the child stays `ready` in the library, so the next load or refresh uses it. A test makes the first refresh fail on a changed `state_revision` and checks that a later re-upload of the same file installs the child's generation and records it; a test re-uploads the file with a provider after an `inherited` activation and checks that the runtime index and pregen pool are replaced while state is untouched, and that a changed timeline or a different active scenario refuses the refresh.

## 17. Two-phase concurrency

Parsing, hashing, page checks and publication must not hold the conversation lock.

```text
LOCK: authorize, capture parent id/hash and revision/timeline, check no conflicting pending operation   UNLOCK
parse, validate, build candidate, publish the immutable derived scenario, await the index and pregen rebuild until `artifacts` is `ready` or `inherited` (either is activatable; `pending` is the only state that waits, and a rebuild always ends in one of the two)
LOCK: re-check authorization, parent id/hash, timeline, replacement guard; activate if still valid      UNLOCK
```

If the state changed after publication, the new version stays in the library and is not applied:

```text
新版已建立，但遊戲狀態在修復期間已變更，因此沒有自動套用。
請重新上傳同一份 repair 檔，會以更正的方式套用，不會重置遊戲。
```

The recovery is to re-upload the same file **while the original parent is still the loaded scenario**: the derived ID is deterministic, so the upload finds the already published version and only activates it with repair semantics. The stale notice is chosen by what changed:

- the revision advanced but the loaded scenario and its source hash are still the parent's: ask the Keeper to upload the same file again;
- the loaded scenario or its source hash changed (another upload, `/coc scenario use`, a different repair): do not advise a re-upload, because the file would then be checked against the newly loaded scenario by title and page count, and say instead that the new version stays in the library and was not applied to the current scenario.

The title check is what makes a mistaken upload against another scenario fail. Do not tell the Keeper to select the version from the library: `/coc scenario use` (`activate_existing_scenario()`) creates a new timeline and clears pending and resolved checks.

A valid published source is never rolled back or deleted just because activation became stale.

## 18. Result messages

Every refusal says what to do next and names the template when the file could not be read as a repair.

```text
✅ 劇本來源修復完成

《The Haunting Scenario trimmed》
修復頁面：2、4、6、7、8、10、14、16、17
新版來源：<new scenario id>

其中 2 頁的數值有變動，詳細內容已私訊給 KP。

已只替換指定頁面，其餘頁面保持不變。
目前遊戲已切換到修正版，角色、進度與目前位置沒有重置。
舊版仍保留在劇本庫。
```

Published but not applied: the version id plus the stale-state notice of section 17, chosen by what changed. A title or page-count mismatch says which loaded scenario the file expected and which is loaded. Idempotent re-upload: `這份修復已經套用，沒有重複建立版本。` A wrong page count, no PDF-derived scenario loaded, an invalid page, an `image` page that has readable text, or an unreadable file each get one specific message and apply nothing.

## 19. Free-form repair Markdown

Do not auto-import free-form files such as `# PDF page 2 ... # PDF page 4 ...`. They are useful reviewed material but carry no page kind, review note or page-count check. The template is the conversion path: copy the reviewed page texts into it.

## 20. Refactor of `scenario_source_review`

Avoid two subtly different validators. Extract the reusable public helpers: `split_source_pages`, `published_page_text`, `candidate_text` and `validate_evidence` (the review CLI keeps its evidence rectangles; page repair does not use them). Two deliberate differences: page repair splits the parent lossless (section 8) and reports numbers with the mechanics tokens (section 10). The stripping splitter and `scenario_numbers.counts` stay for their existing callers.

## 21. Files

New: `app/scenario_page_repair.py`, `app/services/scenario_repair.py`, `tests/test_scenario_page_repair.py`, `tests/test_discord_scenario_repair_upload.py`, `docs/references/scenario_page_repair_template(.md|_zh.md)`, and this spec. Modified: `app/commands/handlers/uploads.py`, `app/discord_bot.py`, `app/commands/router.py` (`handle_uploads()` takes and forwards `user_id`, the display name and `send_private`), `app/discord_transport/delivery.py`, `app/services/scenario_lifecycle.py`, `app/scenario_source_review.py`, `app/scenario_numbers.py`, `app/scenario_index.py`, `app/pregen_extractor.py`, `app/models.py` (the persisted `GroupState` field `installed_artifacts_generation`, with its serialization and default for older saves), `app/scenario_activation.py` (the field is set wherever artifacts are installed, so ordinary library selection through `activate_existing_scenario()` sets it too), `app/scenario_library.py`, `app/trusted_scenario_source.py`, `app/help_registry.py`, the load confirmation in `app/services/scenario_ingestion.py`, and the reference and guide documents.

## 22. API

```python
@dataclass(frozen=True)
class RepairTarget:
    title: str
    page_count: int

@dataclass(frozen=True)
class PageRepair:
    page: int
    text: str
    page_kind: Literal["text", "map", "image"]
    review_note: str

@dataclass(frozen=True)
class RepairProposal:
    version: int
    target: RepairTarget
    patches: tuple[PageRepair, ...]

@dataclass(frozen=True)
class RepairCheck:
    ready: bool
    scenario_id: str
    candidate_digest: str
    candidate_text: str
    repaired_pages: tuple[int, ...]
    issues: tuple[RepairIssue, ...]
    changes: tuple[dict, ...]      # per page: hashes, kind, note, removed and added numeric tokens

parse_markdown_bytes(data: bytes) -> RepairProposal
check(proposal: RepairProposal, scenario_id: str) -> RepairCheck
publish(check: RepairCheck, *, reviewer_user_id, reviewer_display_name, uploaded_filename,
        conversation_id, request_id) -> str
```

## 23. Atomicity and idempotency

Source publication is all or nothing; a failed page publishes nothing. Activation is all or nothing under the state transaction; state never points at a partial candidate. The derived ID is deterministic from the candidate digest. Re-uploading the same file against the same parent returns the same ID and ensures activation is correct; if the derived version is already active it says so; against a newer parent the page-count and page checks apply as for any file, and a page whose text now already equals the repair is reported as unchanged.

## 24. Translation variants and RAG

A source repair changes the canonical source identity. Translated or template variants bound to the old source stay with the old scenario, are unavailable for the repaired one, and the Keeper is told when the active source had one; translations migrate later through `scenario_source_review.rebind()`. Never copy translated records by id. Any RAG cache keyed to the parent hash is stale and is never authoritative for the child; schedule a rebuild for the new hash, use safe fallback retrieval from the new canonical text meanwhile, and do not block play on a rebuild.

## 25. Performance

A 9-page patch reruns none of PaddleOCR, Tesseract, PyMuPDF4LLM extraction or AI PDF repair. Upload latency is parse, hash, page check, numeric report, derived publication and state commit, normally a few seconds, plus the awaited NPC and location index and pregen rebuild, which is a model call and can take longer than the rest; only the RAG prewarm runs asynchronously afterwards.

## 26. Security and trust boundaries

Treat the file as untrusted input: reject path traversal and file paths, embedded page markers, unsupported keys or versions, huge payloads, duplicate pages, invalid UTF-8 and wrong page counts. Never execute Markdown, never reference other local files, never use file-supplied reviewer identity, and never let a repair edit the parent.

## 27. Tests

- **Parser:** a valid one-page repair; BOM accepted; unknown top-level, target or patch keys; duplicate or zero or out-of-range pages; marker injection; invalid `page_kind`; missing review note; an unfilled template is rejected.
- **Binding:** the parser keeps `target.title` and the check compares it, so a wrong title is rejected in the parser and binding tests; no scenario loaded; Markdown-only scenario; wrong page count; a different scenario with the same page count is rejected by its title; the title still matches a repaired child.
- **Numeric report (private):** the detailed report is sent by direct message to the KP Assistant only and the conversation reply carries no removed or added token; with no KP Assistant registered it stays in the audit; a player's patch of arbitrary text cannot read back Keeper-only numbers from any reply; swapped values (`HP 10, SAN 40` → `HP 40, SAN 10`) and swapped repeated labels (`Rat / HP 10; Ogre / HP 20`) are reported; a `1D40 → 1D4` change is reported with removed and added tokens; `+10% → -10%`, `SAN 1/1d6 → SAN 1 1d6` and `STR+10 → STR-10` are reported; unchanged text reports nothing; an unchanged hyphenated label causes no change; one page's report does not affect another.
- **Overlap:** repair A then repair B on the same page, then A uploaded again: the reply names page 10 and repair B as overwritten, and the audit records it; a page no earlier repair touched has no such notice.
- **Lineage:** the overlap notice still names an earlier repair when a full-review node sits between the two repairs; a full source-review child published from a repair child carries no `source_repair`, no `artifacts` marker and no `artifacts_generation` pointer, and selecting it loads its own root-level files without error (and a copy published earlier is ignored by id mismatch), re-uploading the repair against it is not answered as already applied when the full review changed those pages, and the report resend skips the full-review node's audit and reads the repair's own;
- **Merge:** a metadata-only repair (same text, `page_kind: map` clearing a low-text warning) publishes a child with the updated quality rows and copied artifacts, while a file whose text and quality rows are both already in place publishes nothing; a file applied earlier in the lineage and uploaded again after a later non-overlapping repair is answered as already applied, not rejected as a no-op; only the listed pages change; untouched pages keep their exact bytes including whitespace; markers stay ordered once each; patch order does not change the result; a repair whose pages already hold the proposed text and quality rows is not an error: it gets the phase F answer (`這份修復已經套用，沒有重複建立版本。` in a repair lineage, `這些頁面的內容已經與修復檔相同，沒有變動。` otherwise) and a test asserts that nothing is published and no audit record or DM is created; the same repair is idempotent: uploading the same file again after it was applied to the loaded scenario gets the already-applied answer.
- **Map and image:** a map page with labels is accepted and clears its low-text warning; a map repair does not change the scene-map graph; `image` is rejected when native text exists and accepted when it does not.
- **Discord:** the uploader's display name, the conversation id and the request id reach `publish` and the audit from the router (logging off, the audit still has all three), with no call back into Discord; `repair_*.md` routes to the repair handler and never to compare; any user may upload by default and `SCENARIO_LIFECYCLE_KP_ONLY` restricts it; more than one repair attachment is rejected; a pending source replacement follows the admission policy; a state that changed after publication publishes but does not activate.
- **Lifecycle:** activation preserves the active chapter in a multi-chapter campaign; a stale activation is recovered by re-uploading the same file with repair semantics while the parent is still loaded, never by `/coc scenario use`, and no re-upload is advised after a scenario switch; activation preserves timeline, `game_started`, claimed PCs, HP/SAN/Luck/inventory and room positions; it never calls `_new_upload()`; the new source becomes active only after the whole transaction; the old scenario stays readable.
- **Parse quality:** repaired pages cleared, untouched pages kept, the load message lists only the remaining pages.
- **Artifacts:** a repaired page's image description is regenerated from the corrected text while its visibility, type and chapter stay the parent's, and image search finds the corrected terms and not the removed ones; the child keeps the parent's `scene_maps`, image bytes and image-asset metadata (a public handout stays public) and `/coc scenario use <child>` loads working maps; after a successful (`ready`) rebuild the child's indexes and pregens come from its own text and the parent's are not copied; in the `inherited` case the child's files are verbatim parent copies and nothing is installed into the running game; rebuild is keyed to the child hash; a stale rebuild cannot overwrite a newer repair.

## 28. Acceptance test: The Haunting

Base: `The_Haunting_Scenario_trimmed`, 27 physical PDF pages, warning pages 2, 4, 6, 7, 8, 10, 14, 16, 17. A reviewer fills the template for those nine pages from the PDF; the file is uploaded as `repair_the-haunting-trimmed_01.md`. Expected: the page count matches; only the nine pages differ in the 27-page candidate; a new immutable scenario id is created and the original is unchanged; the KP receives the numeric changes per page by direct message and the conversation reply carries none of them; the nine warning pages no longer appear in the parse-quality warning; the timeline, characters, state and room positions are unchanged; the new source hash gets a new RAG and index build; uploading the same file again creates no other version.

## 29. Template and help

A new upload format ships with its documentation, in the same pull request as the behavior it describes:

- **Template.** `docs/references/scenario_page_repair_template(.md|_zh.md)` is the authoring template, linked from `docs/README.md` and `docs/README_zh.md` under References, like `role_card_template`. It states the rules and carries the JSON skeleton. A test keeps its keys identical to the parser's accepted keys so it cannot drift, and checks that an unfilled template is rejected.
- **Help.** The conversation help (`app/help_registry.py`, rendered by `help_service` and the Help UI) gets an entry for uploading `repair_*.md`, shown when a scenario is loaded: anyone may upload, what the file contains, and that the KP receives the number changes privately. `docs/references/player_command_reference(.md|_zh.md)` and `docs/guides/gameplay(.md|_zh.md)` get matching lines, and the load confirmation that lists parse-quality warning pages points at the repair flow and the template.
- **Messages.** Every refusal says what to do next and names the template when the file could not be read.

## Delivery

Each implementation phase below ships as its own pull request with its own review; the status stays `partial` until phase 4 lands. The existing `scenario_source_review.publish` requires a proposal that covers every page and writes a fully clean parse-quality record, so phase 1 extracts the shared page logic and adds a partial-page publication path rather than reusing `publish` unchanged.

## 30. Implementation order

The order makes sure the game can never be switched to a child whose derived artifacts are missing: the rebuild lands before activation, and until activation lands the upload only publishes.

1. **Deterministic core.** `trusted_scenario_source.read_audit()`, `reserve_report()`, `advance_report()`, `mark_report_delivered()` and `release_report()` (all compare-and-set under the publication lock) are added first. Extract the shared page helpers from `scenario_source_review`; implement the parser, strict schema, lossless merge, numeric report, candidate digest, the public `scenario_library.image_asset_description()` and immutable partial-page publication that copies the parent's images, image assets and scene maps, with unit tests and the template. The published child records `artifacts: "pending"` in its manifest.
2. **Derived artifact rebuild.** `scenario_library` first gets a public API for the library entry's derived artifacts, so the repair modules never reach into the library layout: `read_derived_artifacts(scenario_id)` (the unfiltered `indexes` and `pregens` files and the `artifacts` marker), and one writer, `commit_derived_artifacts(scenario_id, source_hash, indexes, pregens, marker)`. There is no separate copy operation: the `inherited` fallback reads the parent's files with `read_derived_artifacts(parent_id)` and commits them to the child through this same writer with marker `inherited`. The writer is a compare-and-set that applies only when the entry's content hash equals `source_hash` and the transition is allowed: `pending → ready`, `pending → inherited`, `inherited → ready`, and `inherited → inherited` (an idempotent no-op that writes nothing). A child that is already `ready` is never replaced, so a failed concurrent rebuild cannot overwrite a successful one. The artifact set is published as a **generation**, so a crash can never leave a mixed set: `commit_derived_artifacts()` writes the new `indexes.json` and `pregens.json` into a fresh staging directory `derived/<generation_id>/` first, and only then switches **one** pointer, the manifest's `artifacts_generation` together with the `artifacts` marker, by a single atomic rename of the manifest. Readers load the indexes and pregens through that pointer, so before the swap they see the old generation whole and after it the new one whole; if the process dies before the swap the new directory is an orphan that the next commit or a startup sweep removes, and if it dies after, the swap is already complete. Entries published before this feature have no `artifacts_generation` and read the existing files as generation `legacy`, unchanged. The commit runs under `publication_lock()`, and the readers take the same lock so that a live reader also never sees the swap half applied: `scenario_library.load_context()` (and `read_derived_artifacts()`) read the manifest, the indexes and the pregens inside it, so a concurrent `/coc scenario use <child>` sees either the old artifact set or the new one, never a new index with old pregens. Only those small JSON reads run inside the lock. A test selects the child while an `inherited → ready` commit is in flight and checks that the loaded index and pregens come from the same generation; a crash test kills the commit after the new files are staged and again after only some of them are renamed (simulated by raising between steps), restarts, and checks that the child loads the old generation whole and that the orphan directory is swept. The rebuild needs to know whether extraction **succeeded**, which the extractors do not report today (`extract_scenario_index()` returns the same empty lists for no provider, a failed call and a genuinely empty result, and `extract_pregens()` returns `[]` likewise), so `scenario_index` and `pregen_extractor` each gain a status-returning variant, `extract_scenario_index_with_status()` and `extract_pregens_with_status()`, returning `(result, status)` with status `ok`, `unavailable` (no provider or empty text) or `failed` (the call failed or returned nothing usable); the existing functions become thin wrappers that return only the result, so their callers are unchanged. The rebuild counts as successful only when both statuses are `ok`. Build the child's NPC and location index and pregen pool from its new text with the existing builders, against the child's source hash, commit them with a hash check, and set `artifacts: "ready"`; RAG prewarm is scheduled separately. The existing builders fail soft: `scenario_index.extract_scenario_index()` returns empty lists and `pregen_extractor.extract_pregens()` an empty pool when no analysis provider is configured or the call fails. The rebuild therefore reports whether extraction **succeeded** and treats an empty result from a failed or unavailable provider as not built: the child's library files are then verbatim copies of the parent's index and pregen files (whatever they hold: the parent's files are not assumed to be non-empty, and nothing is claimed about them), and its marker is `inherited`, not `ready`. `inherited` is activatable, but activation installs no derived artifact into the running game (section 16), so the game keeps its current runtime index and pregens. The reply says the indexes were not rebuilt. The retry is the **re-upload of the same file**: the already-applied branch (phases D2 and F) first reads the child's marker, and when it is `inherited` and an analysis provider is now available it runs the rebuild again, compare-and-sets the child's files and marker from `inherited` to `ready` against the child's source hash, and, if the child is the loaded scenario, installs the rebuilt artifacts into the running game through the artifact-only `scenario_lifecycle.refresh_repair_artifacts()` transaction of section 16 (authorization, active child and hash, timeline, revision and replacement guard re-checked); the reply says the indexes were refreshed. Without a provider the answer is the usual already-applied one plus a note that the indexes are still not rebuilt. The refresh is also retried when the child is already `ready` but the running game never received it (the awaited rebuild finished while an ordinary turn advanced `state_revision`, so `refresh_repair_artifacts()` refused): the game state records the `artifacts_generation` it last installed (set by repair activation, by the refresh and by an ordinary library selection), and the already-applied path compares it with the active child's `artifacts_generation`; when they differ and the child is `ready` it calls `refresh_repair_artifacts()` again, so a later upload of the same file always converges and nothing depends on a marker transition. An extraction that succeeded and is genuinely empty is `ready`. A child with `artifacts: "pending"` is not selectable for activation. The `inherited` guarantee belongs to repair activation. An ordinary library selection of an `inherited` child (`/coc scenario use <child>`) starts a **new** game through `activate_existing_scenario()` and installs the child's library files, which are verbatim copies of the parent's, so it behaves exactly as selecting the parent does and replaces no running game's state; no extra guard is added, and a test selects an `inherited` child and checks that it loads the same indexes and pregens as its parent. Tests: the rebuild fills the indexes and pregens while the parent is still the active scenario and marks the child `ready`; with no provider the child is `inherited`, its files are verbatim copies of the parent's and activating it leaves the running game's index and pregens untouched even when the parent's files are empty; re-uploading the same file once a provider is available rebuilds, moves the marker to `ready` and installs the rebuilt artifacts into the running game, and a re-upload with no provider changes nothing; a stale rebuild cannot overwrite a newer repair; installing into the running game requires the child to be active; and a pending child is refused.
3. **Discord upload.** `repair_*.md` routing, `user_id` in `handle_uploads`, the scenario-lifecycle permission check, the repair service, the rebuild from phase 2, result and refusal messages, routing tests, help entries and guide lines. Until phase 4 the reply says the new version was created and not yet applied to the running game, so the upload never changes the game.
4. **Active-game correction.** `scenario_lifecycle.refresh_repair_artifacts()` and `scenario_lifecycle.activate_repair_version()` requiring `artifacts` to be `ready` or `inherited` (installing derived artifacts only for `ready`), correction semantics, the stale revision and timeline checks, preservation of map position, active chapter and player state, integration tests, and the result messages that say the game switched.

## 31. Merge criteria

- a repair cannot be applied to a scenario whose page count differs, to a Markdown-only scenario, or when none is loaded;
- full-page replacement is deterministic and untouched pages keep their exact bytes;
- every changed number is recorded in the audit, which is authoritative; delivery to the KP by direct message is attempted and retried on re-upload, and when no KP Assistant is registered or DMs are closed the repair still publishes and the audit keeps the report; none of the numbers appears in the conversation reply;
- the parent scenario is immutable and a partial repair cannot publish;
- active-game correction does not reset campaign state;
- untouched page warnings remain visible;
- `repair_*.md` never routes to the generic compare;
- the upload check follows the scenario-lifecycle policy;
- idempotent re-upload is proven by tests;
- no OCR or API call is needed for the page merge;
- existing `scenario_source_review` tests stay green and existing PDF and Markdown upload behavior is unchanged;
- the template, help entries and guide lines ship with the behavior they describe.

## 32. Explicit design choices

- **Why not accept any free-form repair file?** It would carry no page kind, review note or page-count check.
- **Why bind to the loaded scenario and not to hashes?** The file is written by an external reviewer who has the PDF and the template but not the bot's internal hashes. The loaded scenario, the page count and the privately reported number changes give the safety that matters without asking the Keeper to copy anything; the immutable parent makes a wrong repair reversible.
- **Why replace the whole page?** It gives deterministic provenance and avoids fuzzy alignment.
- **Why report numbers instead of rejecting them?** Source repair is allowed to fix numbers, and a correction is by definition a numeric change; what must not happen is a change nobody notices. The report is a visibility tool, not a proof of correctness.
- **Why a child scenario and not an overwrite?** Repairs must be reversible, auditable and safe for existing campaigns.
- **Why keep the game state?** This fixes the source; it is not a new scenario.

## 33. Expected Keeper experience

```text
Bot:
⚠️ 第 2、4、6、7、8、10、14、16、17 頁需要核對。

Keeper:
(fills docs/references/scenario_page_repair_template.md with ChatGPT from the original PDF)
[uploads repair_the-haunting_01.md]

Bot:
✅ 修復完成。
已核對並替換第 2、4、6、7、8、10、14、16、17 頁。
其中 1 頁的數值有變動，詳細內容已私訊給 KP。
建立新版劇本來源：the-haunting-...-repair-xxxxxxxxxxxxxxxx
目前遊戲已使用修正版；角色、進度與位置未重置。
```
