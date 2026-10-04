# Retiring `legacy_commands`

[繁體中文](legacy_commands_retirement_design_spec_zh.md) | [Docs index](../../README.md)

## Status and goal

Category: `refactor`. Status: **implemented** (phase 4 of the [four-phase architecture refactor](architecture_refactor_phases_1_4_design_spec.md)). Based on `main_v2` at `2affd06` (2026-10-04), stacked on the [state transaction](state_transaction_design_spec.md), [check engine](check_engine_design_spec.md) and [combat engine](combat_engine_design_spec.md).

`app/legacy_commands.py` was 2,590 lines holding player checks and Luck, scenario upload orchestration, map work, investigator rules and a few plain-message commands, imported by the router, six handlers, the Discord adapter and 18 test files. Phases 1–3 moved its state writes, checks and battle handling to their owners; what was left (1,184 lines) moved in this phase and the file was **deleted, not renamed**.

## Where each duty went

| Duty | Owner now |
| --- | --- |
| PDF upload orchestration, the "new scenario vs correction" application, scenario comparison, role-sheet upload, pregen merging | `app/services/scenario_ingestion.py` |
| The GM's PDF choice (permission check, lock, reply) | `app/commands/handlers/uploads.py` (`resolve_pdf_upload_choice`) |
| Map upload, resolving a player's words to a map move, the RAG room lookup | `app/services/map_service.py` |
| Claiming a pregenerated sheet, away/back, healing, the readiness roster, the "already has a character" guards | `app/services/character_service.py` |
| `/coc luck roll` for a claimed pregen | `app/commands/handlers/character.py` (`handle_pregen_luck_roll`) |
| `/roll`, the reply to an unsupported message type | `app/commands/handlers/messages.py` |
| Player checks and Luck; callback type aliases; post-turn delivery | `app/checks`, `app/commands/types.py`, `app/services/post_turn.py` (phase 2) |
| Battle actions | `app/services/combat_engine.py` (phase 3) |

Handlers parse input, check permissions and format replies; the rules live in the services. The functions were **moved, not rewritten**: 27 of the 29 moved definitions are identical to the originals once the leading underscore of a name that became public is ignored (a comparison of their syntax trees); the other two are `build_readiness_roster` (one docstring word) and `resolve_pdf_upload_choice` (its function-level `from app.commands import permissions`, which existed only because the package imported this module, is now an ordinary import).

## Contract kept

1. Command names, aliases, help text and error wording are unchanged (the router diff is imports and renamed calls only).
2. Discord custom ids and button payloads are unchanged; the button handlers were not touched, and a click on an expired timeline is still refused.
3. Stored shapes are unchanged, so existing saves, pending checks and battles load and continue.
4. Upload orchestration keeps its call order, arguments, messages and warnings. `tests/test_ingestion_trace.py` replays a first upload, a similar re-upload, a role sheet and a map upload and compares them with a trace recorded on the last commit that still had the module.
5. State writes still go through the phase 1 transaction boundary; moving a function did not change how it commits.
6. `app.commands` keeps its public names (`handle_pdf_upload`, `handle_check_command`, …) as a plain facade over the owning modules. It no longer resolves anything lazily.

## Enforcement

`tests/test_architecture_legacy.py` fails if the module file returns or any shim named like it appears; if anything in `app/`, `scripts/` or `tests/` imports it (by statement, `from app import legacy_commands`, `importlib.import_module` or `__import__`); if a package `__init__` defines a module-level `__getattr__`; if the new services depend on the command layer at runtime (only for type names); or if `import app.legacy_commands` stops failing. Every entry point is imported in a fresh process.

## Not changed

OCR, layout, numeric validation, fallback order, scenario import thresholds, map extraction and provider selection are untouched; the typed extraction result and provider abstraction stay for a later phase. The scenario upload and map code still commit with the strict snapshot path (`commit_snapshot`) rather than as deltas, because moving a function must not change what it does.

Report: [phase 4](../../refactor/phase4-result.md).
