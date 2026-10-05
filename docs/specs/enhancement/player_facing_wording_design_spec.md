# What a player reads when a turn cannot finish and how long a queue is acknowledged

[繁體中文](player_facing_wording_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `f03a110`.

## Problem

Two real five-player runs (the Haunting at `b0e875c`, Dead Boarder at `d6f13c3`) show what players meet that is not a rules problem:

- 15 of 102 Dead Boarder turns (14.7%) ended in "this action is not fully processed"; 9 of those were `executor_no_action`, almost all a player walking somewhere or asking someone something the scenario says nothing about. The message told them to "make the action more specific", which blames the player when the scenario is silent. The turns that ended this way took the longest (p50 57.5 s against 43.0 s for the others).
- The queue notice stopped after about 50 s (three notices). In the same run a five-player burst waited up to 204 s, and the Haunting's up to 255 s, so the last 150-200 s had no signal at all.

## Constraint: only wording and cadence

No judgement, tool, lock or timing of the turn changes. A turn that fell back still falls back for the same reason; only the sentence a player reads differs.

## Changes

- **`executor_no_action`** now reads: the scenario has too little to rule on this action and the Keeper did not invent it; ask about people, objects or places the scenario has already shown, or, if the action was vague, add the target and the way and try again. It names both causes because the reason code cannot tell them apart. For the reasons that ask the player to try something else (`executor_no_action`, `unsupported_action`, `no_scenario_evidence`) it adds one line naming what the table has already been shown, "目前已在劇情中出現、可以接著問或查看的有——…": locations and people from the scenario indexes whose name or an alias has appeared in **public narration**, listed once per entry under **the spelling the narration used** (the most recently narrated one if it used several, so one character never reads as two people) (an entry narrated only by its alias is never printed under its canonical name, which may be the secret), most recent first, at most five of each; a player's own words and non-public narration do not count, and neither does narration or a clue stamped with another timeline: a new scenario keeps the log but starts a new timeline, so the old scenario's "地下室" cannot make a same-named, undisclosed entry of the new one look shown (log entries with no timeline stamp are not trusted once the state has a timeline; an unstamped clue counts as this timeline's, as elsewhere). A name that appears only inside a longer name the scenario also lists ("房東" inside the entry "房東太太") does not count, and an ASCII name must not touch other ASCII letters or digits; a longer phrase that no index lists cannot be told apart, so a short name can still match inside it, and up to three public clues, cut at 24 characters. It can therefore only repeat what players know and never names an unvisited place or an undisclosed character. No model call, no state change, and nothing is added when nothing has been shown yet.
- **Queue notices** keep the 10 s first notice, then space the rest out with a growing interval (20, 30, 45, then 60 s) up to seven notices: 10, 30, 60, 105, 165, 225, 285 s. A typical wait of under a minute still sees at most three. Each notice re-reads the position, so the list is a countdown.

## Not done

- Telling "the scenario is silent" from "the action was vague" would need the Executor to say which; until then the sentence covers both.
- A scenario index name that the Narrator translated differently (an English index entry the narration calls something else) does not match, so the line is shorter; it is never wrong. Objects the scenario has no index for are not listed, except as public clues.
- Bounded improvisation (letting the Keeper invent harmless scene dressing) was considered and is not done: the decision is to keep grounding and guide the player instead.

## Verification

`tests/test_scene_hints.py` (only what was narrated publicly is named, a player's own words and private narration do not count, aliases, recency and the limit, one-character names, public clues only and cut, which reasons get the line, the reply with and without state) and `tests/test_fallback_wording.py` (the fallback wording) and `tests/test_turn_queue_observability.py` (the interval schedule, recorded without a wall clock). `ruff check .`, `mypy app` and the full `pytest` pass. Nothing here was run against a real provider.
