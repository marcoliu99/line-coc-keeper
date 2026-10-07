# The Keeper describes a place fully instead of one or two sentences

[繁體中文](fuller_keeper_narration_design_spec_zh.md) | [Docs index](../../README.md)

Category: `bug`. Status: **implemented**. Base: `main_v2` at `87413ae`.

## Problem

In a real *The Haunting* run, ordinary Keeper replies were 60–180 characters and many ended on "尚未判定", "尚未辨清" or "眼下還沒有確切線索" ("you step into the entrance hall … you cannot yet make out what is in the house; look further in"). The players were never told what the place holds, so they had nothing to act on and the game stalled.

The prompt caused it. The pacing rule said to give "one concrete reaction point and stop", and to treat a third paragraph as a sign to stop. Together with the rules against inventing facts and revealing the scenario early, the model treated the shortest reply that invents nothing as the safe one.

## Change

One prompt rule in `app/prompt_builder.py` (the pacing section shared by the Executor and the Narrator):

- A reply still advances one scene beat, but writes it out: usually two to four paragraphs, about 150–300 characters.
- When an investigator enters or looks at a place, describe what the scenario says is there (furnishings, objects, exits, sounds, smells, who is present) so the player has something to investigate.
- What the scenario says is visible on entering, or has already been found, is described directly; "not yet determined" is not the body of a reply. What needs a check, an investigation or a trigger to be found (hidden doors, concealed clues, hidden people or monsters) stays undisclosed. Details the scenario does not state are covered by atmosphere that adds no key clue, and the reply points at what the player can do next.
- Entering a new place may run longer than usual.

The spoiler, secrecy and "do not invent" rules are unchanged.

## Not done

No length limit enforced in code, no change to the fallback messages, no change to the location index or retrieval. If replies are still thin after this, the next suspect is retrieval (a scenario without a location index gives the Keeper less to describe), not the prompt.

## Tests

None: this is prompt wording. The check is a real run: the replies on entering a room should describe it.
