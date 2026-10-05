# What a player reads when a turn cannot finish, how long a queue is acknowledged, and what a character is called

[繁體中文](player_facing_wording_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `f03a110`.

## Problem

Two real five-player runs (the Haunting at `b0e875c`, Dead Boarder at `d6f13c3`) show what players meet that is not a rules problem:

- 15 of 102 Dead Boarder turns (14.7%) ended in "this action is not fully processed"; 9 of those were `executor_no_action`, almost all a player walking somewhere or asking someone something the scenario says nothing about. The message told them to "make the action more specific", which blames the player when the scenario is silent. The turns that ended this way took the longest (p50 57.5 s against 43.0 s for the others).
- The queue notice stopped after about 50 s (three notices). In the same run a five-player burst waited up to 204 s, and the Haunting's up to 255 s, so the last 150-200 s had no signal at all.
- System lines name a pregenerated character by its registered English name ("The Tough Guy 的背包已確認…", 15 times in 102 turns) while the Narrator calls the same character "硬漢".

## Constraint: only wording and cadence

No judgement, tool, lock or timing of the turn changes. A turn that fell back still falls back for the same reason; only the sentence a player reads differs. Saved state, logs and ids never see an alias.

## Changes

- **`executor_no_action`** now reads: the scenario has too little to rule on this action and the Keeper did not invent it; ask about people, objects or places the scenario has already shown, or, if the action was vague, add the target and the way and try again. It names both causes because the reason code cannot tell them apart. It does not list what is available to look at: that needs the scene's known exits and objects, which is not plumbed into the fallback yet.
- **Queue notices** keep the 10 s first notice, then space the rest out with a growing interval (20, 30, 45, then 60 s) up to seven notices: 10, 30, 60, 105, 165, 225, 285 s. A typical wait of under a minute still sees at most three. Each notice re-reads the position, so the list is a countdown.
- **`CHARACTER_DISPLAY_ALIASES`** (a JSON object, empty by default): `presentation.player_text`, the last step over every public and private reply, writes each registered name as its alias (whole names only, longest first, inserted literally). With the default nothing changes. A value that is not a JSON object of text is ignored and reported at startup like the other bad settings. The setting is documented in `docs/guides/configuration_profiles.md` and `.env.example`, which also gain `LOCK_HELD_WARNING_SECONDS`, added earlier without a row.

## Not done

- Telling "the scenario is silent" from "the action was vague" would need the Executor to say which; until then the sentence covers both.
- The Narrator is not told the alias, so a character the Narrator names differently from the configured alias stays two names. A configured alias is meant to match what the Narrator already says.

## Verification

`tests/test_character_display_aliases.py` (default unchanged, a system line mapped, whole names only, longest first and idempotent, literal insertion, the stored name untouched, the setting's parsing and reporting, the fallback wording) and `tests/test_turn_queue_observability.py` (the interval schedule, recorded without a wall clock). `ruff check .`, `mypy app` and the full `pytest` pass. Nothing here was run against a real provider.
