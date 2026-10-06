# Page-level scenario repair from a Discord upload

[繁體中文](discord_page_level_scenario_repair_design_spec_zh.md)

Status: **backlog** (design only; nothing is implemented). Base: `main_v2` at `3fbec39`.

## 1. Problem

Current scenario ingestion has two distinct paths:

1. PDF upload:
   - parses the whole PDF;
   - creates a new scenario library entry;
   - may be applied as a new scenario or as a correction.

2. `scenario*.md` upload:
   - treats the complete Markdown file as the complete authoritative scenario source;
   - does **not** patch selected pages into the current PDF scenario.

There is already a safe administrator workflow in `app.scenario_source_review`:

```text
prepare → edit proposal.md → check → publish
```

That workflow correctly:

- binds review work to a specific immutable PDF/source snapshot;
- validates physical PDF page numbers;
- checks evidence regions;
- reports numeric-token changes;
- creates a derived scenario instead of overwriting the original;
- records audit metadata;
- invalidates derived artifacts that may now be stale.

However, it currently requires filesystem/CLI access and a full-page proposal covering the entire PDF. It is not suitable for the normal Discord workflow where the Keeper sees:

```text
第 2、4、6、7、8、10、14、16、17 頁有解析品質待核對項目
```

and wants to correct only those pages with an externally reviewed Markdown file.

### Desired UX

The Keeper should be able to:

```text
1. Export a repair workfile for selected warning pages.
2. Give that file + original PDF to ChatGPT / another reviewer.
3. Receive repair_<scenario>_01.md.
4. Upload repair_<scenario>_01.md directly to Discord.
5. Bot validates the repair deterministically.
6. Bot replaces only the specified physical pages.
7. Bot publishes an immutable derived scenario version.
8. If the repaired source is still the active source, Bot applies it as a correction
   while preserving the current game state.
```

No PDF OCR rerun should be required.

## 2. Goals

### 2.1 Functional goals

The implementation MUST:

1. Recognize Markdown attachments whose filename begins with `repair_`.
2. Treat the file as a **page patch**, never as a complete scenario.
3. Bind every repair to one exact scenario source version.
4. Replace only explicitly listed physical PDF pages.
5. Require the full corrected text for every replaced page.
6. Reject stale repair files.
7. Detect unexpected numeric/dice changes deterministically.
8. Reuse the original PDF bytes and rendered page evidence.
9. Create a new immutable scenario library entry.
10. Never overwrite the parent scenario.
11. Record parent/child provenance and a repair audit.
12. Clear parse warnings only for pages that were explicitly reviewed.
13. Preserve unresolved warnings on untouched pages.
14. Preserve the running game when applying a repair to the active scenario.
15. Avoid rebuilding the original PDF OCR pipeline.
16. Be idempotent: re-uploading the same valid repair must not create endless duplicate versions.
17. Remain compatible with the existing `scenario_source_review` CLI workflow.

### 2.2 UX goals

The normal Keeper workflow should not require shell access.

The preferred path is:

```text
/coc repair export warnings
        ↓
repair_<scenario>_<id>.md
        ↓
external review
        ↓
upload repair_*.md to Discord
        ↓
validation
        ↓
published + applied
```

A specific-page export should also be supported:

```text
/coc repair export 2,4,6,7,8,10,14,16,17
```

## 3. Non-goals

Version 1 MUST NOT:

- accept arbitrary unified diffs;
- accept line-number patches;
- fuzzy-match replacement text;
- infer which active scenario the repair “probably” belongs to;
- mutate the parent scenario in place;
- change the original PDF;
- replace arbitrary map graph topology through the repair Markdown;
- treat a free-form Markdown document as a valid repair;
- silently accept missing numeric changes;
- automatically rewrite translation variants;
- rerun OCR over the whole PDF;
- allow ordinary players to alter a published scenario source.

Map topology repair remains owned by the existing map pipeline / `map_*.yaml` path unless a later spec explicitly merges the two systems.

## 4. Existing architecture to reuse

The implementation should reuse rather than duplicate the safety properties already present in:

- `app/scenario_source_review.py`
- `app/scenario_library.py`
- `app/trusted_scenario_source.py`
- `app/services/scenario_lifecycle.py`
- `app/services/scenario_ingestion.py`
- `app/commands/handlers/uploads.py`
- `app/scenario_numbers.py`

Important existing behavior:

### `scenario_source_review`

Already provides the correct conceptual source-repair model:

- physical-page source splitting;
- immutable source binding;
- full-page reviewed text;
- evidence bounding-box validation;
- numeric-token before/after accounting;
- candidate digest;
- immutable derived publication;
- parent scenario provenance.

### `scenario_lifecycle._repair`

Already defines the desired running-game semantics for a source correction:

- update scenario title/text/index references;
- do not create a new game timeline;
- do not clear ordinary game progress as `_new_upload()` does.

