# 文件索引

來源修復操作指南：[繁體中文](guides/scenario_source_review_zh.md) / [English](guides/scenario_source_review.md)

[English](README.md)

現行規格已對照 `main_v2` 的 `afe8ace`；每份 spec 均有英文與繁中版本。歷史提案與量測以不可變來源版本連結保留。
後續新增項目各自標示來源版本；回合正確性與延遲提案對照 PR #94 後的 `8e32683`。

## 缺陷修正

| 規格 | 狀態 | 語言 |
| --- | --- | --- |
| [外部整備數值校對](specs/bug/authoring_numeric_review_design_spec_zh.md) | 已實作；來源仍需校對 | [English](specs/bug/authoring_numeric_review_design_spec.md) |
| [待處理檢定的所有權與重複防護](specs/bug/bugfix_duplicate_pending_checks_zh.md) | 已實作 | [English](specs/bug/bugfix_duplicate_pending_checks.md) |
| [劇本生命週期 review 修正](specs/bug/project_review_fixes_design_spec_zh.md) | 已實作 | [English](specs/bug/project_review_fixes_design_spec.md) |
| [檢定敘事與權威狀態一致](specs/bug/wood_wall_check_state_design_spec_zh.md) | 已實作 | [English](specs/bug/wood_wall_check_state_design_spec.md) |
| [戰鬥卡欄位的防禦式解析](specs/bug/bug-add-npc-to-combat-armor-schema-crash_zh.md) | 已實作 | [English](specs/bug/bug-add-npc-to-combat-armor-schema-crash.md) |
| [效果與參戰者的防禦式反序列化](specs/bug/bug-effectstate-combatant-missing-defensive-parsing_zh.md) | 已實作 | [English](specs/bug/bug-effectstate-combatant-missing-defensive-parsing.md) |
| [區分 NPC 個體與限制工具對話](specs/bug/bug-add-npc-to-combat-duplicate-name-guard_zh.md) | 已實作 | [English](specs/bug/bug-add-npc-to-combat-duplicate-name-guard.md) |
| [如實回報重傷檢定狀態](specs/bug/bug-adjust-character-false-major-wound-check_zh.md) | 已實作 | [English](specs/bug/bug-adjust-character-false-major-wound-check.md) |
| [進入戰鬥與傷害工具契約](specs/bug/bug-combat-trigger-prompt-and-damage-tool-ambiguity_zh.md) | 已實作 | [English](specs/bug/bug-combat-trigger-prompt-and-damage-tool-ambiguity.md) |
| [沉睡敵人的甦醒節拍在致命結算前被跳過](specs/bug/bug-combat-reveal-beat-skipped-before-lethal-damage_zh.md) | 已實作 | [English](specs/bug/bug-combat-reveal-beat-skipped-before-lethal-damage.md) |
| [戰鬥開始後漏登記已在場敵人](specs/bug/bug-active-enemy-registration-after-combat-start_zh.md) | 待實作 | [English](specs/bug/bug-active-enemy-registration-after-combat-start.md) |
| [持續效果傷害與幸運數值隔離](specs/bug/bug-continuing-damage-rolls-corrupt-luck-stat_zh.md) | 已實作 | [English](specs/bug/bug-continuing-damage-rolls-corrupt-luck-stat.md) |
| [無歧義的參戰者定位](specs/bug/bug-find-combatant-substring-collision_zh.md) | 已實作 | [English](specs/bug/bug-find-combatant-substring-collision.md) |
| [待處理 Luck 阻擋衝突的手動檢定](specs/bug/bug-luck-decision-not-checked-non-autoroll_zh.md) | 已實作 | [English](specs/bug/bug-luck-decision-not-checked-non-autoroll.md) |
| [依 RAG 與角色提供 Executor 工具](specs/bug/bug-executor-tool-list-missing-search-scenario_zh.md) | 已實作 | [English](specs/bug/bug-executor-tool-list-missing-search-scenario.md) |
| [移除舊 Keeper 迴圈後的輸出保護](specs/bug/bug-legacy-run-turn-missing-guard_zh.md) | 已由後續設計取代 | [English](specs/bug/bug-legacy-run-turn-missing-guard.md) |
| [Provider 格式錯誤與部分失敗處理](specs/bug/bug-provider-tool-call-unhandled-exceptions_zh.md) | 已實作 | [English](specs/bug/bug-provider-tool-call-unhandled-exceptions.md) |
| [檢定與 Luck 按鈕的冪等交付](specs/bug/bug-duplicate-luck-button-prompt_zh.md) | 已實作 | [English](specs/bug/bug-duplicate-luck-button-prompt.md) |
| [Narrator 指示必須符合檢定狀態](specs/bug/bug-narrator-mechanic-check-consistency_zh.md) | 已實作 | [English](specs/bug/bug-narrator-mechanic-check-consistency.md) |
| [持久化已結算檢定脈絡](specs/bug/bug-resolved-check-outcome-context-design-spec_zh.md) | 已實作 | [English](specs/bug/bug-resolved-check-outcome-context-design-spec.md) |
| [以目前事件完整搜尋劇本](specs/bug/bug-search-scenario-fragmented-queries_zh.md) | 已實作 | [English](specs/bug/bug-search-scenario-fragmented-queries.md) |
| [明確取消登記錯誤的檢定](specs/bug/bug-self-corrected-check-leaves-stale-pending_zh.md) | 已實作 | [English](specs/bug/bug-self-corrected-check-leaves-stale-pending.md) |
| [近戰防禦與遠程攻擊結算](specs/bug/bug-dodge-counter-tie-and-ranged-mechanics_zh.md) | 已實作 | [English](specs/bug/bug-dodge-counter-tie-and-ranged-mechanics.md) |
| [狀態版本與時間線隔離](specs/bug/state-loss-amnesia-hardening_design_spec_zh.md) | 已實作 | [English](specs/bug/state-loss-amnesia-hardening_design_spec.md) |
| [權威回合狀態交接](specs/bug/log_backed_turn_consistency_design_spec_zh.md) | 已實作 | [English](specs/bug/log_backed_turn_consistency_design_spec.md) |
| [原子敘事購買與取得來源](specs/bug/purchase_turn_provenance_design_spec_zh.md) | 已實作 | [English](specs/bug/purchase_turn_provenance_design_spec.md) |
| [Codex OAuth 對話 Provider](specs/enhancement/codex_oauth_provider_design_spec_zh.md) | 實驗分支已實作 | [English](specs/enhancement/codex_oauth_provider_design_spec.md) |
| [劇本整備最多匯出三個檔案](specs/enhancement/three_file_scenario_export_design_spec_zh.md) | 已實作 | [English](specs/enhancement/three_file_scenario_export_design_spec.md) |
| [面向玩家的呈現：等級、內部識別碼、隊伍人數](specs/bug/player_facing_presentation_design_spec_zh.md) | 已實作 | [English](specs/bug/player_facing_presentation_design_spec.md) |
| [有界的長期記憶 embedding](specs/bug/memory_embedding_bounds_design_spec_zh.md) | 已實作 | [English](specs/bug/memory_embedding_bounds_design_spec.md) |
| [回合 fallback 原因與有界復原](specs/bug/turn_fallback_reasons_design_spec_zh.md) | 已實作 | [English](specs/bug/turn_fallback_reasons_design_spec.md) |
| [劇本事件在同一回合的機械義務](specs/bug/event_obligations_design_spec_zh.md) | 已實作 | [English](specs/bug/event_obligations_design_spec.md) |
| [相鄰劇本觸發的檢索](specs/bug/adjacent_scenario_trigger_design_spec_zh.md) | 已實作 | [English](specs/bug/adjacent_scenario_trigger_design_spec.md) |

