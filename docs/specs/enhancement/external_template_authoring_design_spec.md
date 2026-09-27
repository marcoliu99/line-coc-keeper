# External AI template authoring and import diagnostics

[繁體中文](external_template_authoring_design_spec_zh.md)

## Status and scope

Category: `enhancement`. Status: **proposed — not implemented**. Baseline: `main_v2` at `c05e152` (2026-09-27). This proposal extends PR #83 while retaining original-source evidence, chapter isolation and approval before activation, and adds complete storage and demand-driven retrieval for long campaigns.

This branch contains design documents only. Existing schema v3 remains the live import format until implementation and migration tests are complete.

## 1. Observed failure and cause

The reported The Haunting export contains one source unit, `chapter-01-u1`, covering PDF positions 1–27 and 71,612 Unicode characters. Export hashes match the current scenario. Gemini returned four records, retained that source ID, rewrote page ranges (including 28–40), and omitted `source_spans` and `type`. It also used unsupported visibility `all`, plain strings in rule fields, and public content in KP-only records.

Read-only validation reproduced the page/chapter error. Correcting only page metadata in memory exposed the next error: missing `source_spans`. Neither the original file nor live state was modified. The supplied profiler did not capture the import validator; it cannot establish an import latency measurement.

`_blocks()` recognizes Markdown level 1/2 headings. This parsed source lacks usable headings at those levels, so the entire chapter becomes one unit. A short export instruction then asks an external model to split semantics, compute offsets, preserve provenance, and infer a schema from empty arrays. The importer reports only the first generic failure. Correct rejection is necessary, but the authoring contract and diagnostics need improvement.

## 2. Goals and exclusions

1. A user uploads the exported MD to a web AI, pastes its included instruction, downloads the completed MD, and uses existing Help import controls.
2. The program owns hashes, page/chapter mappings, source spans and coverage calculations. The external AI owns faithful Chinese translation and explicit rule/source quotations.
3. Produce an actionable validation report before saving an import candidate; keep preview, approval and activation separate.
4. Reuse prepared content during gameplay without a fixed new LLM call or a new translation API dependency.

Excluded: automatic background translation, browser automation, accepting a prose summary as full coverage, guessing missing rules, silently repairing ambiguous source matches, or translating production scenarios during this documentation task. Better export packaging cannot repair incorrect PDF reading order; damaged excerpts remain unresolved and point back to PDF inspection/reparse.

## 3. Export package and source units

The proposed export includes: a copyable web-AI prompt; immutable source excerpts and unit IDs; editable translation records; one complete synthetic example; field constraints; and a completion checklist. Example records are outside the import JSON and cannot be imported accidentally.

Persist an immutable export manifest in the scenario library, identified by `export_id`, source/chapter hashes, authoring-format version and segmentation version. Store exact text ranges and unit hashes server-side. An imported MD cannot redefine that mapping. Reparse or segmentation changes produce a new export identity; missing/expired identities require re-export.

Segmentation uses existing scene/heading metadata when reliable. With no useful headings, use deterministic page/paragraph units that preserve exact original ranges; pages with opaque or damaged text are flagged. These are provenance anchors, not a claim that a rule ends at a page boundary. Provide neighboring source context separately, with IDs, and require explicit links when a condition/consequence spans units. Context-only material does not count as translated coverage.

Keep existing `_blocks()` source IDs as compilation parents. New smaller unit IDs map to ranges within those parents; do not change legacy v3 interpretation. The compiler can emit multiple linked new-format records when an authored semantic record spans parents. Do not cut a rule merely to meet a size limit; unresolved cross-unit rules block approval.

Initial configurable packaging target: at most 8,000 source characters per batch, allowing an intact unit to exceed the target with an explicit warning. This is a proposed usability target, not a measured optimum or gameplay projection limit. Batches have IDs, expected unit inventories and progress counts; users can return them separately without losing unfinished work. The live v3 limits of 6,000/12,000/18,000 characters describe the old projector; they are not acceptance limits on new authoring or stored content. Section 4A defines the new per-request budget.