The new feature should not invent a second concept of “repair”.

## 5. Architectural decision

Add a dedicated module:

```text
app/scenario_page_repair.py
```

and a service adapter:

```text
app/services/scenario_repair.py
```

Responsibilities:

```text
Discord upload router
        ↓
scenario_repair.handle_repair_upload()
        ↓
scenario_page_repair.parse()
        ↓
scenario_page_repair.validate()
        ↓
scenario_page_repair.publish()
        ↓
scenario_lifecycle.activate_repair_version()
```

Do not put repair parsing or publication logic directly in `discord_bot.py`.

## 6. Attachment routing

Modify:

```text
app/commands/handlers/uploads.py
```

Current order includes:

```text
PDF
scenario*.md
map_*.yaml
role_*.md
generic .txt/.md comparison
```

Add `repair_*.md` before the generic Markdown comparison path.

Recommended order:

```text
PDF
scenario*.md
repair_*.md
map_*.yaml
role_*.txt/.md
generic .txt/.md compare
```

Example:

```python
repairs = [
    u for u in uploads
    if u.filename.lower().startswith("repair_")
    and u.filename.lower().endswith(".md")
]
```

Rules:

- exactly one repair file per Discord message in v1;
- more than one → reject with a specific message;
- a repair file must never fall through to `handle_scenario_compare_upload()`.

## 7. Permission model

This differs intentionally from ordinary initial scenario upload.

A `repair_*.md` file changes the trusted source used by the active campaign. Therefore only the group's KP Assistant may submit it:

```python
permissions.is_kp(state, user_id)
```

Do **not** use `permissions.may_manage_scenario_lifecycle`: with the default `SCENARIO_LIFECYCLE_KP_ONLY=false` it is true for every user, which would let an ordinary player publish or activate a modified trusted source. The refusal uses `permissions.kp_only(...)`, like the existing source and template management handlers.

`handle_uploads()` currently does not receive the upload actor ID. Extend it:

```python
async def handle_uploads(
    conversation_id: str,
    user_id: str,
    uploads: list[Upload],
    reply: Reply,
    ...
) -> bool:
```

and update the caller in `discord_bot._handle_message()`.

Do not trust a `reviewer` field inside the uploaded file.

The authoritative reviewer identity is:

```text
Discord user ID
Discord display name, if available
conversation/channel identity
request ID
timestamp
```

## 8. Repair workfile format

Use Markdown containing exactly one JSON payload, compatible with the existing `authoring.parse_markdown()` conventions.

Top-level schema:

```json
{
  "repair_version": 1,
  "target": {
    "scenario_id": "the-haunting-scenario-trimmed-46c3c49c",
    "content_hash": "<exact parent source content hash>",
    "pdf_sha256": "<exact original PDF sha256>",
    "page_count": 27
  },
  "patches": []
}
```

Only these top-level keys are valid:

```text
repair_version
target
patches
```

Unknown top-level fields MUST be rejected.

## 9. Page patch schema

Each patch is a **complete replacement of one physical PDF page body**.

Example:

```json
{
  "page": 10,
  "base_page_sha256": "a33d...",
  "text": "THE BASEMENT\n\nROOM 1: Storage\n...",
  "page_kind": "text",
  "review_note": "Checked against PDF page 10; repaired two-column order and retained all dice expressions.",
  "evidence": [
    {
      "bbox": [0.0, 0.0, 504.0, 720.0],
      "note": "Full physical PDF page reviewed."
    }
  ],
  "expected_numeric_delta": {
    "removed": {},
    "added": {}
  }
}
```

Allowed page keys:

```text
page
base_page_sha256
text
page_kind
review_note
evidence
expected_numeric_delta
```

No additional page keys in v1.

## 10. `page` semantics

`page` is always the **physical PDF page number**, 1-based.

It is NOT:

- the printed book page number;
- the page number shown in the footer;
- a chapter-relative number.

Example:

```text
PDF physical page 7
printed page 23
```

Repair uses:

```json
"page": 7
```

This is identical to `scenario_source_review` behavior and prevents accidental replacement of the wrong page.

## 11. Full-page replacement, not fragment merge

A patch's `text` MUST contain the complete authoritative source text for that physical page.

Do not support:

```text
append this paragraph
replace lines 30–50
replace this sentence
search-and-replace
unified diff
```

Merge algorithm:

```python
spans = locate_page_body_spans(parent_text)      # lossless: offsets of each physical page body

for patch in validated_patches:
    splice(parent_text, spans[patch.page - 1], patch.text)   # only the selected spans are re-serialized
```

The split is **lossless**. A page body is the text between its marker line and the next marker, with exactly one leading newline and, when a next marker follows, exactly one `\n\n` separator removed; the base page hash (rule below) and the untouched-page invariant are over that raw body. Do not reuse a splitter that strips each body and rebuilds every marker: an untouched page published by the existing source-review workflow deliberately keeps its reviewed leading or trailing whitespace, and re-joining would change it.

