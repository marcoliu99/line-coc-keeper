# Chinese-first scenario retrieval: PR83 redesign

## 1. Objective and measured motivation

Make a Chinese player action retrieve complete Chinese adjudication evidence on the
first pass. Reduce supplementary searches and model round trips without removing
mechanics, source fidelity, chapter boundaries or PR89's validated handoff.

The historical one-page pilot measured median RAG + Executor time of **7.26 s** for
original English material versus **4.22 s** for a Chinese-localized page. Each arm had
only three scored runs; API calls were 3 versus 2, explicit searches 1 versus 0, and
complete target-marker coverage 0/3 versus 3/3. Warm RAG itself took about 0.03 s in
both arms. The observed saving was principally one downstream model/search round,
not faster database work. Raw metadata is preserved in
[evaluations/pr83_chinese_page_pilot.json](evaluations/pr83_chinese_page_pilot.json).

A separate aligned-context pilot reduced Executor median 6.32 s to 4.83 s, but an
English query rewrite cost a reported median 3.35 s. The illustrative sum including
retrieval was 8.42 s, not a paired end-to-end result. Original retrieval does not
always call a translator: it queries directly with Chinese text. Poor evidence can
cause the model to formulate another search. We must not add a mandatory per-turn
translation call to solve that problem.

These pilots used one action, a fixed check and stubbed mechanics; they exclude
Narrator and Discord. They do not validate this redesigned implementation or establish
production latency/quality. See [historical analysis](turn_latency_design_spec.md).

## 2. What changes from the previous PR

| Previous implementation | Redesign |
| --- | --- |
| Internal translation generator, job state, retry and priority infrastructure | External authoring only; remove generator commands, startup jobs and provider/retry additions from this PR |
| One source unit equals one retrieval record | One source unit may contain multiple externally authored semantic records, each with explicit source spans; complete coverage is validated |
| Original English + Chinese narrative + summary + JSON rules in every result | Audit artifact retains original text/quotes; compiled runtime projection contains Chinese narrative and rule text once, with concise provenance |
| Expand potentially unbounded linked units | Record-ID dependencies, cycle deduplication and complete-bundle size validation; links do not override chapter/visibility boundaries |
| Silent fallback indistinguishable from Chinese retrieval | Effective variant and fallback reason reported in retrieval diagnostics |
| Large selected context accepted regardless of cost | Reject oversized complete bundles for external re-editing; apply a response budget between complete records, never mid-rule |

Preserve source PDFs, scenario identity, assets, current scenario/RAG instructions,
context-reuse policy, manual role-card improvements and PR89 handoff. Do not add
Executor early stopping, dynamic tool scoping, a new reviewer model or another agent.

## 3. Source-bound external authoring (schema v3)

Ready-to-use authoring instructions: [external Chinese preparation template](references/scenario_zh_external_preparation.md).
Copy its Chinese prompt together with an exported workbook and the original PDF.

    English PDF/OCR + page images
      -> deterministic source-bound export (no model)
      -> external Chinese translation / semantic grouping / human review
      -> import: schema + source spans + quotes + numbers + dependencies
      -> compile Chinese-only gameplay projection + size checks
      -> KP review and approval
      -> activate immutable version / prewarm existing index

A source unit preserves original heading, source ID, chapter and PDF page positions.
Export provides one empty record per unit. The editor may split it into complete
scene, rule, NPC, clue or handout records. Do not split a trigger from its consequence;
use explicit record dependencies for inseparable related evidence.

Each record contains:

- `id`, `source_id`, `chapter_id`, `page`, `source_pages`, `type`, `name`;
- `source_spans`: nonempty `[start, end)` offsets into that unit's exported Unicode
  text (Python string/code-point offsets, not UTF-8 bytes); overlapping spans permit
  shared context, but their union must cover every source character across records;
- `aliases`, `keywords`: reviewed Chinese/common English names for this record;
  never infer that every entity mentioned in a scene is an alias of the scene;
- `public_text`, `kp_text`: full faithful Chinese prose, visibility-separated;
- `rules`: ordered rule blocks with optional trigger/check/success/failure/exceptions;
  each field holds Chinese `text` and exact `source_quote` for audit;
- `related_record_ids`: complete-evidence dependencies, distinct from merely mentioning
  a lead to a distant chapter;
- `uncertainty`: unresolved translation notes; nonempty notes prevent approval.

`rule_text` remains an editable compatibility field, but does not duplicate structured
rules in gameplay. If populated, structured rules are required. Runtime renders each
structured rule field once; costs, uses-per-round/encounter, timing, exceptions and
special abilities must be retained in those fields. Blank source information stays
unknown. No inferred armor, spell effect, room, item or person is permitted.

V2 variants require re-export/review; no silent reinterpretation. Exported workbooks
include schema version, source hash and chapter hash. Imported exact source excerpts
are reconstructed from spans; user-edited copies cannot override original evidence.

### Authoring limitations

The program checks structural coverage, not semantic equivalence of a translation.
A Chinese paraphrase that omits meaning can still pass numeric checks; human review
remains required. OCR reading order must be checked against page images, especially
for two columns, stat tables and cross-page abilities. Large source units are not
silently cut by the exporter. External editors decide semantic boundaries and spans.

The existing `.md` import contains records JSON; arbitrary prose-only Markdown is not
accepted. Export writes a private file under IMPORT_DIR and privately reports its
server path. A friendly authoring UI and direct Discord attachment delivery are out
of scope. The provided role-card format remains a separate `role_` upload format.

## 4. Audit storage versus runtime evidence

