# Executable Help controls

[繁體中文](actionable_help_buttons_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Every registered Help entry maps to an executable action, not merely a command string. Derive coverage from the registry; historical 52/52 counts are no longer the current contract.

2. Use modals for character names, occupations and free text; use lists for scenarios, pregens, chapters, template files/versions and other server-known choices.

3. Confirmation is required for configured consequential actions. Selection values are server-held indices, never arbitrary client-supplied command fragments.

4. At dispatch, recheck owner/channel, permissions, state revision and available choices before invoking the existing command router. Stale or missing resources do not execute.

5. PDF reparse cancellation preserves staged data so the user can retry. New purchase, correction and template actions are included in coverage.

6. Template import selects files already present in IMPORT_DIR; this does not add template attachment ingestion through Discord.

## Flow and interfaces

```text
Help category -> action -> input/selection -> optional confirmation -> fresh checks -> command router
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/help_actions.py](../../../app/help_actions.py)
- [app/help_registration.py](../../../app/help_registration.py)
- [app/help_service.py](../../../app/help_service.py)
- [app/help_docs.py](../../../app/help_docs.py)
- [app/discord_bot.py](../../../app/discord_bot.py)
- [tests/test_help_actions.py](../../../tests/test_help_actions.py)
- [tests/test_help_reparse_recovery.py](../../../tests/test_help_reparse_recovery.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/actionable_help_buttons_design_spec.md)