This makes the result deterministic, byte-preserving for untouched pages, and avoids fuzzy matching.

## 12. Base binding and stale repair protection

A repair MUST bind to the exact source version it was prepared from.

Validate all of:

```text
target.scenario_id
target.content_hash
target.pdf_sha256
target.page_count
```

against the parent trusted snapshot.

For each page also validate:

```text
base_page_sha256
```

where:

```python
base_page_sha256 = sha256(current_published_page_body.encode("utf-8")).hexdigest()
```

A mismatch means the repair is stale.

Reject with:

```text
這份 repair 是針對較舊的劇本來源製作的，沒有套用。
請重新匯出 repair 工作檔後再修正。
```

Never “best effort” merge a stale repair.

## 13. Numeric/mechanics safety

This is a critical requirement.

The source repair feature is specifically allowed to fix OCR mistakes, including numeric mistakes. Therefore simply rejecting every numeric change is wrong.

Instead, every patch carries an explicit expected numeric delta:

```json
"expected_numeric_delta": {
  "removed": {
    "1d40": 1
  },
  "added": {
    "1d4": 1
  }
}
```

The bot computes:

```python
old_counts = scenario_numbers.mechanics_counts(old_text)
new_counts = scenario_numbers.mechanics_counts(new_text)

actual_removed = old_counts - new_counts
actual_added = new_counts - old_counts
```

The actual delta MUST exactly equal `expected_numeric_delta`.

Otherwise reject the entire repair atomically.

`mechanics_counts` is a stricter tokenizer than the existing `scenario_numbers.counts`, which discards standalone signs and separators (`counts("Bonus +10%") == counts("Bonus -10%")`, and `SAN 1/1d6` and `SAN 1 1d6` have the same token counts). A mechanics token keeps an optional leading sign (`+`, `-`, `−`) that is not glued to a preceding word character, and joins numeric operands that are separated by `/`, `-`, `–` or `−` into one token (`1/1d6`, `1-3`). Spaces and tabs inside a token are not significant, and `expected_numeric_delta` keys are these canonical tokens. The existing `counts` is left unchanged for its other users.

This catches accidental changes to:

- dice expressions;
- percentages;
- HP;
- SAN loss;
- skill values;
- dates;
- money;
- page references;
- stat blocks;
- durations;
- attack thresholds.

Important: matching numeric delta proves only that the declared changes match the file. It does **not** prove semantic correctness. The external review remains the evidence source.

## 14. `page_kind`

Supported values:

```text
text
map
image
```

### `text`

Normal scenario prose/rules/handouts.

Requirements:

- `text` must not be empty.

### `map`

A page whose primary content is a floor plan or diagram.

Requirements:

- transcribe readable labels;
- do not manufacture room descriptions that are not printed on the page;
- low text volume is not itself a parse failure;
- `page_kind=map` may resolve `low_text` style warnings for that page.

Example:

```text
Corbitt House Map (Keeper Version)
Upper Story
Ground Floor
Basement
Scale: 1/4 inch equals 3 feet.
```

Map graph topology itself is NOT edited by this file in v1.

### `image`

Use only when the source page genuinely contains no meaningful readable text.

If the PDF page contains native/readable labels or rules, `image` MUST be rejected, mirroring the current `scenario_source_review.image_only` safety rule.

## 15. Page markers

Replacement `text` MUST NOT contain:

```text
--- 第 N 頁 ---
```

or anything matched by:

```python
library.PAGE_MARKER_RE
```

The system owns physical page markers.

This prevents one patch from injecting or replacing adjacent pages.

## 16. Evidence

`evidence` uses the same physical PDF coordinate concept as `scenario_source_review`.

Each entry:

```json
{
  "bbox": [x0, y0, x1, y1],
  "note": "Full page verified against the rendered PDF."
}
```

Validation:

- one to 100 entries;
- finite numeric coordinates;
- rectangle entirely inside the real PDF page bounds;
- non-empty note.

For the default exported workfile, the Bot should populate one full-page rectangle automatically.

External reviewers can narrow it, but do not require that for normal use.

The evidence PNG does not need to be uploaded back to Discord: the server already owns the original PDF and can render the target page itself.

## 17. Export workflow

Add:

```text
/coc repair export warnings
```

and:

```text
/coc repair export 2,4,6,7,8,10,14,16,17
```

Both commands are KP-only (`permissions.is_kp`, refusal via `permissions.kp_only`) and deliver the workfile by direct message (`send_dm`) only, never in the shared channel: the file contains the scenario's full page text, which includes Keeper-only content. This is the same protection as the existing `/coc scenario source export` and template export handlers. The Help UI entry may be shown to everyone, but invoking it as anyone else is refused.

### `warnings`