## 4. Authoring format and compilation

Introduce a distinct `authoring_version: 1` envelope; do not relabel it as the live `schema_version: 3`. This revision proposes runtime schema v4 and a compiler-version bump to distinguish the new dependency and projection semantics. Required envelope fields: `export_id`, `batch_id`, `records`. An authored record contains unique `id`, `unit_ids`, `type`, `name`, `aliases`, `keywords`, `visibility`, `public_text`, `kp_text`, `rules`, `related_record_ids`, and `uncertainty`. Runtime metadata is absent from the editable payload and reconstructed from the export manifest.

Rules retain the current five fields: trigger/check/success/failure/exceptions. Each supplied field contains `text` and `evidence` entries with `unit_id` and an exact `source_quote`. At compilation, require a unique exact occurrence within the specified unit; multiple matches request clarification/resegmentation. Do not use fuzzy matching or fabricate offsets. If several evidence entries are necessary, compile separate complete rule entries or report a representation error; never widen a quote to include unrelated text to pass validation.

Each assigned source unit needs a complete translation, or an explicit unresolved status that blocks approval. Assigned-unit coverage is only structural coverage: it does not prove translation fidelity. Numeric/negation checks, rule evidence, private preview and review remain necessary. All uncertainty survives import until explicitly resolved.

The compiler creates trusted page/chapter/span fields from the registry, rebuilds source excerpts and maps dependencies. Retain v3 source, authorization, quotation and coverage checks; v4 replaces whole-content projection rejection with complete storage and bounded retrieval. Exceeding an old projection limit alone is not an import or approval error; explicit file/storage resource limits still apply. It may derive metadata unambiguously; it may not repair an unsupported assertion, translate missing content or silently change privacy. A KP-only record with public content is an error, not an automatic publication decision.

Partial batch drafts are stored separately from playable variants. A complete validated aggregate becomes a review-required candidate only through an atomic save. Duplicate identical batches are idempotent; conflicting record IDs or changed already-submitted content require explicit replacement and revalidation. No partial import changes the active variant.

## 4A. Long campaigns: complete storage and per-request budgets

1. **Separate three sizes.** The library stores complete reviewed material; authoring batches control external translation workload; gameplay projection budgets control only the current model input. A record over 6,000 characters or dependencies over 12,000 must not force deletion, rule rewriting or rejection of an entire long scenario.
2. **Separate complete records from retrieval fragments.** Preserve full records and rule groups. Indexed fragments have stable IDs, parent records and exact source ranges. Background can be retrieved by paragraph; armor, attacks, triggers, costs, per-round/per-combat limits and exceptions are required when relevant to adjudication. A shortened summary is not a complete rule.
3. **Typed dependencies without pre-expanding the entire graph.** v4 distinguishes required_for_adjudication, conditional and background links with applicable actions/conditions and source evidence. External AI may propose classifications; mechanical dependencies cannot be downgraded without review. Treat unknown conditions as potentially necessary. Select target scenes/NPCs, assemble applicable required evidence, deduplicate and detect cycles. Local traversal has node/work limits; hitting them marks the result incomplete.
4. **Budget against the model and current remaining capacity.** Estimate tokens and subtract system instructions, tools, current state, retained history, output reservation and a safety margin; take the smaller application budget and available capacity. Use a marked conservative estimate when tokenizer support is unknown. Characters are resource safeguards, not token equivalents. Reuse shared token admission without bypassing rate/wait limits. Omit optional background before required rules.
5. **Return checkable completeness metadata.** Results include record_ids, included_fragment_ids, missing_required_ids, deferred_optional_ids, blocked_dependency_count, complete_for_action, continuation_token and reasons. Completeness is relative to the declared action and known dependencies; a Chinese hit or a machine complete flag does not prove absence of unknown semantic gaps. Executor can still request missing mechanics.
6. **Retain oversized required rules and support continuation.** Hard truncation is not success. If a necessary rule group cannot fit even with minimum context, return a recoverable capacity error and continuation entry point. Paging may follow complete subrules, but dependent mechanical actions remain blocked until evidence is sufficient. Continue within the existing tool loop when possible; otherwise explicitly defer and request a narrower action or reviewed decomposition. Vague cross-turn memory does not establish complete evidence.
7. **Campaign history still obeys authorization.** Index currently permitted chapters separately from persistently revealed facts. Earlier NPCs/clues are accessible only under current policy. Dependencies never unlock unrevealed chapters; inaccessible dependencies return safe reasons/counts without private titles or IDs. Bind continuation tokens to scenario, version, query, role/authorization and relevant timeline; invalidate on permission/version changes.
8. **Do not move the bottleneck into import or memory.** Process storage/indexes in batches and load only matching records and necessary dependencies. Avoid full-graph BFS per query or a complete duplicated bundle per root. Configure separate explicit file, batch, storage and traversal resource limits; report actionable failures without silently discarding content.

