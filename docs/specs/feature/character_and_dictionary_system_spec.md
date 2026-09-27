# Character imports, reconciliation and dictionary

[繁體中文](character_and_dictionary_system_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `feature`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Discord routes role_, dict_ and map_ inputs through the appropriate parsers; scenario PDFs use the reusable library lifecycle. Filename routing does not authorize a player to overwrite unrelated live state.

2. Matching has three independent gates; any one is sufficient: exact name/alias or a substring of at least two characters; at least seven equal, present values among nine base attributes; or matching occupations plus at least three canonical skill/value matches. It does not perform general phonetic translation. Successful attribute or occupation/skill matches can teach future name aliases. Preserve source/manual provenance when reconciling.

3. Derive supported secondary values and baseline skills when rules permit. Preserve arbitrary skills, age, open descriptive fields and weapon descriptions rather than forcing all content into numeric fields.

4. Luck uses verified final-sheet evidence or an explicit owner roll when blank. Unresolved PDF core attributes cannot silently become constructor defaults; manual corrections can resolve them.

5. Candidate pools are scenario-scoped; manual assets persist independently. Active investigators and claimed cards require careful preservation during correction and game transitions.

6. The real modules are pregen_extractor.py, character_matcher.py and dictionary.py. The old dictionary_manager/character_reconciler/game_pipeline examples were sketches, not existing runtime files.

7. Game start enforces readiness and keeps preparation separate from ordinary narrative turns. General character generation is not a substitute for claiming required scenario pregens.

## Flow and interfaces

```text
Named upload -> parse fields -> evidence/identity matching -> merge candidates -> owner claim -> readiness gate
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/pregen_extractor.py](../../../app/pregen_extractor.py)
- [app/character_matcher.py](../../../app/character_matcher.py)
- [app/dictionary.py](../../../app/dictionary.py)
- [app/skill_aliases.py](../../../app/skill_aliases.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [app/discord_bot.py](../../../app/discord_bot.py)
- [tests/test_pregen_and_creation.py](../../../tests/test_pregen_and_creation.py)
- [tests/test_pregen_extra_fields.py](../../../tests/test_pregen_extra_fields.py)
- [tests/test_manual_pregen_persistence.py](../../../tests/test_manual_pregen_persistence.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/character_and_dictionary_system_spec.md)
