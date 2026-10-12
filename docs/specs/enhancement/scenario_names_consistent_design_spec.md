# A scenario's people and places keep one Chinese name

[繁體中文](scenario_names_consistent_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `7dc932c`.

## Problem

The scenario index (`/coc index`) already translates each NPC/monster and main location into Traditional Chinese and lists English originals as aliases. The Keeper and Narrator see the index, but nothing asked them to use its names. A name could therefore come back as the English original or a new translation from turn to turn.

Tools that keep long LLM translations consistent do two things. They give the model a fixed glossary, and they fix the names after generation, because a glossary in the prompt is followed most of the time but not every time.

## Change

- **Prompt rule.** When the index is present, the static prompt adds a 「譯名」 section. A person or place listed above is written with the Chinese name after 「・」, the same every time. Aliases and English originals only identify who is meant; they are not written into the narration, and no new translation is made. A person whose identity is not yet revealed is still called what the players know. A listed name that is still English (a map's location) gets one Chinese translation on first mention, used from then on.
- **Names fixed in what is delivered.** A new display-only reply-pipeline step, `scenario_names`, runs after `finalize` and before `player_text`, on the rendered reply, private messages and private controls, so result and clue lines that delivery appends read the same names as the narration. It calls `presentation.use_scenario_names` to write each English alias the index lists as its entry's Chinese name, for example 「Corbitt」 → 「柯比特」 and 「Corbitt's」 → 「柯比特的」.
  - The match is case-insensitive and whole-word, so 「Corbittson」 stays.
  - Only an alias of three letters or more that names one entry is used. An alias shared by two entries (「Deep One」 for 深潛者 and 深潛者（幼體）) is left alone.
  - Investigators' and pregens' names are never rewritten, and neither is an alias that is a word of one (「Carter」 in 「Malcolm Carter」).
  - No English word the index does not list is guessed at.
  - A Chinese name is written only once the table has been told it, in this reply or earlier public narration: an index name may be the identity behind a disguise (「Masked Man」 for 兇手約翰), and the rewrite must never be what reveals it.
  - A match with an English word right beside it is English the narration quotes (「Will he return?」), not the name, and is left as written.
  - Private messages get the same names as the public narration.
  - Like `player_text`, it only changes how a name is written; the facts delivery validated are unchanged. The rewritten public reply must still pass delivery's spoiler and hard-fact checks (`turn_delivery.rewrite_still_safe`); if it doesn't, the validated English spelling is shown.
- **Existing games.** Nothing else changes: combat, checks, saved state and the index itself are untouched. A game whose scenario is already indexed gets both changes on its next turn.

## Verification

`tests/test_scenario_names.py`:

- English originals and possessives become the index name.
- A longer word, a two-letter alias and an alias shared by two entries are left alone.
- An investigator's name is kept, and so is a name inside quoted English.
- A private message gets the same names.
- The prompt carries the rule only when an index exists.
- `scenario_names` is display-only and runs between `finalize` and `player_text`, covering appended lines and private controls.

`tests/test_reply_pipeline.py` lists the new step in the shipped order.
