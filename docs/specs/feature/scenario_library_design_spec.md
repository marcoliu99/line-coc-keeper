# Reusable scenario library and chapter context

[繁體中文](scenario_library_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `feature`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Persist reusable parsed source, chapters, indexes, pregens, page images and extraction-quality artifacts outside per-conversation state. PDF remains the rich source format, and a UTF-8 Markdown file whose filename starts with `scenario` is also a first-class text-only scenario source. Reusing a scenario does not require reparsing its source.

2. GroupState.scenario_text remains a compatibility snapshot of the permitted context window, not an unrestricted full-library authorization. Active chapter/context IDs govern installation and retrieval.

3. A typical player window contains current and next chapters under spoiler policy. Explicit reparse/correction preserves the intended game position and claims; a new scenario resets scenario-owned state.

4. Pending upload choices and pregen Luck prevent incompatible switching. Slow parsing is outside conversation-lock critical sections, with fresh authorization/state checks before installation.

5. Image/map assets have visibility and spoiler metadata. Searching or displaying assets must honor role/privacy policy, not merely detect a map-shaped image or the word map.

6. Source/chapter changes invalidate incompatible Chinese variants. Activation responses surface fallback notices; manual pregen assets are reconciled with source identity rather than silently reused across unrelated scenarios.\n\n7. `scenario*.md` is routed before the generic `.md` comparison-upload path. The original bytes are preserved as `source.md`; runtime text gets a synthetic page-1 marker only when the source has no page markers. Markdown import does not synthesize PDF pages, images, OCR evidence, or floor-plan maps. When another scenario is active, Markdown uses the same new-scenario-versus-correction choice as PDF. PDF-only source-review/export tooling rejects Markdown sources explicitly.

## Flow and interfaces

```text
scenario*.md -> UTF-8 text ingest (no OCR) --\\\n\n                                      > library manifest/assets -> select chapter window -> install group snapshot -> gameplay\nPDF/staged PDF -> PDF/OCR parse -------/
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/scenario_library.py](../../../app/scenario_library.py)
- [app/pdf_loader.py](../../../app/pdf_loader.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)\n- [app/commands/handlers/uploads.py](../../../app/commands/handlers/uploads.py)\n- [app/trusted_scenario_source.py](../../../app/trusted_scenario_source.py)
- [app/commands/handlers/system.py](../../../app/commands/handlers/system.py)
- [app/scene_map.py](../../../app/scene_map.py)
- [tests/test_scenario_library.py](../../../tests/test_scenario_library.py)\n- [tests/test_upload_routing.py](../../../tests/test_upload_routing.py)\n- [tests/test_trusted_scenario_source.py](../../../tests/test_trusted_scenario_source.py)
- [tests/test_spoiler_policy.py](../../../tests/test_spoiler_policy.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/scenario_library_design_spec.md)