## 功能強化

| 規格 | 狀態 | 語言 |
| --- | --- | --- |
| [外部 AI 英文劇本來源整備](specs/enhancement/external_english_source_preparation_design_spec_zh.md) | 已於功能分支實作 | [English](specs/enhancement/external_english_source_preparation_design_spec.md) |
| [使用最終合併角色卡的可驗證 Luck](specs/enhancement/pregen_sheet_luck_design_spec_zh.md) | 已實作 | [English](specs/enhancement/pregen_sheet_luck_design_spec.md) |
| [可執行的 Help 控制項](specs/enhancement/actionable_help_buttons_design_spec_zh.md) | 已實作 | [English](specs/enhancement/actionable_help_buttons_design_spec.md) |
| [分類 Help 導覽](specs/enhancement/enhanced_help_navigation_design_spec_zh.md) | 已實作 | [English](specs/enhancement/enhanced_help_navigation_design_spec.md) |
| [重用足夠的主動劇本依據](specs/enhancement/executor_reuse_existing_rag_design_spec_zh.md) | 已實作 | [English](specs/enhancement/executor_reuse_existing_rag_design_spec.md) |
| [戰鬥快照重用與批次搜尋提案](specs/enhancement/enhancement-batch-scenario-search-and-combat-snapshot_zh.md) | 部分實作 | [English](specs/enhancement/enhancement-batch-scenario-search-and-combat-snapshot.md) |
| [對話排隊與受限工具迭代](specs/enhancement/enhancement-conversation-lock-and-tool-loop-latency_zh.md) | 已實作 | [English](specs/enhancement/enhancement-conversation-lock-and-tool-loop-latency.md) |
| [記錄實際交付的 Discord 回覆](specs/enhancement/enhancement-discord-reply-text-logging_zh.md) | 已實作 | [English](specs/enhancement/enhancement-discord-reply-text-logging.md) |
| [確定性驗證與可選敘事修復](specs/enhancement/enhancement-guard-agent_zh.md) | 已實作 | [English](specs/enhancement/enhancement-guard-agent.md) |
| [共用重試與請求准入](specs/enhancement/enhancement-llm-rate-limit-and-turn-latency_zh.md) | 已實作 | [English](specs/enhancement/enhancement-llm-rate-limit-and-turn-latency.md) |
| [提供所有合法且有效的 Luck 升級](specs/enhancement/enhancement-luck-buyup-always-offered_zh.md) | 已實作 | [English](specs/enhancement/enhancement-luck-buyup-always-offered.md) |
| [停止 Bot 後封存 log 與 profile](specs/enhancement/enhancement-archive-log-on-stop_zh.md) | 已實作 | [English](specs/enhancement/enhancement-archive-log-on-stop.md) |
| [歷史 Executor 分級與延後工具範圍設計](specs/enhancement/enhancement-executor-model-tiering-and-tool-scoping_zh.md) | 已由後續設計取代 | [English](specs/enhancement/enhancement-executor-model-tiering-and-tool-scoping.md) |
| [輸入預算、自適應准入與截斷](specs/enhancement/token_admission_evaluation_design_spec_zh.md) | 已實作 | [English](specs/enhancement/token_admission_evaluation_design_spec.md) |
| [NPC 攻擊延遲與確定性後果](specs/enhancement/npc_attack_latency_design_spec_zh.md) | 已實作 | [English](specs/enhancement/npc_attack_latency_design_spec.md) |
| [劇本正典邊界與持久更正](specs/enhancement/narrative_boundaries_design_spec_zh.md) | 已實作 | [English](specs/enhancement/narrative_boundaries_design_spec.md) |
| [保留證據的 PDF 解析與局部修復](specs/enhancement/pdf_parse_quality_design_spec_zh.md) | 已實作 | [English](specs/enhancement/pdf_parse_quality_design_spec.md) |
| [外部預製中文優先檢索](specs/enhancement/scenario_templates_design_spec_zh.md) | 已實作 | [English](specs/enhancement/scenario_templates_design_spec.md) |
| [獨立劇透與隱私政策](specs/enhancement/spoiler-protection-hardening_design_spec_zh.md) | 已實作 | [English](specs/enhancement/spoiler-protection-hardening_design_spec.md) |
| [待處理按鈕交付與劇本檢索延遲](specs/enhancement/turn_latency_design_spec_zh.md) | 已實作 | [English](specs/enhancement/turn_latency_design_spec.md) |
| [Executor 動態工具範圍](specs/enhancement/enhancement-executor-dynamic-tool-scoping_zh.md) | 待實作提案 | [English](specs/enhancement/enhancement-executor-dynamic-tool-scoping.md) |
| [批次戰鬥初始化提案](specs/enhancement/enhancement-macro-combat-initialization-tool_zh.md) | 待實作提案 | [English](specs/enhancement/enhancement-macro-combat-initialization-tool.md) |
| [持續戰鬥效果的推理設定提案](specs/enhancement/enhancement-executor-reasoning-effort-for-combat-ongoing-effects_zh.md) | 待實作提案 | [English](specs/enhancement/enhancement-executor-reasoning-effort-for-combat-ongoing-effects.md) |
| [外部 AI 中文模板整備與匯入診斷](specs/enhancement/external_template_authoring_design_spec_zh.md) | 已實作；實測待完成 | [English](specs/enhancement/external_template_authoring_design_spec.md) |
| [回合階段計時與檢索放大](specs/enhancement/turn_latency_instrumentation_design_spec_zh.md) | 已實作 | [English](specs/enhancement/turn_latency_instrumentation_design_spec.md) |