Audit records preserve full source slices, exact source quotes, review notes, reviewer
identity/time and content/source versions. These are available to the KP's paginated
private preview. Runtime indexing never embeds full original excerpts or source-quote
JSON. Proper-name aliases may legitimately remain English.

    approved audit record
      -> pure deterministic projection
           public: Chinese public prose
           KP: Chinese KP prose + ordered Chinese rule fields
           metadata: record/source IDs and PDF pages
      -> Chinese searchable child chunks (~500 characters)
      -> rank record IDs, return complete permitted evidence bundle

Use one shared projection compiler for validation and runtime indexing so approval
checks the same payload that gameplay will receive. A record's combined public/KP
projection must fit 6,000 characters; its complete dependency closure must fit 12,000.
These are explicit deterministic payload bounds, not token-count equivalences.
Oversized content fails validation with an edit requirement. Never silently truncate
rules. Source/audit content has no corresponding runtime projection size requirement.

Return up to top-k records with a combined 18,000-character evidence budget, stopping
between complete records and signaling omissions. Chapter filtering precedes expansion;
visibility filtering applies to every dependency. A dependency outside the allowed
window is reported as unavailable, not silently treated as complete or unlocked.
Public retrieval must not reveal KP dependency text or forbidden chapter identities.

Rules-mode alternatives (standard CoC versus Pulp), scenario dates and optional scenes
must stay explicit in authored content. A reference link is not an instruction to
advance the campaign or disclose a future chapter. Full nonlinear campaign navigation
is not implemented by this PR.

## 5. Gameplay interfaces and fallback

    player Chinese action
      -> context_builder -> index_for_state -> shared Chinese record index
      -> search -> complete, bounded Chinese evidence + selection diagnostics
      -> Executor (reuse supplied evidence)
          -> if concrete evidence missing: search_scenario through same selector
          -> real mechanics tools -> PR89 validated adjudication
      -> Narrator -> player

No fixed query translation, no background translation and no fixed model audit.
Supplementary search remains available; never cap it merely to improve benchmark
numbers. Query embeddings are ordinary retrieval calls, not generative translation.

`index_for_state` accepts a diagnostics map that records requested/effective variant,
fallback reason and projection version. Both proactive RAG and explicit search log
these fields. Invalid/stale/unapproved variants fall back to the original scenario;
the fallback is observable and a failed Chinese arm cannot be counted as Chinese
success. Cache identity includes source/chapter/variant/window/embedding model and
projection version. A source change must not serve an old variant under a new key. A bounded in-memory
selection cache checks inode, size, modification and change times for source, records
and manifests before reuse; unchanged turns avoid rereading full bilingual artifacts.
Cold loads verify source and record SHA-256 hashes. Approval or file changes invalidate
the cache; edited approved records require re-import/review. The glossary stores aliases
per record so identical names in different scenes do not overwrite one another.

The original source is retained for KP audit and original-mode fallback. This change
does not create an always-on second English search path. Specific unresolved source
questions require correction/review; the model must not invent an answer because the
Chinese record is incomplete.

## 6. Safety and stability gates

- Schema/type/bounds validation before writing an immutable draft.
- Complete source-span coverage, no out-of-range spans, unique IDs, valid dependencies.
- Exact quote containment within the record's source slices; per-field number/dice
  checks; missing numeric/negation evidence becomes a review issue.
- No automatic approval; pending uncertainty blocks activation.
- Same projection and size limits at import/approval/indexing; deterministic failure
  does not trigger automatic paid repair.
- Cycles terminate; complete evidence is not recursively duplicated within a bundle.
- No source-quote leakage into ordinary gameplay text; scope boundaries retained.
- No mutation of live campaign state during import or benchmarks.

## 7. Verification and performance evaluation

Deterministic tests must cover split-unit source coverage, missing spans, malformed
records, wrong quotes/numbers, lost consequences, projection without English source,
complete linked rules, scope/window isolation, cycles, payload bounds, response-budget
notices, effective fallback diagnostics, export/import/approve/restart, and unchanged
role-card prose/age behavior. All tests use isolated state; no test invokes paid models.

A local structural comparison may report evidence characters/tokens and coverage on
fixtures. It cannot establish real model speed or ruling correctness. The historical
7.26/4.22 s numbers remain historical.

For the next explicitly requested API evaluation, pair the same scenarios/actions/state
under: A original English; B English with reviewed Chinese aliases (experimental
harness arm); C externally reviewed Chinese v3. Keep model/reasoning, tool exposure,
PR89/PR90 settings, chapter access and cache conditions identical. Use 50 cases per
arm as an initial expanded plan, counterbalance order and include the supplied short
scenarios plus long-campaign-like revisits, timed events and entity aliases. No API run
is authorized solely by this specification.

Measure actual provider calls, search count, first-pass expected-rule coverage, complete
mechanics/tool correctness, input/cached/output/reasoning tokens, retrieval time,
queue/retry/429 time, Executor, Narrator, text/button-ready and full-turn wall time.
Measure directly rather than summing overlapping spans. Include failures and retries;
separate cold-start builds from warmed gameplay. Report paired 95% intervals and
p50/p95; an interval spanning zero does not demonstrate a stable speedup. Translation
and human-review costs are preparation costs, separately amortized over turns.

Reference inspection: [scenario_template_reference_review.md](scenario_template_reference_review.md).

### Implementation verification (2026-09-27)

The redesigned isolated suite passes 794 tests and 15 subtests, with one skipped test.
Ruff passes and mypy checks 75 source files. Regression tests include real temporary
export/import/approval artifacts, in-memory and persisted-index reuse, source/record/
approval invalidation, split-source coverage, scope-preserving dependency closure and
whole-record response-budget handling. These are deterministic correctness checks,
not a new API latency benchmark.