Select the current scenario's unresolved parse-quality warning pages.

If there are no warning pages:

```text
目前這份劇本沒有需要人工修復的解析頁面。
```

### Explicit page list

Validate:

- integers only;
- 1 ≤ page ≤ page_count;
- unique;
- sorted before export.

### Exported file

Suggested filename:

```text
repair_the-haunting-scenario-trimmed_<short-hash>.md
```

The workfile should contain:

- repair instructions;
- exact target source identity;
- page number;
- current page text;
- base page SHA;
- full-page evidence bbox;
- current parse warnings;
- empty replacement/review fields to fill.

For external AI usability, it is acceptable for the export template to include `base_text` and `current_warnings`, but these fields must either:

1. be outside the import JSON payload; or
2. be stripped by a dedicated export/import schema.

Preferred implementation: keep the import JSON strict and place existing text in Markdown reference sections outside the JSON block.

## 18. Example repair file

````markdown
# Scenario page repair

This file replaces only the listed physical PDF pages.
Do not change target identity fields.

```json
{
  "repair_version": 1,
  "target": {
    "scenario_id": "the-haunting-scenario-trimmed-46c3c49c",
    "content_hash": "abc123...",
    "pdf_sha256": "def456...",
    "page_count": 27
  },
  "patches": [
    {
      "page": 6,
      "base_page_sha256": "111aaa...",
      "text": "LOCATION 9: THE OLD CORBITT PLACE\n\n...",
      "page_kind": "text",
      "review_note": "Checked against physical PDF page 6. Corrected the broken page reference from page @@ to page 33.",
      "evidence": [
        {
          "bbox": [0.0, 0.0, 504.0, 720.0],
          "note": "Full page checked against source image."
        }
      ],
      "expected_numeric_delta": {
        "removed": {},
        "added": {
          "33": 1
        }
      }
    },
    {
      "page": 7,
      "base_page_sha256": "222bbb...",
      "text": "Corbitt House Map (Keeper Version)\nUpper Story\nGround Floor\nBasement\nScale: 1/4 inch equals 3 feet.",
      "page_kind": "map",
      "review_note": "Verified as a floor-plan page. Low source text is intentional; visible labels were transcribed.",
      "evidence": [
        {
          "bbox": [0.0, 0.0, 504.0, 720.0],
          "note": "Full map reviewed."
        }
      ],
      "expected_numeric_delta": {
        "removed": {},
        "added": {}
      }
    }
  ]
}
```
````

## 19. Parsing rules

Add:

```python
parse_repair_markdown(data: bytes) -> RepairProposal
```

Requirements:

- UTF-8 or UTF-8 BOM;
- normalize CRLF to LF;
- enforce existing max authoring file size;
- exactly one JSON payload;
- no duplicate page numbers;
- strict field sets;
- max number of patches: recommended `100`;
- max replacement text per page: bounded by existing authoring file-size policy;
- no symlink/filesystem assumptions for Discord bytes.

Do not use a permissive YAML parser.

## 20. Validation phases

Validation must be separated into deterministic phases.

### Phase A — envelope

Validate:

- file name;
- UTF-8;
- schema version;
- exact keys;
- patch count.

### Phase B — target identity

Validate:

- scenario exists;
- scenario is PDF-derived;
- content hash matches;
- PDF SHA matches;
- page count matches.

A repair cannot target a Markdown-only scenario in v1 because there is no authoritative physical PDF page identity.

### Phase C — page identity

Validate:

- page range;
- uniqueness;
- base page SHA;
- no physical page marker injection.

### Phase D — evidence

Validate all rectangles against real PDF page bounds.

### Phase E — page contents

Validate:

- non-empty review note;
- page-kind requirements;
- image-page rules;
- replacement text requirements.

### Phase F — numeric delta

Compute and exactly compare numeric changes.

### Phase G — candidate construction

Build the complete candidate source by replacing only listed pages.

### Phase H — candidate invariants

Verify:

- physical page markers remain 1..N exactly once;
- untouched page bodies are byte-for-byte unchanged (raw bodies as defined in rule 11);
- patched page count equals requested patch count;
- candidate differs from parent;
- candidate digest is deterministic.

Any failure aborts the entire repair.

No partial publication.

## 21. Publication model

Never update the parent scenario directory.

Use the same immutable-derived-source pattern as `scenario_source_review.publish()`.

Suggested derived ID:

```text
<parent-prefix>-repair-<candidate_digest[:16]>
```

If the same derived ID already exists and its audit digest matches, return it as an idempotent success.

If it exists with different content, hard fail.

Suggested manifest addition:

```json
{
  "source_repair": {
    "version": 1,
    "parent_scenario_id": "...",
    "parent_content_hash": "...",
    "candidate_digest": "...",
    "pages": [2, 4, 6],
    "reviewer_user_id": "...",
    "reviewer_display_name": "...",
    "uploaded_filename": "repair_....md",
    "reviewed_at": "..."
  }
}
```

