# An enemy fights with its stat block's attacks, and a declaration paused on a weapon name can be corrected

[繁體中文](enemy_block_attacks_weapon_retry_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `b0c11f3`.

## Problem

From the 2026-10-12 soak runs at b0c11f3:

- **An enemy that never struck.** Camp Sunny rerun6: Billy (Fighting 70%, damage 8D6, written in his stat block) was registered with no attack the engine could play, most likely with only his Engulf ability. That passes the registration check, but his turns were only narrated and given up, so in the round that was played he never struck.
- **A weapon name paused the battle.** Lightless rerun3 turn 50: Beatrice's Brawl was declared under a name `resolve_weapon` did not match. That put the fight in NEEDS_RULING before any die. The model then found the weapon, but nothing could be declared while the ruling stood, and the player read 「這次行動尚未完整處理…工具失敗了」.

## Change

- **Attacks from the block.** When `initialize_combat` or `add_npc_to_combat` registers a non-ally enemy with a named ability but no well-formed attack (skill value and damage), `support.attacks_from_stat_block` reads the melee attacks its scenario stat block writes as `<name> NN% … damage <dice>` and registers those (a gun, bow or thrown attack is left out: read as a blow it would be fought back against and spend no ammunition).
  - A `DB` in the damage is the block's own DB line. An attack whose bonus the block doesn't give is left out.
  - Dodge is not an attack.
  - Attacks the Keeper did give are kept as given. An entry with nothing at all is still refused as before, so the Keeper registers how the scenario says the enemy attacks (Corbitt's floating knife, not his Fighting line). The block may write the bonus in place (`damage bonus(1D4)`, 「傷害加值（-1）」), and a difficulty annotation such as `(Hard 25%/Extreme10%)` is skipped.
- **A corrected declaration replaces the paused one.** An unknown weapon still pauses for a ruling, because that is how a scenario-only weapon gets its definition. But when the same actor declares again while the only open action is that actor's attack, paused for a ruling before any die, the paused action is withdrawn and the new declaration proceeds. This works with the same action id or a new one. If the correction is itself refused, even after it was recorded (a check the actor still owes), the actions are put back as they were and the ruling stands. A ruling the Keeper still wants to make goes through `resolve_combat_ruling` as before.

## Verification

`tests/test_combat_state_machine_integration.py`:
- an enemy registered with an ability alone gets its block's Fighting 70% / 8D6 attack;
- a declaration paused on an unknown weapon name gives way to the corrected one, with the same action id or a new one, with nothing rolled.
