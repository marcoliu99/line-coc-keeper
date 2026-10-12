# The scenario's other proper names keep one Chinese name

[繁體中文](scenario_glossary_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `7dc932c`, on top of `scenario_names_consistent_design_spec.md`.

## Problem

The stat index fixes the Chinese names of NPCs/monsters with stats and main locations. Everything else with a name had no fixed translation: people without stats (a shopkeeper, a witness), items, books, spells, groups and Mythos beings. The model could translate each one differently from turn to turn.

## Change

- **Extraction.** `report_scenario_index` gains a `terms` list: each named thing not already in `npcs`/`locations`, as `{"name": Chinese, "aliases": [original, …], "kind"}`. `extract_scenario_index` returns `terms`, and entries without a name are dropped.
- **Kept apart from the stat index.** The terms are stored in `indexes.json` and on `GroupState.scenario_glossary`, never in `scenario_npc_index`. Combat registration matches enemies against the NPC index, so it cannot pick up a person with no stats.
  - `scenario_library.load_context` passes the whole scenario's terms, not just the active chapter's, because a name stays fixed across chapters.
  - Upload, activation, repair and `/coc index` set the field.
  - The next chapter of the same scenario keeps a glossary that `/coc index` rebuilt when the library entry has none; another scenario starts empty.
  - A republish whose extraction found names but no stats keeps the entry's stat index and takes the new terms.
- **Prompt.** The index block gains 「其他專有名詞（只固定譯名；列在這裡不代表調查員已經知道）」, listed as 「・中文名（原文：…）」 and capped at `GLOSSARY_PROMPT_LIMIT` (80). The 「譯名」 rule now covers people, places and terms.
- **Narration.** The `scenario_names` reply step also reads the glossary, so an English original it lists, for example 「Necronomicon」, is written as its Chinese name.
- **Existing games.** A scenario indexed before this change has no glossary and behaves as before. Its terms appear after the next upload or `/coc index`.
- **Doc fix.** The character/dictionary spec claimed a `dict_` upload. No such route exists; the dictionary grows only from extraction and matching. The template-variant `glossary.json` written by `scenario_templates` is a review artifact for translated templates and is unrelated to this field.

## Verification

`tests/test_scenario_glossary.py`:

- Extraction keeps named terms only.
- The library returns the whole scenario's terms while the NPC index stays chapter-scoped.
- The field survives save and load.
- The prompt lists terms with the "not known to investigators" note, capped.
- A glossary original in the narration is written in Chinese.
- A rebuilt glossary survives the next chapter of the same scenario only.
- A names-only republish keeps the stat index and takes the new terms.