Do not rely on reviewer identity supplied by the repair file.

## 22. Repair audit

Store a private audit file alongside the derived scenario:

```text
source_repair_audit.json
```

Recommended structure:

```json
{
  "version": 1,
  "parent_scenario_id": "...",
  "source_hash_before": "...",
  "source_hash_after": "...",
  "pdf_sha256": "...",
  "candidate_digest": "...",
  "reviewer": {
    "discord_user_id": "...",
    "display_name": "..."
  },
  "uploaded_filename": "...",
  "pages": [
    {
      "page": 6,
      "before_sha256": "...",
      "after_sha256": "...",
      "review_note": "...",
      "page_kind": "text",
      "evidence": [],
      "numeric_removed": {},
      "numeric_added": {}
    }
  ]
}
```

Storing the full before/after text is optional if privacy/storage size is a concern; page hashes plus the immutable parent/child scenarios are sufficient to recover the diff.

## 23. Parse-quality update

Do not replace all parse-quality history with a single clean result.

For untouched pages:

```text
preserve existing page quality metadata and warnings
```

For repaired pages:

```text
method = "operator-reviewed-discord"
warnings = []
selected_sha256 = hash(replacement_text)
page_kind = text/map/image
```

Top-level:

```json
{
  "version": "source-repair-v1",
  "parent_parse_quality_version": "...",
  "repaired_pages": [2,4,6,...]
}
```

This makes the next load message accurate.

Example:

Before:

```text
⚠️ 第 2、4、6、7、8、10、14、16、17 頁有解析品質待核對項目
```

After repairing all nine:

```text
(no warning)
```

If only pages 2 and 4 were repaired:

```text
⚠️ 第 6、7、8、10、14、16、17 頁仍有解析品質待核對項目
```

Do not hide warnings on untouched pages.

## 24. Derived artifacts

Existing `scenario_source_review.publish()` deliberately invalidates:

```text
indexes
pregens
scene_maps
```

because source repair can invalidate data extracted from the old text.

The Discord feature should retain the same library safety property.

### Library version

The new derived scenario MUST NOT blindly copy old:

- NPC index;
- location index;
- pregen extraction;
- scene-map inference.

### Active runtime

When applying the repair to the currently running game, continuity matters.

Use repair semantics:

- preserve current player characters;
- preserve claimed pregens;
- preserve current HP/SAN/Luck/inventory;
- preserve the timeline;
- preserve current room positions where possible;
- preserve current runtime `scene_maps` in v1 because the underlying PDF image has not changed;
- mark derived source artifacts for rebuild.

Then asynchronously rebuild:

```text
NPC/location index
RAG/prewarm
pregen candidates, if required
```

against the new source hash.

The rebuild result may replace derived artifacts only if the repaired scenario is still the active source version when the rebuild finishes.

Never allow a slow background rebuild from an old source hash to overwrite a newer repair.

## 25. Map-page behavior

This feature must solve the false-warning problem without pretending it repaired map topology.

For:

```json
"page_kind": "map"
```

the Bot may record:

```text
low text is expected
visible labels reviewed
```

and clear the page's text-quality warning.

It MUST NOT change:

- room graph;
- adjacency;
- entry room;
- secret edges;
- visual_basis;
- existing map coordinates.

If map topology is wrong, use the existing map repair path.

A future v2 may add:

```text
logical_map_id
map_variant
paired_page
```

but these fields should not be accepted in v1.

## 26. Active-scenario application

Add a lifecycle API rather than abusing `pending_pdf_upload`:

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

Preconditions under conversation lock:

1. actor remains authorized;
2. active `scenario_library_id == parent_scenario_id`;
3. active source hash still equals repair target hash;
4. `timeline_id` is unchanged;
5. `state_revision` satisfies the repair transaction policy;
6. no other pending scenario submission;
7. no pending pregen Luck decision;
8. `resource_bridge.guard_replacement(state)` permits replacement.

Apply with correction semantics, not new-scenario semantics.

Do NOT call `_new_upload()`.

Do NOT:

- reset `game_started`;
- clear the timeline;
- wipe characters;
- reset current rooms;
- clear normal campaign history.

## 27. Two-phase concurrency model

External review validation can involve file parsing, PDF inspection, hashing, and artifact publication. Do not hold the conversation lock for the entire operation.

Use:

```text
LOCK
  authorize
  capture parent scenario ID/hash
  capture revision/timeline
  verify no conflicting pending operation
UNLOCK

parse + validate + construct candidate
publish immutable derived scenario

LOCK
  re-check authorization
  re-check parent scenario ID/hash
  re-check timeline
  re-check replacement guard
  activate as repair if still valid
UNLOCK
```

If state changed after publication:

