# Phase 3 結果：Combat Engine

分支 `refactor/phase3-combat-engine`，基底 `refactor/phase2-check-engine`（疊在第 2 階段之上）。規格：[`combat_engine_design_spec`](../specs/refactor/combat_engine_design_spec_zh.md)；需求：[架構重構規格](../specs/refactor/architecture_refactor_phases_1_4_design_spec_zh.md)；盤點：[migration-inventory](migration-inventory.md)。

## 1. 版本

| 項目 | 值 |
| --- | --- |
| Base SHA | `c57122f58e0d43c6ff6c810d9a48383b695b23d5`（第 2 階段 head；其下 `2affd06c9b90105d454d19c6eb4f7e7c1fb232f8` 為 `main_v2`） |
| 程式碼 commit | `0eab400246b6dfe46a74eed94ceeacf4a500f00f`（`Route every battle action through one combat engine`） |
| Result SHA | 本 PR 的 head commit；文件 commit 在 `0eab400` 之後 |
| 實際環境 | Python 3.13.14、pytest 9.1.1、ruff 0.16.8、mypy 2.3.1、SQLite 3.45.1、4 核 Linux |

## 2. 變更檔案與責任

| 檔案 | 責任 |
| --- | --- |
| `app/services/combat_engine.py`（新） | `CombatEngine.handle(state, action)`；`mode_of`（IDLE／LEGACY／MANAGED，每個 action 只讀一次）；35 個 handler；管理類命令的 `authorize`（必須指名目前戰鬥並給理由） |
| `app/services/combat_actions.py`（新） | 35 個不可變 action dataclass，型別參數即回傳型別；不帶骰值 |
| `app/combat.py` | 移除所有對 `combat_flow` 的依賴；依模式不同的步驟改由必填的 `ModeOps` 參數注入；`LegacyOps`／`LEGACY_OPS`；`ModeMismatch` 守門；`TimingBlocked`、`planned_damage` 等原本的私有輔助改為公開；移除不可達的舊程式碼 |
| `app/combat_flow.py` | 接收從 `combat.py` 搬來的 `apply_managed_damage`、`managed_single_hit`、`process_managed_timing`；`ManagedOps`／`MANAGED_OPS`；以 `combat_resources.is_managed` 取代 `combat.is_managed` |
| `app/combat_resources.py` | 唯一的 `is_managed` 定義；`admit_continuing_state`（自 `combat_flow` 下移）；`LEGACY_NEEDS_ADMISSION` 訊息常數 |
| `app/keeper_tools/{combat,managed_combat,resource_bridge}.py`、`app/commands/handlers/{combat,character}.py`、`app/services/managed_checks.py`、`app/keeper.py` | 全部改為呼叫引擎；`managed_combat` 的 `_battle` 檢查移進引擎的 `authorize` |
| `app/models.py` | `retire_character`／`retire_active_character` 改由呼叫端傳入 `finish_turn`，模型層不再匯入 combat |
| `tests/test_combat_engine.py`、`test_architecture_combat.py`、`combat_calls.py`；`scripts/experiments/benchmark_combat_engine.py` | 新測試、測試用呼叫包裝（依狀態選擇與引擎相同的 ops）、情境基準 |

## 3. Inventory 前後

見 [migration-inventory 第 3 節](migration-inventory.md)。重點：

* `combat` ↔ `combat_flow` 循環：5 條邊（4 處延遲＋1 處頂層）→ 0。匯入圖（含延遲匯入）上 `combat` 到達不了 `combat_flow` 與引擎；`combat_flow` 只到 `combat`；只有引擎匯入 `combat_flow`。
* `combat.py` 對 managed 旗標的讀取：15 → 2（`_require_legacy` 守門、`close_legacy_combat` 的明確關閉）。全 repo 的 `is_managed`／`.managed(`／`pipeline_version` 讀取點：52 → 42（`combat_flow` 的 18 處是「進行中的戰鬥 vs. 已結束戰鬥的義務」區分，不是模式分支，另加 1 處守門；本階段沒有動它們）。
* 「managed」謂詞：2 份獨立定義（`combat.is_managed`、`resource_bridge.managed`）→ 1 份（`combat_resources.is_managed`，`resource_bridge.managed` 只是委派）。
* 模型層向上依賴：`models → combat`（函式內匯入）→ 0。

