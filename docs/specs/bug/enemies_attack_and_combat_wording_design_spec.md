# Enemies attack, the narrator is told the blow, and players read no bookkeeping

[繁體中文](enemies_attack_and_combat_wording_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `4e63718`.

## Problem

From the 2026-10-11 soak runs:

- **Bosses never struck.** Haunting rerun3: Corbitt played three turns and opened no defence; nobody lost HP. Camp Sunny rerun4: Billy the same. `_plan_enemy_turn` picks a special ability before an attack whenever its trigger matches, and the default trigger matches every turn. `run_enemy_plan` has no rule for such an ability, so it only narrates it and spends no usage, and the same ability is picked again next turn.
- **Narration contradicted the attack just settled.** Haunting rerun3, five of eight combat turns: 「你的拳頭尚未揮出」 beside 「⚔️ 命中，受到 3 點傷害」. The narrator sees the battle after the turn moved on; the blow was only inside a raw receipt dump.
- **Bookkeeping in the player's text.** 「【戰鬥暫定；尚未結算】」 on 8 replies, 「（戰鬥尚未結算）」 on 13 roll follow-ups, and 「【戰鬥機械結果暫定；尚未結算】」 under combat rolls, including after a blow that ended the fight.
- **An empty gun refused without saying so.** Lightless rerun3 turn 79: the player read 「要攻擊的話，請指名上面列出的敵人」 though the enemy was named and the revolver was empty.
- **A roll on another investigator thrown away.** Camp Sunny rerun4 turn 62: one player's turn rolled another investigator's repair (the player asked for it), then failed validation as having no effect; the dice were spent and the player asked to say it again.

## Change

- **An ability the engine only tells is told once.** When planning, an ability already given up this combat by the same enemy is passed over if the enemy has an attack that can reach; the enemy attacks. An enemy with no attack still uses it. The auto-advance summary of an enemy turn carries the given-up ability's narration, and the resolved-roll follow-up tells the narrator that the enemy did that instead of attacking.
- **The narrator gets the blow as a fact.** `build_mechanic_facts_block` adds 「【本回合已結算的攻擊：已經發生】」 with each public settled-attack line from `declare_combat_action` / `run_combat_action` / `submit_combat_choice` this turn (a replay is already silenced to internal) and: write the blow first, then who is next; never write it as not yet struck.
- **No bookkeeping in player text.** `observe_tool` no longer appends the provisional mark; `finalize` no longer adds 「（戰鬥尚未結算）」 to the blow; combat roll lines drop the status suffix (the Keeper message keeps it). Status sheets still show provisional HP.
- **The empty gun is named.** When the asking player is the current actor, the combat guidance adds 「.38 Revolver 已經沒有子彈，可以改用徒手或其他武器。」 for each of their guns at 0 rounds in the battle's working resources.
- **A roll made this turn counts.** `validate_resolution` accepts a resolved roll from this turn's tools as the turn's effect whoever's skill it was.

## Not changing

- No rules for scenario special abilities; the Keeper can still apply a stated effect with the owned tools.
- No phrase matching on narration; the fact block is structured from the tool outcomes.

## Verification

`tests/test_enemies_attack_and_combat_wording.py`: told once then attacks; no attack, still the ability; the attack fact block and its absence for a replay, refusal or other tool; no bookkeeping on HP or attack lines; the empty gun named only at 0 rounds. Updated: `tests/test_combat_turn_friction.py`, `tests/test_combat_wiring.py`, `tests/test_turn_consistency_handoff.py`.