```text
新版已建立，但遊戲狀態在修復期間已變更，因此沒有自動套用。
```

The immutable derived scenario may remain in the library.

Do not roll back or delete a valid published source merely because activation became stale.

## 28. Result messages

### Success: published and activated

```text
✅ 劇本來源修復完成

《The Haunting Scenario trimmed》
修復頁面：2、4、6、7、8、10、14、16、17
新版來源：<new scenario id>

已只替換指定頁面，其餘頁面保持不變。
數值／骰式差異已通過 repair 檔宣告比對。
目前遊戲已切換到修正版，角色、進度與目前位置沒有重置。

NPC／地點索引會依修正版來源重新整理。
```

### Success: published but activation became stale

```text
✅ 修正版已建立：<new scenario id>

⚠️ 上傳期間目前遊戲狀態已變更，因此沒有自動切換來源。
請由 KP 重新確認後選用這個版本。
```

### Stale repair

```text
❌ 沒有套用 repair。

這份檔案是依較舊的劇本來源製作：
expected content hash: ...
current content hash: ...

請重新匯出 repair 工作檔後再修正。
```

### Unexpected number change

```text
❌ 第 10 頁的數值差異與 repair 宣告不一致，整份修復沒有套用。

實際新增：1D4+2 × 1
repair 宣告：無

請重新核對原 PDF 後再上傳。
```

## 29. Existing free-form repair Markdown

Do NOT attempt to auto-import free-form files such as:

```text
# PDF page 2
...
# PDF page 4
...
```

Those files are useful human-reviewed source material, but they are not safely bound to:

- scenario ID;
- parent content hash;
- PDF hash;
- page-body hash;
- numeric delta;
- evidence.

Provide a conversion path:

```text
/coc repair export 2,4,6,7,8,10,14,16,17
```

Then copy the reviewed content into the generated workfile.

This prevents a repair prepared for one version of The Haunting from being silently applied to another import of the same title.

## 30. Refactor of `scenario_source_review`

Avoid duplicating page-validation logic.

Extract reusable public helpers from private functions where practical.

Suggested:

```python
scenario_source_review.split_source_pages(...)
scenario_source_review.validate_reviewed_page(...)
scenario_source_review.numeric_delta(...)
scenario_source_review.build_candidate_text(...)
```

Or move common logic into:

```text
app/scenario_source_repair_common.py
```

Both CLI source review and Discord repair should use the same:

- page splitting;
- page-marker protection;
- bbox validation;
- numeric counting;
- published-page serialization;
- candidate hashing.

Two deliberate differences from the review CLI: page repair splits the parent lossless (rule 11) and compares numbers with the mechanics-aware tokens (rule 13). The stripping splitter and `scenario_numbers.counts` stay for their existing callers.

Do not maintain two subtly different validators.

## 31. Proposed files

### New

```text
app/scenario_page_repair.py
app/services/scenario_repair.py
tests/test_scenario_page_repair.py
tests/test_discord_scenario_repair_upload.py
docs/specs/feature/discord_page_level_scenario_repair_design_spec.md
docs/specs/feature/discord_page_level_scenario_repair_design_spec_zh.md
```

If the project keeps English/Traditional-Chinese mirrored specs, add both.

### Modified

Likely:

```text
app/commands/handlers/uploads.py
app/discord_bot.py
app/services/scenario_lifecycle.py
app/scenario_source_review.py
app/scenario_library.py
app/help_service.py
app/discord_transport/help_ui.py
tests/test_pdf_scenario_lifecycle_integration.py
```

Potentially:

```text
app/trusted_scenario_source.py
app/scenario_templates.py
```

depending on publication and translation invalidation handling.

## 32. Suggested API

### `scenario_page_repair`

```python
@dataclass(frozen=True)
class RepairTarget:
    scenario_id: str
    content_hash: str
    pdf_sha256: str
    page_count: int

@dataclass(frozen=True)
class PageRepair:
    page: int
    base_page_sha256: str
    text: str
    page_kind: Literal["text", "map", "image"]
    review_note: str
    evidence: tuple[EvidenceRegion, ...]
    expected_numeric_delta: NumericDelta

@dataclass(frozen=True)
class RepairProposal:
    version: int
    target: RepairTarget
    patches: tuple[PageRepair, ...]

@dataclass(frozen=True)
class RepairCheck:
    ready: bool
    candidate_digest: str
    candidate_text: str
    repaired_pages: tuple[int, ...]
    issues: tuple[str, ...]
    changes: tuple[dict, ...]
```

Functions:

```python
parse_markdown_bytes(data: bytes) -> RepairProposal

check(
    proposal: RepairProposal,
    *,
    scenario_id: str | None = None,
) -> RepairCheck

publish(
    check: RepairCheck,
    *,
    reviewer_user_id: str,
    reviewer_display_name: str,
    uploaded_filename: str,
) -> str
```

## 33. Atomicity

