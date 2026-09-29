# Dormant-enemy reveal beat before lethal resolution

[繁體中文](bug-combat-reveal-beat-skipped-before-lethal-damage_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `bug`. Status: **implemented**. Identified from a real production session (`main_v2`, conversation `ed20ad1edfb8`, 2026-09-28).

This edition describes the current contract. Proposed work is explicitly identified.

## Incident

An investigator drew a gun on an enemy the scenario documents as motionless and "reluctant to move at all unless threatened." The Keeper correctly recognized the drawn gun as a threat and started combat (`start_combat`, `add_npc_to_combat`) before the shot was even fired — that part of the existing combat-trigger contract worked as intended.

The shot was an Extreme Success. Two narration beats followed, roughly a minute apart:

1. Immediately after the roll: "槍聲大作，屍體仍伏在原處，煙霧尚未散盡" (the corpse is still lying there).
2. After the damage tools resolved (`roll_impaling_damage`, `damage_combatant`, `end_combat` — a real, correctly-computed kill): "他倒伏下去，再沒有動靜" (he collapsed, no more movement).

Both sentences describe an inert body. The scenario's own required beat — the enemy visibly rising/animating in response to the threat, which is also the exact moment the scenario ties its Sanity-roll trigger to — was never narrated. From the players' side, nothing appeared to change. Two players independently pushed back ("屍體應該要動啦", "屍體沒動，為什麼要SAN"), and the Keeper then re-ran the entire encounter instead of just narrating the beat it had skipped: a second `add_npc_to_combat` (a new enemy instance, distinct from the one already defeated), a second impaling-damage roll (23, versus the original 22), and several minutes of the Keeper self-correcting across multiple replies.

## Current contract

1. When a scenario documents a dormant enemy's activation trigger (rises, wakes, animates, "moves"), that beat must be narrated as part of the same resolution that first damages or defeats it — never a silent jump from "still motionless" straight to "defeated," which leaves players unable to tell anything happened.

2. When a player points out that an enemy or event should have reacted, check existing mechanical state (`get_combat_status`, `get_character_sheet`) for what already happened before re-running `start_combat`/`add_npc_to_combat`/damage tools — narrate the missed beat instead of re-resolving the same attack a second time under a new enemy instance.

This rule is written in English in the static prompt, unlike its ~30 sibling bullets (Traditional Chinese). It is a tool-calling instruction consumed only by the model, not player-facing narration, and measured ~32% fewer tokens than an equally-tightened Chinese version on this session's provider tokenizer — real savings since the static prompt is resent every turn. Being a brand-new, previously untuned bullet, there was no consistency cost against prior tuning to weigh against that.

## Flow and interfaces

```text
dormant enemy threatened -> combat starts -> lethal resolution narrates the rise/wake beat + the damage in the same breath
player flags a missing beat -> check current state -> backfill narration only, no duplicate re-resolution
```

## Implementation and verification

- [app/keeper.py](../../../app/keeper.py)
- [tests/test_combat_reveal_beat_before_lethal_damage.py](../../../tests/test_combat_reveal_beat_before_lethal_damage.py)

Related: [bug-combat-trigger-prompt-and-damage-tool-ambiguity.md](bug-combat-trigger-prompt-and-damage-tool-ambiguity.md) covers the adjacent, opposite failure mode (narrative suspicion incorrectly starting combat); this spec covers an already-correctly-triggered combat whose lethal resolution skipped a scenario-mandated narrative beat.
