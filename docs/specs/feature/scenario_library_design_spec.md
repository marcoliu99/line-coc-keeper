# Reusable scenario library and chapter context

[繁體中文](scenario_library_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `feature`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Persist reusable parsed source, chapters, indexes, pregens, page images and extraction-quality artifacts outside per-conversation state. Reusing a scenario does not require reparsing its PDF.

2. GroupState.scenario_text remains a compatibility snapshot of the permitted context window, not an unrestricted full-library authorization. Active chapter/context IDs govern installation and retrieval.

3. A typical player window contains current and next chapters under spoiler policy. Explicit reparse/correction preserves the intended game position and claims; a new scenario resets scenario-owned state.

4. Pending upload choices and pregen Luck prevent incompatible switching. Slow parsing is outside conversation-lock critical sections, with fresh authorization/state checks before installation.

5. Image/map assets have visibility and spoiler metadata. Searching or displaying assets must honor role/privacy policy, not merely detect a map-shaped image or the word map.

6. Source/chapter changes invalidate incompatible Chinese variants. Activation responses surface fallback notices; manual pregen assets are reconciled with source identity rather than silently reused across unrelated scenarios.

## Flow and interfaces

```text
Upload/staged PDF -> parse once -> library manifest/assets -> select chapter window -> install group snapshot -> gameplay
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/scenario_library.py](../../../app/scenario_library.py)
- [app/pdf_loader.py](../../../app/pdf_loader.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [app/commands/handlers/system.py](../../../app/commands/handlers/system.py)
- [app/scene_map.py](../../../app/scene_map.py)
- [tests/test_scenario_library.py](../../../tests/test_scenario_library.py)
- [tests/test_spoiler_policy.py](../../../tests/test_spoiler_policy.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/scenario_library_design_spec.md)
