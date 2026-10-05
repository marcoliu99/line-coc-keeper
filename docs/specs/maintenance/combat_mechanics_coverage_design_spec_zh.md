# Camp Sunny 長跑從未涵蓋的戰鬥與機制

[English](combat_mechanics_coverage_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與目標

分類：`maintenance`。狀態：**已實作（僅離線覆蓋）**。來源：Camp Sunny 修正規格第 9 節（「Combat and Missing Coverage」）。基於 `main_v2` 的 `aa79f22`。

那次 500 回合的執行從來沒有打過架、推過骰、開過槍或貫穿。第 9 節要求針對十一項機制做定向的真實執行覆蓋，每項都要檢查：對的擁有者、對的資源變化、待處理的生命週期、工具順序、沒有重複套用，以及敘事與狀態一致。這個環境無法做真實執行（沒有 Codex 登入、沒有 Discord），所以這裡補上最強的離線對應版本，並說明其餘已由哪些既有測試覆蓋。

## 新增內容

`tests/test_combat_mechanics_coverage.py`（15 項測試）在真實的 SQLite 狀態上驅動真實的工具、按鈕處理、檢定引擎與戰鬥引擎，只有骰子是腳本化的。每項測試在結果出來後都會再按一次玩家的按鈕，並斷言骰子、傷害與狀態都沒有變動。

## 第 9 節的覆蓋

| # | 機制 | 由哪些測試覆蓋 |
| --- | --- | --- |
| 1 | 玩家近戰攻擊 | **新增** `test_a_player_melee_hit_damages_the_enemy_once_…`、`test_a_player_miss_changes_no_hit_points`；既有 `test_public_check_button_saves_receipt_before_reply_…` |
| 2 | 防守方閃避 | **新增** `test_a_failed_dodge_costs_the_defender_…`、`test_a_successful_dodge_costs_nothing`、`test_only_the_defender_may_choose_how_to_defend`；既有 `test_npc_attack_owned_defense_choice_and_manual_roll_survive_reload` |
| 3 | 反擊 | **新增** `test_a_winning_fight_back_hurts_the_attacker_…`、`test_a_losing_fight_back_is_hit_for_the_normal_damage`；既有 `test_failed_attacker_can_be_hit_by_successful_fightback_normal_damage` |
| 4 | NPC 攻擊 | **新增**（NPC 攻擊是閃避與反擊測試的起點）；既有 `test_first_npc_can_run_source_bound_plan_…` |
| 5 | 武器傷害 | **新增**（近戰與射擊測試的傷害收據） |
| 6 | 貫穿 | **新增** `test_an_extreme_shot_impales_…`、`test_an_ordinary_hit_does_not_impale` |
| 7 | 彈藥 | **新增**（消耗一發、重播不再消耗、結算時才寫回）、`test_a_shot_with_no_range_needs_a_ruling_…`；既有 `test_single_shot_uses_owned_ammo_once_…` |
| 8 | 重傷 | **新增** `test_a_blow_of_half_her_hit_points_registers_one_con_check_…`、`test_a_blow_below_the_threshold_…`；既有 `test_major_wound_failed_con_sets_effective_unconscious_only` |
| 9 | 戰鬥結束 | **新增** `test_settling_commits_the_working_resources_once_…`；既有 `test_atomic_settlement_updates_both_mirrors_…` |
| 10 | 戰鬥外的推骰 | **新增** `test_a_failed_check_can_be_pushed_and_the_pushed_roll_is_final`（引擎讓推骰結果成為終局且不提供 Luck；不限制一個骰可以推幾次，那是 Keeper 的規則）；既有 `test_c10_a_pushed_roll_is_final_and_never_offers_luck`、`test_pushed_roll_never_offers_luck_buyup_…` |
| 11 | 由已結算結果觸發的後續傷害／檢定 | 既有 `test_resolved_damage_roll_and_hp_commit_are_one_idempotent_result`、`test_major_wound_damage_and_con_check_commit_together`、`test_successful_spot_hidden_creates_distinct_pending_dodge_…` |
| – | 確定性的結果文字與狀態一致 | **新增** `test_the_result_text_the_player_receives_matches_the_state_and_shows_no_raw_tier`（Keeper 的敘事沒有被執行：共用的測試框架把 `run_turn` 換成了固定回覆） |

## 未涵蓋

沒有真實模型、Discord 或 provider 參與：骰子是腳本化的，所以這些測試證明的是機制及其冪等性，並不證明真實的 Keeper 會依正確順序選對工具。「工具順序」是以引擎階段與待處理控制項的順序檢查，並非模型的工具計畫。連發、全自動、投擲武器與多敵人戰鬥只由既有測試涵蓋。這不代表戰鬥已通過規格中的真實執行門檻；那道門檻仍然未過。