The operation has two independent atomic boundaries:

### Source publication

All-or-nothing.

If any patched page fails validation:

```text
publish nothing
```

### Active game activation

All-or-nothing under the conversation/state transaction.

Do not update:

```text
scenario_text
```

before:

```text
scenario_library_id
```

or vice versa.

State must never point at a partial candidate.

## 34. Idempotency

Calculate:

```text
candidate_digest =
digest(
  parent scenario identity
  normalized repair proposal
  candidate page text
)
```

Derived scenario ID is deterministic from this digest.

Re-upload behavior:

### Same repair, already published, parent still active

Return the same derived ID and ensure activation is correct.

### Same repair, derived version already active

Reply:

```text
這份修復已經套用，沒有重複建立版本。
```

### Same file against a newer parent

Reject stale.

## 35. Translation variants

Source repair changes canonical source identity.

Existing translated/template variants bound to the old source must not silently become valid for the repaired source.

Behavior:

- preserve old variant data under the old scenario;
- mark it unavailable for the new repaired scenario;
- inform the Keeper if the active source had a translated variant;
- use existing `scenario_source_review.rebind()` flow later if translation migration is desired.

Do not auto-copy translated records by record ID.

## 36. RAG behavior

The repaired source creates a new source hash.

Any RAG cache keyed to the parent hash is stale.

Requirements:

1. never reuse a RAG index whose source hash is the parent hash as authoritative for the repaired scenario;
2. schedule rebuild/prewarm for the new hash;
3. while the rebuild is unavailable, use safe fallback retrieval from the new canonical scenario text;
4. do not block game start purely because a derived RAG index is rebuilding, consistent with the project requirement that import enhancements must not prevent play.

## 37. Performance

Expected repair size is small.

A 9-page patch should not rerun:

```text
PaddleOCR
Tesseract
PyMuPDF4LLM full-document extraction
AI PDF repair
```

Expected expensive work:

```text
none for source merge
optional asynchronous index/RAG rebuild afterward
```

Primary upload latency should be dominated by:

```text
Markdown parse
hashing
PDF page bound checks
numeric diff
derived publication
state commit
```

Target: normally sub-second to a few seconds before any optional background index rebuild.

## 38. Security / trust boundaries

Treat repair Markdown as untrusted input.

Reject:

- path traversal;
- filesystem paths from the file;
- embedded page markers;
- unsupported schema keys;
- huge payloads;
- duplicate pages;
- NaN/Infinity evidence values;
- evidence outside the source PDF;
- wrong hashes;
- wrong PDF;
- wrong page count;
- invalid UTF-8;
- unsupported repair versions.

Do not execute Markdown.

Do not use file-provided reviewer identity.

Do not allow repair Markdown to reference another local file.

## 39. Tests

### Parser tests

1. valid one-page repair.
2. UTF-8 BOM accepted.
3. unknown top-level key rejected.
4. unknown patch key rejected.
5. duplicate page rejected.
6. zero page rejected.
7. page > PDF page count rejected.
8. physical page marker injection rejected.
9. malformed evidence rejected.
10. invalid `page_kind` rejected.

### Binding tests

11. wrong scenario ID rejected.
12. wrong content hash rejected.
13. wrong PDF SHA rejected.
14. wrong page count rejected.
15. wrong base-page SHA rejected.
16. repair made against parent A cannot apply to child B.

### Numeric safety tests

17. undeclared added number rejected.
18. undeclared removed number rejected.
19. declared `1D40 → 1D4` change accepted.
20. extra percentage change causes full rejection.
21. number delta on one page does not affect another page.

### Merge tests

22. only patched pages change.
23. untouched page bytes remain identical.
24. physical markers remain ordered exactly once.
25. patch order in JSON does not affect candidate result.
26. no-op repair rejected.
27. same valid repair is idempotent.

### Map tests

28. map page with readable labels and `page_kind=map` accepted.
29. map page can clear low-text quality warning.
30. map repair does not mutate scene-map graph.
31. `page_kind=image` rejected when native/readable text exists.

### Discord tests

32. `repair_*.md` routes to repair handler, not compare handler.
33. ordinary player cannot submit repair.
34. KP can submit repair.
35. more than one repair attachment rejected.
36. repair upload during pending source replacement follows admission policy.
37. stale game revision after validation publishes library version but does not auto-activate.

### Lifecycle tests

38. active repair preserves timeline ID.
39. active repair preserves game_started.
40. active repair preserves claimed PCs.
41. active repair preserves HP/SAN/Luck/inventory.
42. active repair preserves current map position.
43. active repair does not call `_new_upload()`.
44. new source ID becomes active only after complete transaction.
45. old scenario remains readable from library.

### Parse-quality tests

46. repaired page warnings cleared.
47. untouched page warnings retained.
48. load confirmation lists only remaining warning pages.

### Artifact tests