Example: a 300-page campaign with a 20,000-character NPC record is stored intact. An appearance question retrieves public description; combat retrieves the NPC's required mechanics. If an exception resides in a chapter not yet authorized, report insufficient adjudication evidence rather than unlocking that chapter. This illustrates expected behavior, not a completed performance benchmark.

## 5. Included web-AI instruction

The export must provide the following copyable instruction in both languages. It describes the proposed authoring format, not today's v3 importer:

> Translate the attached source units faithfully into Traditional Chinese using the attached authoring schema and example. This is not a summary or creative rewrite. Keep export_id, batch_id and supplied unit IDs unchanged. Fill only editable fields. Do not calculate page numbers, hashes or character offsets. Do not shorten or omit translations to meet gameplay input budgets; return additional unit batches while preserving the complete content. Preserve every mechanical value, trigger, cost, limit, exception and consequence. Rules require exact source quotations with their unit IDs. Use only public or kp_only visibility; kp_only public_text must be empty. Report damaged or ambiguous source text in uncertainty; never fill gaps from another edition or memory. Return a downloadable Markdown file with exactly one authoring JSON block. Before starting, report the unit inventory and planned batches. If the response cannot cover all units, return a clearly labeled partial batch and list unfinished IDs; do not claim completion.

Include an actual valid synthetic example with every required record field and one quoted rule, plus explicit statements that example IDs are not real source IDs. Explain that the downloaded file must be placed in the server IMPORT_DIR before using Help; a web AI cannot directly call the bot's import command. No browser product-specific capabilities or output-length guarantees are assumed.

## 6. Validation report and user flow

Collect independent failures rather than stopping after the first record. Bound the report (proposed cap: 100 issues, with total/omitted counts) and skip dependent checks when prerequisites are invalid. Malformed JSON gets line/column information; invalid record shapes must not crash deeper validators.

Each issue has code, severity, record ID, unit ID, field path, bounded expected/actual values and a suggested correction. Suggested codes: SOURCE_METADATA_MISMATCH, UNKNOWN_UNIT, MISSING_FIELD, INVALID_VISIBILITY, RULE_EVIDENCE_MISSING, AMBIGUOUS_QUOTE, COVERAGE_GAP, STALE_EXPORT, PROJECTION_DEFERRED, REQUIRED_EVIDENCE_UNAVAILABLE, RESOURCE_LIMIT. Deferred projection is recoverable and does not invalidate a translation. Detailed reports and source snippets remain KP-only; public replies provide a safe summary without private paths or source text.

Preserve Help's authorization, owner checks and filename selection. An import invokes preflight and then either retains an incomplete draft, returns a correction report, or saves a review-required variant. Preview/approve/select remain explicit. A failed import leaves existing playable content unchanged. A full report is downloadable through the authorized flow and can be given back to the external AI along with the same export package.

## 7. Flow and existing interfaces

