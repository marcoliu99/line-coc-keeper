# The dictionary names the lore, learns it carefully, and can be corrected

[繁體中文](dictionary_terms_and_learning_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `9d9ce93`, on top of `coc7_glossary_design_spec.md` and `scenario_glossary_design_spec.md`.

## Problem

The built-in CoC7 glossary has a `terms` table (Mythos beings, tomes, places, rules), but nothing read it, so only skills used the glossary. The dictionary also learned from the model's own pairings with no checks: a wrong pair was saved for the whole deployment, outranked the built-in glossary, was copied together with the built-in tables into the database, and could not be seen or removed. A scenario's lore names (a cult, a book) were learned only inside that scenario.

## Change

- **The terms are used when a scenario is read.** `extract_scenario_index`:
  - adds to the model's instruction the glossary names the text uses, as 「・譯名（原文）」 (`dictionary.glossary_hits`, at most 60, in order of first use; a plural glossary spelling also matches the singular, so "Deep One" finds 深潛者), so NPC, location and term names start from the names the project already writes;
  - then gives each extracted term the dictionary's name for it (`lookup_term` on its name and every spelling). When its spellings point to different names, the model's choice stays.
- **Learning is guarded** (`dictionary._learn`, used by every `learn_*`):
  - A spelling is settled once. The built-in glossary wins, then what was learned first, so a name stays the same. A pair that disagrees is not saved; it is logged as `dictionary.learn.rejected`.
  - A built-in spelling wins over anything stored, including a pair an older save holds (the model's "Fast Talk" → 話術 no longer shadows 快速交談).
  - Only what was learned is saved. The built-in tables are no longer copied into the database, so a corrected glossary file takes effect on every deployment. Each learned spelling records its source (`pregen`, `match`, `scenario`) and time.
- **Lore is learned for every scenario** (`learn_terms`). A term in a scenario's name list whose kind is a book, spell, group or Mythos being is saved under its fullest English spelling (at least four letters; a short alias such as 「John」 could belong to anyone). A person or an item is the scenario's own and is not learned. Learned terms are used by the hints and by the name step above, like built-in ones.
- **KP view and correction.** `/coc dict` lists what was learned, newest first (25 shown) with its source and date. `/coc dict forget <原文或譯名>` removes an entry by spelling or by name. Both are KP-only: the list can name a scenario's monsters, and the dictionary is shared by every group, which the replies say. A built-in entry cannot be removed with a command (the reply says which file to edit). `sudo` cannot run it. A help entry and the generated command reference list it.

## Not changed

Narration is not rewritten from the built-in terms; only the scenario's own name list drives `scenario_names`. The occupation, skill and character-name learning calls are unchanged apart from the guards.

## Verification

`tests/test_dictionary_learning.py`:

- A built-in skill is not shadowed by a model pairing; an older save that does so is ignored.
- The first learned name stays; only learned entries are saved; each records source and time.
- `forget` removes by spelling or name, and never a built-in entry.
- Lore terms are learned under the fullest spelling; people and items are not; a built-in term is not replaced.
- Hits come back in order, once per name, whole words only, singular matching a plural spelling.
- The extraction instruction lists the names; a term gets the dictionary's name; conflicting spellings keep the model's choice; a second scenario reuses a learned name.
- `/coc dict` is KP-only, lists, forgets and explains a built-in entry.
