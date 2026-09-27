# Externally prepared Chinese-first retrieval

[繁體中文](scenario_templates_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Localization is prepared outside the bot and reused during play. There is no automatic background translation, startup translation job or fixed per-turn query-translation request.

2. Schema v3 binds source/chapter hashes, records_hash and compiler version. Semantic records carry exact source spans and field evidence; coverage and source-quote validation reject stale or unsupported workbooks.

3. Private preview, approval and activation are separate steps. Help selects source/file or version pairs from server-held options; files must already be in IMPORT_DIR and pass authoritative validation.

4. Compile only current permitted chapter records. Expand dependencies with deduplication/cycle handling and visibility isolation; merge permitted public/KP sibling scopes before result deduplication.

5. Projection limits are 6000 characters per record, 12000 per dependency bundle and 18000 per response. Whole-record omission and inaccessible dependencies are signaled, not silently treated as complete evidence.

6. Runtime retrieval uses Chinese projections while original quotes remain private audit evidence. Cache identity includes source/chapter/model/compiler and file stamps; stale/unapproved/missing variants fall back to original.

7. Shared search_for_state handles proactive and explicit lookup. Zero Chinese hits or known incomplete/budget warnings trigger one original search within state.scenario_text, preserving useful Chinese evidence if supplementation misses.

8. source=original bypasses the Chinese index when Executor identifies missing armor, attacks, abilities, triggers, costs or limits. Neither Chinese hits nor original hits prove semantic completeness; unresolved facts remain unknown.

9. Original supplementation never unlocks another chapter or invents a location. Preserve enemy-card and distinct-instance requirements before mechanical action.

10. Historical pilot: RAG plus Executor median 7.26s original versus 4.22s partial Chinese, 3 versus 2 generative calls, only three cases per arm with mocked checks. Query rewriting alone cost median 3.35s. These are motivation, not a current full-game benchmark.

## Flow and interfaces

```text
Export workbook -> external localization -> import/validate -> private preview -> approve -> select
Current chapter records -> Chinese retrieval -> sufficient evidence / bounded original supplementation
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/scenario_templates.py](../../../app/scenario_templates.py)
- [app/scenario_projection.py](../../../app/scenario_projection.py)
- [app/scenario_rag.py](../../../app/scenario_rag.py)
- [app/help_actions.py](../../../app/help_actions.py)
- [tests/test_scenario_template_v3.py](../../../tests/test_scenario_template_v3.py)
- [tests/test_scenario_template_units.py](../../../tests/test_scenario_template_units.py)
- [tests/test_scenario_query_fallback.py](../../../tests/test_scenario_query_fallback.py)
- [tests/test_scenario_template_review_fixes.py](../../../tests/test_scenario_template_review_fixes.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/scenario_templates_design_spec.md)

## Subsequent authoring v1 / runtime v4

The v3 contract above is tied to its stated baseline and remains applicable to legacy imports. See [external AI authoring](external_template_authoring_design_spec.md) for new exports, campaign retrieval and diagnostics. The new format does not reject complete content using old projection thresholds and retains original-source fallback and review requirements.