## 4. 驗證

指令（乾淨 checkout、Python 3.13 venv）：

```text
ruff check .          # All checks passed
mypy app              # Success: no issues found in 146 source files
pytest                # 2202 passed, 1 skipped, 222 subtests passed（第 2 階段 2154 項 → +48）
```

唯一的 skip 同第 2 階段（`tests/test_codex_analysis_smoke.py`，需已登入的 Codex CLI），**未執行**。

驗收情境與對應測試（「既有」是第 3 階段之前就存在、本次只改接縫的測試）：

| ID | 新測試（`test_combat_engine.py`） | 既有測試 | 結果 |
| --- | --- | --- | --- |
| B1 近戰 Dodge／Fight Back，含平手 | 閃避平手歸防守方；反擊平手歸攻擊方；反擊勝過失敗的攻擊並傷到敵人 | `test_failed_attacker_can_be_hit_by_successful_fightback_normal_damage`、`test_dice_resolve_opposed` | 通過 |
| B2 遠程、彈藥、護甲 | 一發只花一發、重送不再花；空彈匣在擲骰前要求裁定；護甲扣減、`bypass_armor` 不扣、同一 entry id 不重複結算；故障仍花彈藥且無傷害 | `test_ranged_successful_dive_applies_penalty_instead_of_automatic_miss`、`test_single_shot_uses_owned_ammo_once_and_preserves_persistent_weapon_mapping`、`test_npc_ranged_trusted_range_difficulty_or_refusal_before_draw` | 通過 |
| B3 NPC 攻擊需玩家防禦 | 只有攻擊方擲骰、停在 `PLAYER_CHOICE`、HP 不變、沒有傷害事件、等待屬於被攻擊者 | `test_npc_attack_owned_defense_choice_and_manual_roll_survive_reload` | 通過 |
| B4 同按鈕連按 | 同一選擇送兩次回放原收據且不寫入；並行雙擊擲骰只擲一次、傷害與推進各一次 | `test_consumed_check_and_luck_buttons_replay_same_battle_receipts_without_mutation`、`test_owned_defense_choice_button_replays_saved_delivery_without_new_roll` | 通過 |
| B5 多 NPC、多人 | 2 名調查員＋2 隻敵人：只問被攻擊者；另一位玩家被拒絕且不擲骰、不能代選；扣值只落在被攻擊者 | `test_other_owner_check_click_cannot_draw_or_consume_player_control` | 通過 |
| B6 重傷／死亡／CON | 重傷 → `INJURY_CHECK` 與 CON 待處理，推進被擋，CON 完成後恢復；致命一擊不要求 CON；等待檢定時被打倒的調查員不能完成該攻擊 | `test_major_wound_con_gate`（16 處）、`test_structured_single_hit_injury_includes_zero`、`test_injury_ownership_block_is_all_or_nothing` | 通過 |
| B7 legacy／managed 存檔 | legacy：載入、查看、規劃、解決、推進；拒絕 managed action 且不被轉換；明確關閉（冪等）後可開新的 managed 戰鬥；managed：存檔後續玩；戰鬥已消失後殘留的控制被拒絕、不擲骰 | `test_legacy_active_history_requires_explicit_closure_without_guessed_baseline`、`test_legacy_admission_requires_explicit_pending_empty_closure` | 通過 |
| B8 舊工具與新入口混用 | 同一 `event_id` 先經 `adjust_character` 再經 `ApplyDamage` 只結算一次；原始傷害工具在戰鬥中全部被拒；重複宣告回傳原收據，不同內容為衝突 | `test_managed_repeatable_mutation_requires_explicit_identity`、`test_reserved_action_metadata_cannot_be_overwritten_by_tool_call` | 通過 |
| B9 commit／delivery 失敗 | 提交失敗：狀態與失敗前完全相同，重試只花一發彈藥、只有一筆彈藥事件 | `test_settlement_storage_failure_rolls_back_mirrors_and_is_retryable`、`test_lost_player_reply_replays_saved_roll_without_rng_or_resource_cost`、第 1 階段的失敗注入 | 通過 |
| B10 依賴與 import smoke | `test_architecture_combat.py`（18 項）：匯入圖上的分層、單一模式讀取、只有引擎匯入 `combat_flow`、8 個入口在全新 process 各自能 import | — | 通過 |