## 功能

| 規格 | 狀態 | 語言 |
| --- | --- | --- |
| [玩家擁有的確定性檢定](specs/feature/keeper-deterministic-check-resolution_design_spec_zh.md) | 已實作 | [English](specs/feature/keeper-deterministic-check-resolution_design_spec.md) |
| [預製角色幸運值所有權](specs/feature/pregen_luck_roll_design_spec_zh.md) | 已實作 | [English](specs/feature/pregen_luck_roll_design_spec.md) |
| [跨遊戲保存手動角色卡](specs/feature/manual_pregen_persistence_design_spec_zh.md) | 已實作 | [English](specs/feature/manual_pregen_persistence_design_spec.md) |
| [本地 Discord Bot 生命週期](specs/feature/bot_lifecycle_scripts_design_spec_zh.md) | 已實作 | [English](specs/feature/bot_lifecycle_scripts_design_spec.md) |
| [角色匯入、協調與字典](specs/feature/character_and_dictionary_system_spec_zh.md) | 已實作 | [English](specs/feature/character_and_dictionary_system_spec.md) |
| [戰鬥卡、效果與權威傷害](specs/feature/combat_design_spec_zh.md) | 已實作 | [English](specs/feature/combat_design_spec.md) |
| [KP Assistant 代玩家操作](specs/feature/kp_assistant_sudo_control_design_spec_zh.md) | 已實作 | [English](specs/feature/kp_assistant_sudo_control_design_spec.md) |
| [可重用劇本庫與章節脈絡](specs/feature/scenario_library_design_spec_zh.md) | 已實作 | [English](specs/feature/scenario_library_design_spec.md) |
| [持久狀態、checkpoint 與範圍記憶](specs/feature/state_persistence_design_spec_zh.md) | 已實作 | [English](specs/feature/state_persistence_design_spec.md) |
| [結構化請求與回合可觀測性](specs/feature/structured_performance_logging_design_spec_zh.md) | 已實作 | [English](specs/feature/structured_performance_logging_design_spec.md) |

