# Scenario lifecycle review fixes

[繁體中文](project_review_fixes_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Reject scenario switching while pregen Luck or a PDF/scenario selection is pending; retain the existing pending state rather than stranding it in a new scenario.

2. Import, merge and reparse must not acquire the same non-reentrant conversation lock twice. Slow parsing happens outside the short installation critical section.

3. SCENARIO_LIFECYCLE_KP_ONLY defaults false. When enabled, text commands and PDF callbacks revalidate the current KP/Discord Keeper authorization; do not describe this as an unconditional KP-only rule.

4. Stage synchronous state reads off the Discord event loop. Public error replies use fixed wording; detailed exceptions belong in logs.

## Flow and interfaces

```text
Upload/command -> authorization and pending-state checks -> parse outside lock -> guarded installation
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [app/commands/router.py](../../../app/commands/router.py)
- [app/discord_bot.py](../../../app/discord_bot.py)
- [app/config.py](../../../app/config.py)
- [tests/test_scenario_library.py](../../../tests/test_scenario_library.py)
- [tests/test_bot_lifecycle_scripts.py](../../../tests/test_bot_lifecycle_scripts.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/project_review_fixes_design_spec.md)