引擎契約測試：模式由戰鬥決定；對沒有戰鬥的對話執行 managed action 會拒絕（見第 5 節）；管理類命令必須指名目前戰鬥並給理由；規則拒絕另一種模式的 ops；未知 action 為 `TypeError`；每個 action 都有 handler。

### 時點行為矩陣

需求要求「若現況不一致，先建立行為矩陣」。逐一對照程式與測試後，**沒有發現不一致**，因此沒有行為修復，只補上原本沒有測試釘住的兩列：

| 時點 | 現有行為 | 釘住的測試 |
| --- | --- | --- |
| 宣告 | 驗證目前行動者可行動、目標存活且不是自己、沒有其他未完成 action、武器與來源可解析；不花任何資源；宣告寫成收據並跑到第一個等待 | `test_foreign_actor_and_stale_control_refuse_before_rng`、`test_b8_a_repeated_declaration_returns_the_stored_receipt` |
| 彈藥 | 每個 action 只花一次：攻防骰都已知之後、命中結算之前；花之前再核對，改變就 `NEEDS_RULING` 且不花；空彈匣在宣告時就要求裁定 | `test_b2_a_shot_spends_one_round_and_a_retry_spends_none`、`test_b2_an_empty_magazine_asks_for_a_ruling_before_any_roll`、`test_unsupported_or_unsourced_action_needs_ruling_before_rng_or_ammo` |
| 故障 | 骰值 ≥ 武器故障值：彈藥已花、不造成傷害、標記武器故障 | `test_b2_a_malfunction_still_costs_the_round_and_deals_no_damage`（**新增**） |
| 取消 | 控制者 `cancel` 裁定要明確理由與事件 id；移除該 action 的待處理檢定／Luck；標為完成＋取消；傷害已實體化則拒絕 | `test_unsupported_special_cancellation_preserves_evidence`、`test_unsourced_first_npc_ruling_can_be_cancelled_and_advanced_without_damage` |
| 更換武器 | 只在 `NEEDS_RULING` 的 `resume` 裁定重新解析；技能改變而已有玩家結果時要求先取消 | `test_source_ruling_resumes_unknown_weapon_without_arbitrary_damage`、`test_verified_npc_ruling_resumes_missing_damage_bonus_from_card` |
| 參與者倒下 | 非傷害檢定的控制在原參與者失去行動能力時被拒絕，要求明確 reconciliation，不擲骰 | `test_b6_an_investigator_put_down_while_an_attack_waits_for_the_roll_cannot_finish_it`（**新增**） |
| 過期防禦／殘留控制 | combat id、interaction id、持有人、phase 任一不符就拒絕；已消耗的控制重播原收據 | `test_foreign_actor_and_stale_control_refuse_before_rng`、`test_b7_a_control_left_over_from_a_battle_that_is_gone_is_refused_and_rolls_nothing` |

### 工具呼叫數與引擎額外成本

固定的情境（一名調查員對一隻教徒、近戰；先攻方出手後輪到敵人，敵人的反擊需要玩家閃避）只用公開的工具介面與玩家 `/coc check`，改前改後各跑 5 次（交替執行，同一台機器）：

| 項目 | 改前（第 2 階段 head） | 改後 |
| --- | --- | --- |
| Keeper 工具呼叫數 | 2（`declare_combat_action`、`advance_combat_turn`） | 2（相同） |
| 每次工具呼叫 median／p95 | 3.93–4.13 ms／4.75–5.27 ms | 3.90–4.09 ms／4.78–5.33 ms |
| 純分派成本（`engine.handle` 對直接呼叫 handler，20000 次） | — | median 0.37 µs 對 0.22 µs（約 +0.15 µs），p95 0.43 µs 對 0.27 µs |