49. parent indexes are not copied as authoritative into child.
50. prewarm/rebuild is keyed to child source hash.
51. stale background rebuild cannot overwrite a newer repair.

## 40. Acceptance test: The Haunting

Base:

```text
The_Haunting_Scenario_trimmed
27 physical PDF pages
warning pages:
2, 4, 6, 7, 8, 10, 14, 16, 17
```

Steps:

```text
/coc repair export 2,4,6,7,8,10,14,16,17
```

External reviewer completes the generated file.

Upload:

```text
repair_the-haunting-scenario-trimmed_01.md
```

Expected:

1. target source identity matches;
2. 9 page hashes match;
3. all evidence boxes valid;
4. numeric deltas match declared changes;
5. candidate contains 27 pages;
6. only the 9 target pages differ;
7. new immutable scenario ID is created;
8. original scenario remains unchanged;
9. repaired warning pages no longer appear in parse-quality warning output;
10. active campaign timeline is unchanged;
11. player characters and state are unchanged;
12. current room positions remain unchanged;
13. new source hash receives a new RAG/index build;
14. re-uploading the same file does not create another version.

## Delivery

Each implementation phase below ships as its own pull request with its own review; the status stays `partial` until phase 5 lands. The existing `scenario_source_review.publish` requires a proposal that covers every page and writes a fully clean parse-quality record, so phase 1 extracts the shared page logic and adds a partial-page publication path rather than reusing `publish` unchanged.

## 41. Implementation order

Recommended sequence:

### Phase 1 — deterministic library repair

1. Extract shared page validation from `scenario_source_review`.
2. Implement `scenario_page_repair` parser.
3. Implement strict schema validation.
4. Implement source/hash/page binding.
5. Implement numeric-delta verification.
6. Implement deterministic candidate merge.
7. Implement immutable derived publication.
8. Add unit tests.

### Phase 2 — Discord upload

9. Add `repair_*.md` routing.
10. Pass `user_id` into upload handling.
11. Add KP/Host authorization.
12. Add repair service handler.
13. Add success/error messages.
14. Add Discord routing tests.

### Phase 3 — active-game correction

15. Add `scenario_lifecycle.activate_repair_version()`.
16. Reuse correction semantics.
17. Add stale revision/timeline checks.
18. Preserve map position/player state.
19. Add lifecycle integration tests.

### Phase 4 — export UX

20. Add `/coc repair export warnings`.
21. Add explicit page-list export.
22. Add Help UI entry.
23. Attach generated workfile to Keeper/DM where supported.
24. Add export tests.

### Phase 5 — derived artifact refresh

25. Schedule index/RAG rebuild against new source hash.
26. Protect rebuild commit with hash/version check.
27. Improve post-repair status reporting.

## 42. Merge criteria

This feature is mergeable only when all of the following are true:

- repair cannot target the wrong source silently;
- ordinary players cannot apply repair;
- full-page replacement is deterministic;
- undeclared numeric changes hard-fail;
- parent scenario is immutable;
- partial repairs cannot publish;
- active game correction does not reset campaign state;
- untouched page warnings remain visible;
- `repair_*.md` never routes to generic compare;
- idempotent re-upload is proven by tests;
- no OCR/API call is required for the page merge itself;
- existing `scenario_source_review` tests remain green;
- existing PDF/Markdown upload behavior remains unchanged.

## 43. Explicit design choices

### Why not directly accept the free-form repair file?

Because a free-form file is not cryptographically bound to the source it was reviewed against.

A title such as:

```text
The Haunting Scenario trimmed
```

is not enough to prove source identity.

### Why replace the whole page instead of merging paragraphs?

Because physical page replacement gives deterministic provenance and avoids fuzzy text alignment.

### Why require numeric deltas?

Because source-repair is exactly where OCR-corrupted mechanics can enter the canonical source. A repair system must make numeric mutations explicit.

### Why create a child scenario instead of overwriting?

Because repairs must be reversible, auditable, reproducible, and safe for existing campaigns.

### Why preserve the current game state?

Because this operation fixes the scenario source; it does not represent starting a new scenario.

## 44. Final expected Keeper experience

After implementation, the entire flow should feel like:

```text
Bot:
⚠️ 第 2、4、6、7、8、10、14、16、17 頁需要核對。

Keeper:
/coc repair export warnings

Bot:
已產生 repair_the-haunting-xxxx.md。
請連同原 PDF 交給外部工具核對，完成後把 repair_*.md 上傳回來。

Keeper:
[uploads repair_the-haunting-xxxx_01.md]

Bot:
✅ 修復完成。
已核對並替換第 2、4、6、7、8、10、14、16、17 頁。
建立新版劇本來源：the-haunting-...-repair-xxxxxxxxxxxxxxxx
目前遊戲已使用修正版；角色、進度與位置未重置。
```

That is the intended v1 contract.
