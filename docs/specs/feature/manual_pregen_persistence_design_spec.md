# Manual role cards across games

[繁體中文](manual_pregen_persistence_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `feature`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Manual role-card assets persist independently of the current scenario candidate pool and active investigators. Restarting or switching a game must not erase reusable manual cards.

2. Associate cards with scenario/source identity. Reinstall the correct pool on scenario activation and preserve applicable claims during correction; unrelated scenario candidates must not leak.

3. Legacy assets without trustworthy source provenance require conservative migration. Source changes can mark merged cards stale and request reimport rather than guessing affiliation.

4. Persist asset capture and pool installation with state changes in a transaction. Manual attribute corrections survive later PDF re-extraction.

## Flow and interfaces

```text
role_ import -> durable manual asset -> scenario association -> candidate pool -> claim
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/repositories/manual_pregens.py](../../../app/repositories/manual_pregens.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [app/db.py](../../../app/db.py)
- [tests/test_manual_pregen_persistence.py](../../../tests/test_manual_pregen_persistence.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/manual_pregen_persistence_design_spec.md)