## 重構

| 規格 | 狀態 | 語言 |
| --- | --- | --- |
| [架構重構（第 1–4 階段）](specs/refactor/architecture_refactor_phases_1_4_design_spec_zh.md) | 已實作 | [English](specs/refactor/architecture_refactor_phases_1_4_design_spec.md) |
| [遊戲狀態交易](specs/refactor/state_transaction_design_spec_zh.md) | 已實作 | [English](specs/refactor/state_transaction_design_spec.md) |
| [檢定引擎](specs/refactor/check_engine_design_spec_zh.md) | 已實作 | [English](specs/refactor/check_engine_design_spec.md) |
| [戰鬥引擎](specs/refactor/combat_engine_design_spec_zh.md) | 已實作 | [English](specs/refactor/combat_engine_design_spec.md) |
| [移除 legacy_commands](specs/refactor/legacy_commands_retirement_design_spec_zh.md) | 已實作 | [English](specs/refactor/legacy_commands_retirement_design_spec.md) |
| [回合交接契約](specs/refactor/turn_payload_contract_design_spec_zh.md) | 已實作 | [English](specs/refactor/turn_payload_contract_design_spec.md) |
| [更正生命週期](specs/refactor/correction_lifecycle_design_spec_zh.md) | 已實作 | [English](specs/refactor/correction_lifecycle_design_spec.md) |
| [劇本來源與模板版本儲存](specs/refactor/scenario_source_store_design_spec_zh.md) | 已實作 | [English](specs/refactor/scenario_source_store_design_spec.md) |
| [每個戰鬥工具只有一個分派分支](specs/refactor/dead_combat_tool_branches_design_spec_zh.md) | 已實作 | [English](specs/refactor/dead_combat_tool_branches_design_spec.md) |
| [移除未使用的舊指令分派器](specs/refactor/bug-remove-dead-legacy-coc-command-handler_zh.md) | 已實作 | [English](specs/refactor/bug-remove-dead-legacy-coc-command-handler.md) |
| [統一玩家回合流程與獨立 KP Assistant](specs/refactor/unified_keeper_turn_flow_design_spec_zh.md) | 已實作 | [English](specs/refactor/unified_keeper_turn_flow_design_spec.md) |
| [Agentic Keeper 架構](specs/refactor/agentic_keeper_design_spec_zh.md) | 已實作 | [English](specs/refactor/agentic_keeper_design_spec.md) |
| [原生非同步 provider 與 I/O 契約](specs/refactor/async_provider_performance_design_spec_zh.md) | 已實作 | [English](specs/refactor/async_provider_performance_design_spec.md) |
| [歷史 v1.0 後整合紀錄](specs/refactor/main_post_v1.0_into_main_v2_design_spec_zh.md) | 歷史紀錄 | [English](specs/refactor/main_post_v1.0_into_main_v2_design_spec.md) |
| [回合正確性、結果恢復與延遲量測](specs/refactor/turn_safety_and_latency_design_spec_zh.md) | 部分完成；S0／S1／S3 已實作，S2 已撤回 | [English](specs/refactor/turn_safety_and_latency_design_spec.md) |
| [依職責拆分 app/keeper.py](specs/refactor/keeper_module_split_design_spec_zh.md) | 已實作 | [English](specs/refactor/keeper_module_split_design_spec.md) |
| [拆分 app/discord_bot.py](specs/refactor/discord_transport_split_design_spec_zh.md) | 已實作 | [English](specs/refactor/discord_transport_split_design_spec.md) |

