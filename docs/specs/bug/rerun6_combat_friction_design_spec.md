# Three combat-turn failures from one Codex run: the brawl skill as a weapon, a retry that re-read, an owned gun called ambiguous

[繁體中文](rerun6_combat_friction_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `8bcf892`.

## Problem

The four-investigator Haunting run on `8bcf892` (Codex `gpt-6-luna`, 200 turns, 2026-10-10) had 9 fallback turns. Six of them came from three causes in code:

| Turns | What the player read | Cause |
|---|---|---|
| 37, 38, 52 | 「處理這個行動的工具失敗了」 | The player said 「我以格鬥（鬥毆）攻擊」 and the Keeper passed the skill name 「格鬥（鬥毆）」 as `weapon_reference`. The unarmed entry knew 「徒手」, 「拳頭」 and `brawl` but not the skill's own name, so `declare_combat_action` stopped on "Unknown weapon; explicit definition required" and the battle waited in `NEEDS_RULING`. Turn 38 then re-declared under the same action id with another weapon and was refused. |
| 29 | 「系統發生內部錯誤」 | The Executor's first attempt changed nothing and returned `incomplete`, so the turn was retried. The retry's first request read the enemy's stat block with the same arguments as the first attempt, and `codex_provider` refused it as `codex_duplicate_tool_attempt`: its record of calls spans the whole turn, retry included. It was 12.9 s into the request, nowhere near a timeout. |
| 28 | 「處理這個行動的工具失敗了」 | The Keeper looked up `.38 Revolver` with `get_weapon_definition`. The lookup read only the generic catalog, which has several revolvers and no .38, and returned "Ambiguous weapon reference", although Evelyn carries the sheet's own `.38 Revolver` (`pregen_weapon_stats_design_spec`) and `declare_combat_action` resolves it. The refusal put its reason in `reason`, not `error`, so the tool summary read 「失敗：未知錯誤」. |

## Change

- **The brawl skill is an unarmed blow.** The `i.weapon.brawl` catalog entry gains the aliases 「格鬥（鬥毆）」, 「格鬥(鬥毆)」, 「鬥毆」, 「空手」 and `fist`. 「格鬥」 alone is not added: by containment it would take 「格鬥刀」. Every existing catalog name and alias still resolves to its own weapon (a test walks the whole catalog). The catalog version is unchanged: no rule value moved.
- **A turn's retry may read again.** In `codex_provider.run_conversation` a look-up (`INFORMATION_QUERY_TOOLS`: the `get_*` queries, `search_scenario`, `search_memory`) already made in an earlier conversation of the same turn is sent again. The supervisor retries a turn only when its first attempt touched no game state, and a look-up changes nothing, so this replays nothing. Inside one conversation the same look-up is still refused, and a roll or mutation is never sent twice in a turn.
- **The weapon lookup reads the investigator's own weapons first.** `get_weapon_definition` takes an optional `investigator`; without it, the acting player's character is used. A weapon that character carries is resolved the way `declare_combat_action` resolves it (sheet definition, pinned instance), and the result says `owned: true`. Otherwise the generic catalog is read as before. A refusal now carries `error`: 「查不到確定的武器「左輪」（Ambiguous weapon reference）；候選：…。調查員身上的武器請填 investigator；徒手攻擊填「徒手」。」

## Not changing

- Turn 71: the Keeper answered the public-1D100 refusal (`d100_against_a_characteristic_is_a_check_design_spec`) by rolling the same 1D100 with `secret: true`, which is allowed, and still had no check to settle. Once in 200 turns, and a Keeper needs hidden rolls; left as is.
- The duplicate rule for rolls and mutations, and the turn-wide tool budget.

## Verification

- `tests/test_combat_rules.py`: the six spellings of the brawl skill resolve to `i.weapon.brawl`; 「格鬥刀」 does not; every catalog name and alias resolves to its own weapon.
- `tests/test_codex_provider.py`: a second conversation in the same turn may repeat a look-up; a repeated mutation in the turn and a repeated look-up inside one conversation are still refused.
- `tests/test_combat_wiring.py`: the sheet's `.38 左輪` is found by `get_weapon_definition` by name and by 「左輪」 with `investigator`; a lookup for someone carrying none reports the candidates in `error`.
