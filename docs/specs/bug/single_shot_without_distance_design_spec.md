# A shot with no stated distance paused the whole fight, a creature named in the players' words found no stat block, and the log did not say why a combat tool failed

[繁體中文](single_shot_without_distance_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `e4a9a1f`.

## Problem

In the 2026-10-11 soak runs on `d18fffd` (Camp Sunny rerun2, The Lightless Beacon rerun3) a fight stopped moving for several turns while every player read 「戰鬥暫停中，要等守密人裁定後才能繼續」 or 「處理這個行動的工具失敗了」. The reasons were in the text log, in the line each turn writes for its tool calls.

- **Camp Sunny turns 77 to 99.** Reidel's first shot was declared as a single shot with no distance. The engine answered with a ruling (`Single shot requires trusted physical distance and reviewed base range`), which sets the phase to `NEEDS_RULING` for the whole table. His next lines and Phil's were refused until the Keeper cleared it.
- **Lightless turns 31 to 43.** The Keeper asked for the stat block of 「幼體（深海夢魘）」. The scenario writes it as `Youngling, deep-sea nightmare`, the lookup found nothing, and the Keeper found the block only by asking again with the English heading. The fight that followed began with an enemy attack that lacked a scenario source, paused for a ruling, and every player's shot was refused (`Resolve the current action before declaring another`) until the Keeper cancelled it. Its two rulings before that failed on `Unknown weapon; explicit definition required`.
- **The log.** The structured `turn.observed` event carried only the tool name, so it could not show that a call failed. The reason was in the text log only in the line a turn writes when it finishes, and a turn that timed out never wrote it.

## Change

- **A shot with no stated distance is taken at the nearest range, and the fight goes on.** The hit roll is regular, whoever shoots and whatever the weapon's base range. The damage of a weapon with distance bands (a shotgun) is the nearest band. A stated distance is used as before; a distance past the weapon's extreme range, a distance with no base range to judge it by, and an invalid one still need a ruling. An enemy's shot with no distance is no longer a ruling either, but one with no ammunition still is. The rule lives in `combat_rules` (`resolve_range_difficulty`, `resolve_weapon_damage`), so the player's declaration, a resumed ruling and an enemy's planned shot share it.
- **An enemy attack with no scenario source is a melee blow.** `run_enemy_plan` paused the fight on `NPC attack requires verified scenario provenance` when the card had no source, and no resume argument could settle it: the paused action carries no weapon, so a resume looks up an empty weapon name and fails with `Unknown weapon; explicit definition required`, and only a cancel works (Lightless turns 38 to 43). An attack with no source now plays as a melee blow with the values the card carries, the extreme-success rule `maximum`, and is defended like any blow. A card with a source is unchanged, including a single shot with no ammunition, which still needs a ruling.
- **An enemy's special ability the engine cannot play is given up and narrated.** `run_enemy_plan` paused the fight on `NPC special/movement plan requires an explicit ruling`. The enemy's turn is now taken as a skip: it is narrated from the plan's public hint, no mechanic applies (a poison needle's effect is not applied; only the narration remains), and the fight goes on. The receipt says so (`special_given_up`, `narration`, and a note that an effect, if stated, is applied with the owned tools). A replay returns the same receipt.
- **A creature named in the players' words finds its stat block.** `get_enemy_stat_block` matched the scenario's NPC index by exact name or alias only. It now also accepts the same words in another order or bracketing (the table's trailing letter or number ignored), so 「幼體（深海夢魘）」 reaches an entry 「深海夢魘（幼體）」 that lists the English heading as an alias. It never matches by similarity: 「深潛者」 is close to 「深潛者（幼體）」 and is a different creature, and the wrong block would hand the Keeper another creature's HP and attacks. When two entries have the same words the name is ambiguous and finds nothing. A name matching nothing still fails and lists the headings.
- **A failed tool call says so when it fails.** `turn.observed` now carries `ok`, and for a failed call the phase it left the fight in. The reason (first 300 characters of the tool's error) goes to the text log as one warning line, `tool <name> failed: <error>`, because the structured events carry no free-form text.

## Not changing

- **The rulings that remain are the Keeper's to make.** A distance past extreme range, a distance with no base range, an invalid distance, and an enemy shot with no ammunition each still pause the fight for the Keeper's judgement. The Keeper's context carries every unfinished action with the reason it waits (`needs_ruling` in `keeper_only_combat.actions`), so the Keeper reads what it is being asked to rule on; a test pins that for an enemy with no ammunition.
- **The Keeper declaring a second attack for an actor who has already acted** (Camp Sunny, three times) is the Keeper's error and is unchanged.

## Verification

- A shot with no distance is a regular roll at any base range, including none; a shotgun with no distance uses its nearest band; an invalid or conflicting distance, and a distance with no base range, still need a ruling (`tests/test_combat_rules.py`).
- A player's single shot with no distance is declared and rolls at `regular`; one with `distance_yards=60` does not; one with `distance_yards=1000` still needs a ruling and spends no ammunition (`tests/test_combat_state_machine_integration.py`, `tests/test_combat_mechanics_coverage.py`). An enemy's single shot with no distance is declared and waits for the player's defence (`tests/test_combat_flow.py`).
- The ruling test that relied on a missing distance now uses a distance past extreme range and still checks that the owned weapon key survives a reload.
- An enemy whose turn is a special ability is given up with a narration and no dice, replays to the same receipt, and can be advanced (`tests/test_combat_state_machine_integration.py`).
- An enemy with no source attacks as a melee blow at the start of the fight and waits for the player's defence, with no ruling pending (`tests/test_combat_state_machine_integration.py`, `tests/test_enemy_source_from_index.py`); the skip-a-turn test now makes the next enemy's turn need a ruling by an empty gun (`tests/test_combat_skip_turn.py`).
- 「幼體（深海夢魘）」, 「幼體（深海夢魘）A」, 「深海夢魘（幼體）」 and the English heading all return the Youngling block; an unrelated name, a close but different one (「深潛者」, 「深潛者頭目」) and an ambiguous one still fail (`tests/test_combat_turn_friction.py`).
- A failed tool call logs `ok` and `phase` in the structured event, with no error text, and one text-log warning with the reason (`tests/test_turn_phases.py`).