## 維護

| 規格 | 狀態 | 語言 |
| --- | --- | --- |
| [雙語規格對齊與分類](specs/maintenance/documentation_alignment_design_spec_zh.md) | 已實作 | [English](specs/maintenance/documentation_alignment_design_spec.md) |
| [Camp Sunny 長跑從未涵蓋的戰鬥與機制](specs/maintenance/combat_mechanics_coverage_design_spec_zh.md) | 已實作 | [English](specs/maintenance/combat_mechanics_coverage_design_spec.md) |

## References / 參考

- [API](references/API_zh.md)
- [carry_audit](references/carry_audit_zh.md)
- [gameplay_style](references/gameplay_style_zh.md)
- [keeper_skill](references/keeper_skill_zh.md)
- [log_backed_turn_consistency_source_review](references/log_backed_turn_consistency_source_review_zh.md)
- [player_command_reference](references/player_command_reference_zh.md)
- [prep_persistence](references/prep_persistence_zh.md)
- [role_card_template](references/role_card_template_zh.md)
- [rules_reference](references/rules_reference_zh.md)
- [scenario_template_reference_review](references/scenario_template_reference_review_zh.md)
- [scenario_zh_external_preparation](references/scenario_zh_external_preparation_zh.md)

## Guides / 指南

- [setup](guides/setup_zh.md)
- [gameplay](guides/gameplay_zh.md)

## Historical records / 歷史紀錄

- [Changelog](changelog.md)
- [Raw evaluations](evaluations)
- [Camp Sunny 驗證：根因](validation/camp_sunny_fix_root_cause.md)、[定向回歸](validation/camp_sunny_targeted_regression.md)、[延遲前後](validation/camp_sunny_latency_before_after.md)、[發現清單](validation/camp_sunny_findings.json)（真實執行的階段尚未進行）
- [Machine-readable spec catalog](specs/catalog.json)