```text
Help export -> export_template -> trusted source-unit registry
  -> MD instructions + example + batches
  -> user uploads to web AI -> translated MD -> server IMPORT_DIR
Help import -> authorization + file selection -> format dispatch
  +-> legacy schema v3 -> enhanced diagnostics + existing validation
  +-> authoring v1 -> registry lookup -> batch/quote validation
       -> incomplete draft / correction report
       -> complete aggregate -> compile v4 -> validate source/coverage/storage resources
  -> atomic review-required variant -> private preview -> approve -> select
Gameplay -> authorized source index -> assemble required evidence -> token-budgeted retrieval
  -> complete evidence: adjudicate/narrate
  -> required evidence missing: explicit continuation/original lookup -> verify -> adjudicate
```

Implementation boundaries: `app/scenario_templates.py` owns export/import orchestration and versioned persistence; pure authoring validation/compilation should live in a dedicated module. `app/scenario_projection.py` needs v4 bounded projection and evidence-completeness reporting. `app/help_actions.py` and scenario command handlers expose files/reports through existing authorization. RAG paths must dispatch compiled v3/v4 records by version and never consume authoring drafts. v4 requires an Executor contract for missing evidence and continuation; changing import validation alone is insufficient.

## 8. Compatibility, tests and acceptance

Keep valid legacy v3 imports, approved variants and English fallback working. Do not silently recompile v3; explicit conversion to v4 requires revalidation and review. Cache identity must include schema/compiler/segmentation/authorization/budget versions so old pre-expanded bundles cannot be reused incorrectly. Do not automatically reinterpret a malformed v3 file as authoring v1. The reported Gemini output should yield multiple useful diagnostics; it must not become approved through metadata patching alone.

Required tests:
1. Synthetic unheaded multipage source exports manageable, deterministic units with exact coverage and stable registry mappings.
2. Unicode offsets, repeated quotes, cross-page conditions, context-only text, duplicate IDs and cross-chapter links.
3. Complete valid authoring export/edit/import round trip, compiling to v4 and preserving numeric rules and privacy.
4. Missing fields, all visibility, KP-only/public conflicts, string-shaped rules, out-of-range pages, stale/missing registries and malformed JSON.
5. Partial batches, idempotent replay, conflicting replacement, failed atomic save and preservation of active variants.
6. Report bounds, authorization and no private excerpts in public replies.
7. Legacy v3 import/approval and existing Chinese/original retrieval regression suites.
8. Prompt/example schema validation so shipped examples cannot drift from the importer.
9. Store synthetic campaigns of 300+ pages, recurring cross-chapter NPCs, 20,000-character records and dependencies over 12,000 characters intact; old projection limits do not reject import.
10. Huge/cyclic dependency graphs, mandatory rules over budget, unknown conditions and permission/version changes during continuation neither reveal blocked chapters nor claim completeness incorrectly.
11. Compare short scenarios and campaigns on input tokens, tool/LLM calls, retrieval latency, full-turn latency, required-rule recall and adjudication accuracy. Explicitly check armor, abilities, triggers, costs and use limits, not just NPC-name hits.

Use small synthetic fixtures shaped like the reported failure; do not commit the user's full scenario or logs. Later, perform an explicitly authorized web-AI usability trial: record units completed, import attempts, diagnostics, omissions and manual fixes. No paid API trial is implied by approving this design. Performance acceptance: no fixed additional gameplay LLM call; assemble required evidence locally before the normal request where possible. Oversized or ambiguous cases may require continuation and extra LLM rounds, which must be measured rather than promising equal latency for every campaign. Measure export/import time separately from translation time.

## 9. Decisions to review

Recommended direction: separate authoring v1 from runtime v4, with a legacy v3 compatibility path and keep authoritative metadata server-side. Review batch sizing and draft retention policy before implementation. Exact quotation is intentionally conservative; repeated or broken excerpts need explicit correction rather than permissive matching. Automatic segmentation cannot certify complete semantic dependencies, so unresolved boundaries must stay visible.

Approval of the design is required before runtime implementation. The existing malformed translation is useful as a draft, but reconstructing and reviewing it is a separate content task.

[Existing template specification](scenario_templates_design_spec.md) | [Authoring reference](../../references/scenario_zh_external_preparation.md)
