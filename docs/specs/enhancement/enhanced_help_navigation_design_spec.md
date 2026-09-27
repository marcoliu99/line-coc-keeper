# Categorized Help navigation

[繁體中文](enhanced_help_navigation_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Use one registry for descriptions, categories and availability. Text help and Discord views should agree on command names and role restrictions.

2. The actionable Help layer now executes forms, lists and confirmations. The earlier command-display-only design is superseded by that layer.

3. Nested commands normalize casing consistently. Human-readable references remain supplementary and must not become a second executable command registry.

## Flow and interfaces

```text
Registry -> role-aware category pages -> detail -> executable action
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/help_registry.py](../../../app/help_registry.py)
- [app/help_service.py](../../../app/help_service.py)
- [app/help_docs.py](../../../app/help_docs.py)
- [app/help_registration.py](../../../app/help_registration.py)
- [tests/test_help_navigation.py](../../../tests/test_help_navigation.py)
- [tests/test_help_text.py](../../../tests/test_help_text.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/enhanced_help_navigation_design_spec.md)
