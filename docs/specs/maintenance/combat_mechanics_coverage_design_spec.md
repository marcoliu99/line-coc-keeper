# Combat and mechanics coverage the Camp Sunny soak never exercised

[繁體中文](combat_mechanics_coverage_design_spec_zh.md) | [Docs index](../../README.md)

## Status and goal

Category: `maintenance`. Status: **implemented (offline coverage only)**. Source: section 9 of the Camp Sunny fix specification ("Combat and Missing Coverage"). Based on `main_v2` at `aa79f22`.

The 500-turn run never fought, pushed a roll, fired a weapon or impaled. Section 9 asks for targeted real-runtime coverage of eleven mechanics, each checked for the right owner, the right resource change, the pending lifecycle, the tool sequence, no duplicate application and narration that matches the state. A real-runtime run was not possible here (no Codex login, no Discord), so this adds the strongest offline equivalent and says which existing tests already cover the rest.

## What was added

`tests/test_combat_mechanics_coverage.py` (15 tests) drives the real tools, button handlers, check engine and combat engine on a real SQLite state, with only the dice scripted. Every test replays the player's button after the result and asserts that the dice, the damage and the state do not move.

## Coverage of section 9

| # | Mechanic | Covered by |
| --- | --- | --- |
| 1 | player melee attack | **new** `test_a_player_melee_hit_damages_the_enemy_once_…`, `test_a_player_miss_changes_no_hit_points`; existing `test_public_check_button_saves_receipt_before_reply_…` |
| 2 | defender dodge | **new** `test_a_failed_dodge_costs_the_defender_…`, `test_a_successful_dodge_costs_nothing`, `test_only_the_defender_may_choose_how_to_defend`; existing `test_npc_attack_owned_defense_choice_and_manual_roll_survive_reload` |
| 3 | fight back | **new** `test_a_winning_fight_back_hurts_the_attacker_…`, `test_a_losing_fight_back_is_hit_for_the_normal_damage`; existing `test_failed_attacker_can_be_hit_by_successful_fightback_normal_damage` |
| 4 | NPC attack | **new** (the NPC attack opens the dodge and fight-back tests); existing `test_first_npc_can_run_source_bound_plan_…` |
| 5 | weapon damage | **new** (the damage receipt of the melee and shot tests) |
| 6 | impale | **new** `test_an_extreme_shot_impales_…`, `test_an_ordinary_hit_does_not_impale` |
| 7 | ammunition | **new** (one round spent, replay spends none, committed on settlement), `test_a_shot_with_no_range_needs_a_ruling_…`; existing `test_single_shot_uses_owned_ammo_once_…` |
| 8 | major wound | **new** `test_a_blow_of_half_her_hit_points_registers_one_con_check_…`, `test_a_blow_below_the_threshold_…`; existing `test_major_wound_failed_con_sets_effective_unconscious_only` |
| 9 | combat termination | **new** `test_settling_commits_the_working_resources_once_…`; existing `test_atomic_settlement_updates_both_mirrors_…` |
| 10 | push outside combat | **new** `test_a_failed_check_can_be_pushed_and_the_pushed_roll_is_final` (the engine makes a pushed result final and offers no Luck; it does not limit how many times a roll is pushed — that is the Keeper's rule); existing `test_c10_a_pushed_roll_is_final_and_never_offers_luck`, `test_pushed_roll_never_offers_luck_buyup_…` |
| 11 | follow-up damage or check from a resolved result | existing `test_resolved_damage_roll_and_hp_commit_are_one_idempotent_result`, `test_major_wound_damage_and_con_check_commit_together`, `test_successful_spot_hidden_creates_distinct_pending_dodge_…` |
| – | the deterministic result text matches the state | **new** `test_the_result_text_the_player_receives_matches_the_state_and_shows_no_raw_tier` (the Keeper's prose is not exercised: the shared harness stubs `run_turn`) |

## Not covered

No real model, Discord or provider was involved: the dice are scripted, so these tests prove the mechanics and their idempotency, not that a real Keeper chooses the right tool in the right order. "Tool sequence" is checked as the sequence of engine phases and pending controls, not as a model's tool plan. Burst fire, full-auto, thrown weapons and multi-enemy battles are covered by the existing suites only. This is not a claim that combat passed the real-runtime gate of the specification; that gate is still open.
