# An enemy attacks through the combat engine, not through the old defense check

[繁體中文](enemy_attack_through_combat_engine_design_spec_zh.md) | [Docs index](../../README.md)

Category: `bug`. Status: **implemented**. Base: `main_v2` at `87413ae`.

## Problem

In a real *The Haunting* run, an enemy woke up and attacked. The Keeper called `offer_npc_attack_defense_choice` (the older NPC attack and defense check) first and only then started combat. The player chose to fight back, the attack hit, and the Keeper rolled the damage itself. Once combat was running, the system refused that damage, so the player was told they were hit while their HP did not change.

`offer_npc_attack_defense_choice` already refuses to run inside a managed combat. The problem is the order: the attack is resolved before the fight exists, so its damage has nowhere to go.

## Change

Wording only:

- The combat-start rule in `app/prompt_builder.py` now says to call `initialize_combat` before any enemy attack, that a started fight's enemy attacks only on its own turn through `plan_enemy_turn` and `run_enemy_combat_plan`, and never to open a fight with a stand-alone defense check or roll an enemy's damage. The prompt does not name the older tool on purpose: existing tests keep it out of the routing text so the model is not pointed at it.
- The `offer_npc_attack_defense_choice` tool description (`app/keeper_tools/registry.py`) says not to use it when combat has started or is about to.

## Not done

The tool and the legacy combat mode are not removed; saved battles from before the managed pipeline still need them, and removing them is a separate decision. The engine is unchanged.

## Tests

None: this is prompt wording. The check is a real run: an enemy that attacks on waking is registered with `initialize_combat` first and its damage lands on the target's HP.

## Follow-up: the two older tools are removed from the Keeper

With no saved battle from before the managed pipeline left to keep (confirmed by the project owner), the wording fix above is replaced by removal for the two tools that caused it:

- `offer_npc_attack_defense_choice` (the older NPC attack and defense check) and `close_legacy_combat` are no longer in the Keeper's tool list, and their handlers and the tests that only exercised them are deleted. The Keeper can no longer resolve an enemy attack before a fight exists, or try to close a legacy battle.
- `offer_check_choice`, `npc_skill_check`, `clear_pending_check` and the prompt no longer point at the removed tool. The older tool also dropped the Fight Back option against a Critical attacker; the managed combat's own defense choice now does the same (see [a Critical attack leaves the defender no Fight Back option](no_fight_back_against_critical_design_spec.md)). `offer_check_choice` also no longer takes `attacker_tier` (the second leg of the older two-call flow) and rejects it, so a defense choice against an NPC attack can only come from the combat engine.
- Kept: the dodge and fight-back machinery in the check engine (the managed combat's own defense choice uses it), and the legacy combat mode inside `combat.py` and `combat_engine.py`. Removing that mode touches roughly 250 to 300 lines that only the legacy mode uses in `combat.py` and `combat_engine.py`, plus many tests that build battles through that mode; it is a separate piece of work, scoped in a report.