**沒有減少工具呼叫數，也沒有可測得的延遲變化**：第 3 階段之前的 managed 流程已經把「宣告＋執行」合為一次 `declare_combat_action`、把「推進＋規劃＋執行敵人回合直到需要玩家」合為一次 `advance_combat_turn`，所以這段確定性連續段原本就是一次 domain action。本階段沒有新增聚合 tool。LLM round trip 沒有測量（需要真實模型）；工具呼叫數只是循序 round trip 的下界。上列延遲是本機單寫入者、腳本化骰子的數字，不是玩家端延遲。

## 5. 缺陷與衝突紀錄

| 類別 | 項目 |
| --- | --- |
| baseline 既有（本 PR 修復） | 對**沒有進行中戰鬥**的對話呼叫只屬於 managed 的工具（例如 `run_combat_action`、`advance_combat_turn`、`preview_combat_settlement`），舊程式會先 `initialize_working_state`，憑空建立一場空的 managed 戰鬥（`active=True`、有 `combat_id`），並由工具存檔留下。已在基底 head 實際重現；現在引擎對 IDLE 回傳 `{"ok": false, "error": "目前沒有進行中的戰鬥"}` 且不寫入（`test_the_tools_do_not_fabricate_a_battle_when_none_is_running`）。LEGACY 的行為不變（仍丟出 admission 錯誤） |
| baseline 既有（本 PR 修復） | `models.CombatState.retire_character` 在函式內匯入 `combat`（最低層向上依賴）；改由呼叫端傳入 `finish_turn` |
| baseline 既有（本 PR 清理） | `combat.end_combat` 在 `raise` 之後還有一段永遠到不了的報告程式碼，已刪除；`combat.apply_final_combat_damage` 只是 `apply_combat_damage(bypass_armor=True)` 的包裝，改為 `ApplyDamage(bypass_armor=True)`（工具名稱與輸出不變） |
| 未解決（沿用第 2 階段） | 戰鬥管轄的檢定骰仍直接用 `app.dice`（測試以 patch `app.dice` 腳本化），不是檢定引擎的 `DicePort` 實例；戰鬥傷害骰、反擊骰同理。同一個 Luck 政策衝突（需求說戰鬥檢定不可用 Luck，現況對攻擊／防禦擲骰提供）仍待產品決定 |
| 未解決（已記錄） | `combat_flow` 內仍有 18 處 `is_managed`：它們區分「進行中戰鬥的工作副本」與「已結束戰鬥的已提交義務」（例如 `postcombat_obligations`、`stabilize_investigator`、`stop_effect`），不是 legacy／managed 分支。要消除它們需要一個明確的「義務範圍」概念，超出本階段 |
| 未解決（已記錄） | legacy 戰鬥的 `resolve_enemy_action` 仍接受呼叫端給的命中／傷害結果（舊存檔專用）；managed 戰鬥一律拒絕 |
| 限制 | 沒有新增聚合 tool：既有 `declare_combat_action`／`advance_combat_turn` 已經是一次 domain action；多玩家同時等待的順序仍由既有的「一次一個等待」規則決定 |

## 6. 相容性與回退

* 舊存檔：不需遷移。legacy 與 managed 戰鬥、pending、Luck 決定、收據與結算結構全部不變；legacy 仍不被自動轉換。
* 工具名稱、schema、輸出、Discord custom id、指令文字不變。行為差異只有：對沒有戰鬥的對話呼叫 managed 專用工具現在乾淨地拒絕（見上）。
* `app.combat.apply_managed_damage`、`managed_single_hit`、`is_managed` 已搬走，repo 內沒有程式碼再從舊位置匯入；`combat.advance_turn`、`plan_enemy_turn`、`process_timing`、`apply_combat_damage`、`damage_combatant` 現在需要 `ops=` 參數，只有引擎與 `combat_flow` 會呼叫它們（`test_architecture_combat.py` 檢查）。
* 回退：還原本 PR 後，舊程式讀得懂所有現有資料。
* 部署：未實際部署，也未連真實 Discord 或付費 provider。

## 7. 未執行項目

* 真實 Discord 群組與付費 provider 的連線測試（規格禁止作為預設環境）；離線結果不代表線上驗證。
* LLM round trip 的實測（需要真實模型與玩家端量測）。
* 離線五人中文情境重播：留待第 4 階段完成後，與統一後的所有入口一起做。
* `tests/test_codex_analysis_smoke.py`（需已登入的 Codex CLI）。
