# Documentation

Source repair guide: [English](guides/scenario_source_review.md) / [繁體中文](guides/scenario_source_review_zh.md)

[繁體中文](README_zh.md)

Current specifications audited against `main_v2` at `afe8ace`. Every spec has an English and Traditional Chinese edition. Historical proposals and measurements remain linked to their immutable source revisions.
Later entries record their own source revisions; the proposed turn safety and latency review uses `8e32683` (after PR #94).

## bug

| Spec | Status | Language |
| --- | --- | --- |
| [External authoring numeric review](specs/bug/authoring_numeric_review_design_spec.md) | implemented; source review required | [繁體中文](specs/bug/authoring_numeric_review_design_spec_zh.md) |
| [Pending-check ownership and duplicate protection](specs/bug/bugfix_duplicate_pending_checks.md) | implemented | [繁體中文](specs/bug/bugfix_duplicate_pending_checks_zh.md) |
| [Scenario lifecycle review fixes](specs/bug/project_review_fixes_design_spec.md) | implemented | [繁體中文](specs/bug/project_review_fixes_design_spec_zh.md) |
| [Keep check narration consistent with authoritative state](specs/bug/wood_wall_check_state_design_spec.md) | implemented | [繁體中文](specs/bug/wood_wall_check_state_design_spec_zh.md) |
| [Defensive combat-card parsing](specs/bug/bug-add-npc-to-combat-armor-schema-crash.md) | implemented | [繁體中文](specs/bug/bug-add-npc-to-combat-armor-schema-crash_zh.md) |
| [Defensive effect and combatant deserialization](specs/bug/bug-effectstate-combatant-missing-defensive-parsing.md) | implemented | [繁體中文](specs/bug/bug-effectstate-combatant-missing-defensive-parsing_zh.md) |
| [Distinct NPC instances and bounded tool conversations](specs/bug/bug-add-npc-to-combat-duplicate-name-guard.md) | implemented | [繁體中文](specs/bug/bug-add-npc-to-combat-duplicate-name-guard_zh.md) |
| [Truthful major-wound check status](specs/bug/bug-adjust-character-false-major-wound-check.md) | implemented | [繁體中文](specs/bug/bug-adjust-character-false-major-wound-check_zh.md) |
| [Combat entry and damage-tool contracts](specs/bug/bug-combat-trigger-prompt-and-damage-tool-ambiguity.md) | implemented | [繁體中文](specs/bug/bug-combat-trigger-prompt-and-damage-tool-ambiguity_zh.md) |
| [Dormant-enemy reveal beat before lethal resolution](specs/bug/bug-combat-reveal-beat-skipped-before-lethal-damage.md) | implemented | [繁體中文](specs/bug/bug-combat-reveal-beat-skipped-before-lethal-damage_zh.md) |
| [Active enemies omitted after combat starts](specs/bug/bug-active-enemy-registration-after-combat-start.md) | backlog | [繁體中文](specs/bug/bug-active-enemy-registration-after-combat-start_zh.md) |
| [Keep continuing-effect damage separate from Luck](specs/bug/bug-continuing-damage-rolls-corrupt-luck-stat.md) | implemented | [繁體中文](specs/bug/bug-continuing-damage-rolls-corrupt-luck-stat_zh.md) |
| [Unambiguous combatant targeting](specs/bug/bug-find-combatant-substring-collision.md) | implemented | [繁體中文](specs/bug/bug-find-combatant-substring-collision_zh.md) |
| [Pending Luck blocks conflicting manual checks](specs/bug/bug-luck-decision-not-checked-non-autoroll.md) | implemented | [繁體中文](specs/bug/bug-luck-decision-not-checked-non-autoroll_zh.md) |
| [RAG-aware role-specific Executor tools](specs/bug/bug-executor-tool-list-missing-search-scenario.md) | implemented | [繁體中文](specs/bug/bug-executor-tool-list-missing-search-scenario_zh.md) |
| [Output protection after removal of the legacy Keeper loop](specs/bug/bug-legacy-run-turn-missing-guard.md) | superseded | [繁體中文](specs/bug/bug-legacy-run-turn-missing-guard_zh.md) |
| [Malformed provider calls and partial-failure handling](specs/bug/bug-provider-tool-call-unhandled-exceptions.md) | implemented | [繁體中文](specs/bug/bug-provider-tool-call-unhandled-exceptions_zh.md) |
| [Idempotent check and Luck button delivery](specs/bug/bug-duplicate-luck-button-prompt.md) | implemented | [繁體中文](specs/bug/bug-duplicate-luck-button-prompt_zh.md) |
| [Narrator instructions must match check state](specs/bug/bug-narrator-mechanic-check-consistency.md) | implemented | [繁體中文](specs/bug/bug-narrator-mechanic-check-consistency_zh.md) |
| [Durable resolved-check outcome context](specs/bug/bug-resolved-check-outcome-context-design-spec.md) | implemented | [繁體中文](specs/bug/bug-resolved-check-outcome-context-design-spec_zh.md) |
| [Search for a complete current event](specs/bug/bug-search-scenario-fragmented-queries.md) | implemented | [繁體中文](specs/bug/bug-search-scenario-fragmented-queries_zh.md) |
| [Explicit cancellation of an incorrectly registered check](specs/bug/bug-self-corrected-check-leaves-stale-pending.md) | implemented | [繁體中文](specs/bug/bug-self-corrected-check-leaves-stale-pending_zh.md) |
| [Melee defense and ranged attack resolution](specs/bug/bug-dodge-counter-tie-and-ranged-mechanics.md) | implemented | [繁體中文](specs/bug/bug-dodge-counter-tie-and-ranged-mechanics_zh.md) |
| [State revision and timeline isolation](specs/bug/state-loss-amnesia-hardening_design_spec.md) | implemented | [繁體中文](specs/bug/state-loss-amnesia-hardening_design_spec_zh.md) |
| [Authoritative turn-state handoffs](specs/bug/log_backed_turn_consistency_design_spec.md) | implemented | [繁體中文](specs/bug/log_backed_turn_consistency_design_spec_zh.md) |
| [Atomic narrative purchases and acquisition provenance](specs/bug/purchase_turn_provenance_design_spec.md) | implemented | [繁體中文](specs/bug/purchase_turn_provenance_design_spec_zh.md) |
| [Player-facing presentation: tiers, internal ids, party size](specs/bug/player_facing_presentation_design_spec.md) | implemented | [繁體中文](specs/bug/player_facing_presentation_design_spec_zh.md) |
| [Bounded long-term memory embeddings](specs/bug/memory_embedding_bounds_design_spec.md) | implemented | [繁體中文](specs/bug/memory_embedding_bounds_design_spec_zh.md) |
| [Turn fallback reasons and bounded recovery](specs/bug/turn_fallback_reasons_design_spec.md) | implemented | [繁體中文](specs/bug/turn_fallback_reasons_design_spec_zh.md) |
| [Same-turn mechanical obligations of a scenario event](specs/bug/event_obligations_design_spec.md) | implemented | [繁體中文](specs/bug/event_obligations_design_spec_zh.md) |
| [Adjacent scenario trigger retrieval](specs/bug/adjacent_scenario_trigger_design_spec.md) | implemented | [繁體中文](specs/bug/adjacent_scenario_trigger_design_spec_zh.md) |

## enhancement

| Spec | Status | Language |
| --- | --- | --- |
| [Codex OAuth conversation provider](specs/enhancement/codex_oauth_provider_design_spec.md) | implemented on experiment branch | [繁體中文](specs/enhancement/codex_oauth_provider_design_spec_zh.md) |
| [External AI preparation of English scenario sources](specs/enhancement/external_english_source_preparation_design_spec.md) | implemented on feature branch | [繁體中文](specs/enhancement/external_english_source_preparation_design_spec_zh.md) |
| [Verified Luck from the final merged role card](specs/enhancement/pregen_sheet_luck_design_spec.md) | implemented | [繁體中文](specs/enhancement/pregen_sheet_luck_design_spec_zh.md) |
| [Executable Help controls](specs/enhancement/actionable_help_buttons_design_spec.md) | implemented | [繁體中文](specs/enhancement/actionable_help_buttons_design_spec_zh.md) |
| [Categorized Help navigation](specs/enhancement/enhanced_help_navigation_design_spec.md) | implemented | [繁體中文](specs/enhancement/enhanced_help_navigation_design_spec_zh.md) |
| [Reuse sufficient proactive scenario evidence](specs/enhancement/executor_reuse_existing_rag_design_spec.md) | implemented | [繁體中文](specs/enhancement/executor_reuse_existing_rag_design_spec_zh.md) |
| [Combat snapshot reuse and proposed batch search](specs/enhancement/enhancement-batch-scenario-search-and-combat-snapshot.md) | partial | [繁體中文](specs/enhancement/enhancement-batch-scenario-search-and-combat-snapshot_zh.md) |
| [Conversation queuing and bounded tool iteration](specs/enhancement/enhancement-conversation-lock-and-tool-loop-latency.md) | implemented | [繁體中文](specs/enhancement/enhancement-conversation-lock-and-tool-loop-latency_zh.md) |
| [Logging delivered Discord replies](specs/enhancement/enhancement-discord-reply-text-logging.md) | implemented | [繁體中文](specs/enhancement/enhancement-discord-reply-text-logging_zh.md) |
| [Optional narrative repair with deterministic validation](specs/enhancement/enhancement-guard-agent.md) | implemented | [繁體中文](specs/enhancement/enhancement-guard-agent_zh.md) |
| [Shared retry and request admission](specs/enhancement/enhancement-llm-rate-limit-and-turn-latency.md) | implemented | [繁體中文](specs/enhancement/enhancement-llm-rate-limit-and-turn-latency_zh.md) |
| [Offer every legal useful Luck upgrade](specs/enhancement/enhancement-luck-buyup-always-offered.md) | implemented | [繁體中文](specs/enhancement/enhancement-luck-buyup-always-offered_zh.md) |
| [Archive logs and profiles after stopping the bot](specs/enhancement/enhancement-archive-log-on-stop.md) | implemented | [繁體中文](specs/enhancement/enhancement-archive-log-on-stop_zh.md) |
| [Historical Executor tiering and deferred tool scoping](specs/enhancement/enhancement-executor-model-tiering-and-tool-scoping.md) | superseded | [繁體中文](specs/enhancement/enhancement-executor-model-tiering-and-tool-scoping_zh.md) |
| [Input budgets, adaptive admission and truncation](specs/enhancement/token_admission_evaluation_design_spec.md) | implemented | [繁體中文](specs/enhancement/token_admission_evaluation_design_spec_zh.md) |
| [NPC attack latency and deterministic consequences](specs/enhancement/npc_attack_latency_design_spec.md) | implemented | [繁體中文](specs/enhancement/npc_attack_latency_design_spec_zh.md) |
| [Scenario canon boundaries and durable corrections](specs/enhancement/narrative_boundaries_design_spec.md) | implemented | [繁體中文](specs/enhancement/narrative_boundaries_design_spec_zh.md) |
| [Evidence-preserving PDF extraction and targeted repair](specs/enhancement/pdf_parse_quality_design_spec.md) | implemented | [繁體中文](specs/enhancement/pdf_parse_quality_design_spec_zh.md) |
| [Externally prepared Chinese-first retrieval](specs/enhancement/scenario_templates_design_spec.md) | implemented | [繁體中文](specs/enhancement/scenario_templates_design_spec_zh.md) |
| [Independent spoiler and privacy policy](specs/enhancement/spoiler-protection-hardening_design_spec.md) | implemented | [繁體中文](specs/enhancement/spoiler-protection-hardening_design_spec_zh.md) |
| [Pending-button delivery and scenario retrieval latency](specs/enhancement/turn_latency_design_spec.md) | implemented | [繁體中文](specs/enhancement/turn_latency_design_spec_zh.md) |
| [Dynamic Executor tool scoping](specs/enhancement/enhancement-executor-dynamic-tool-scoping.md) | backlog | [繁體中文](specs/enhancement/enhancement-executor-dynamic-tool-scoping_zh.md) |
| [Proposed batch combat initialization](specs/enhancement/enhancement-macro-combat-initialization-tool.md) | backlog | [繁體中文](specs/enhancement/enhancement-macro-combat-initialization-tool_zh.md) |
| [Proposed reasoning policy for ongoing combat effects](specs/enhancement/enhancement-executor-reasoning-effort-for-combat-ongoing-effects.md) | backlog | [繁體中文](specs/enhancement/enhancement-executor-reasoning-effort-for-combat-ongoing-effects_zh.md) |
| [External AI template authoring and import diagnostics](specs/enhancement/external_template_authoring_design_spec.md) | implemented; live trials pending | [繁體中文](specs/enhancement/external_template_authoring_design_spec_zh.md) |
| [At most three scenario authoring files](specs/enhancement/three_file_scenario_export_design_spec.md) | implemented | [繁體中文](specs/enhancement/three_file_scenario_export_design_spec_zh.md) |
| [Turn phase timeline and retrieval amplification](specs/enhancement/turn_latency_instrumentation_design_spec.md) | implemented | [繁體中文](specs/enhancement/turn_latency_instrumentation_design_spec_zh.md) |
| [One summary line per turn, whatever the logging settings](specs/enhancement/turn_summary_log_design_spec.md) | implemented | [繁體中文](specs/enhancement/turn_summary_log_design_spec_zh.md) |
| [Counting the turns that did not finish, from the turn summary line](specs/enhancement/turn_summary_report_design_spec.md) | implemented | [繁體中文](specs/enhancement/turn_summary_report_design_spec_zh.md) |
| [Keeping the scenario search from being starved, and counting Chinese text honestly without a tokenizer](specs/enhancement/retrieval_budget_headroom_design_spec.md) | implemented | [繁體中文](specs/enhancement/retrieval_budget_headroom_design_spec_zh.md) |
| [What a player reads when a turn cannot finish, how long a queue is acknowledged, and what a character is called](specs/enhancement/player_facing_wording_design_spec.md) | implemented | [繁體中文](specs/enhancement/player_facing_wording_design_spec_zh.md) |
| [An empty internal id label must not reach a player, and removing one must not eat the sentence](specs/enhancement/internal_id_display_design_spec.md) | implemented | [繁體中文](specs/enhancement/internal_id_display_design_spec_zh.md) |
| [Handing an item to another investigator is one committed step, and an item has one holder](specs/bug/inventory_transfer_design_spec.md) | backlog | [繁體中文](specs/bug/inventory_transfer_design_spec_zh.md) |

## feature

| Spec | Status | Language |
| --- | --- | --- |
| [Player-owned deterministic checks](specs/feature/keeper-deterministic-check-resolution_design_spec.md) | implemented | [繁體中文](specs/feature/keeper-deterministic-check-resolution_design_spec_zh.md) |
| [Pregenerated-character Luck ownership](specs/feature/pregen_luck_roll_design_spec.md) | implemented | [繁體中文](specs/feature/pregen_luck_roll_design_spec_zh.md) |
| [Manual role cards across games](specs/feature/manual_pregen_persistence_design_spec.md) | implemented | [繁體中文](specs/feature/manual_pregen_persistence_design_spec_zh.md) |
| [Local Discord bot lifecycle](specs/feature/bot_lifecycle_scripts_design_spec.md) | implemented | [繁體中文](specs/feature/bot_lifecycle_scripts_design_spec_zh.md) |
| [Character imports, reconciliation and dictionary](specs/feature/character_and_dictionary_system_spec.md) | implemented | [繁體中文](specs/feature/character_and_dictionary_system_spec_zh.md) |
| [Combat cards, effects and authoritative damage](specs/feature/combat_design_spec.md) | implemented | [繁體中文](specs/feature/combat_design_spec_zh.md) |
| [KP Assistant delegated player actions](specs/feature/kp_assistant_sudo_control_design_spec.md) | implemented | [繁體中文](specs/feature/kp_assistant_sudo_control_design_spec_zh.md) |
| [Reusable scenario library and chapter context](specs/feature/scenario_library_design_spec.md) | implemented | [繁體中文](specs/feature/scenario_library_design_spec_zh.md) |
| [Durable state, checkpoints and scoped memory](specs/feature/state_persistence_design_spec.md) | implemented | [繁體中文](specs/feature/state_persistence_design_spec_zh.md) |
| [Structured request and turn observability](specs/feature/structured_performance_logging_design_spec.md) | implemented | [繁體中文](specs/feature/structured_performance_logging_design_spec_zh.md) |

## refactor

| Spec | Status | Language |
| --- | --- | --- |
| [Architecture refactor, phases 1–4](specs/refactor/architecture_refactor_phases_1_4_design_spec.md) | implemented | [繁體中文](specs/refactor/architecture_refactor_phases_1_4_design_spec_zh.md) |
| [Game-state transaction](specs/refactor/state_transaction_design_spec.md) | implemented | [繁體中文](specs/refactor/state_transaction_design_spec_zh.md) |
| [Check engine](specs/refactor/check_engine_design_spec.md) | implemented | [繁體中文](specs/refactor/check_engine_design_spec_zh.md) |
| [Combat engine](specs/refactor/combat_engine_design_spec.md) | implemented | [繁體中文](specs/refactor/combat_engine_design_spec_zh.md) |
| [Retiring legacy_commands](specs/refactor/legacy_commands_retirement_design_spec.md) | implemented | [繁體中文](specs/refactor/legacy_commands_retirement_design_spec_zh.md) |
| [Turn payload contract](specs/refactor/turn_payload_contract_design_spec.md) | implemented | [繁體中文](specs/refactor/turn_payload_contract_design_spec_zh.md) |
| [Correction lifecycle](specs/refactor/correction_lifecycle_design_spec.md) | implemented | [繁體中文](specs/refactor/correction_lifecycle_design_spec_zh.md) |
| [Scenario source and variant store](specs/refactor/scenario_source_store_design_spec.md) | implemented | [繁體中文](specs/refactor/scenario_source_store_design_spec_zh.md) |
| [Single dispatch branch for each combat tool](specs/refactor/dead_combat_tool_branches_design_spec.md) | implemented | [繁體中文](specs/refactor/dead_combat_tool_branches_design_spec_zh.md) |
| [Remove the unused legacy command dispatcher](specs/refactor/bug-remove-dead-legacy-coc-command-handler.md) | implemented | [繁體中文](specs/refactor/bug-remove-dead-legacy-coc-command-handler_zh.md) |
| [Unified player-turn flow and independent KP Assistant](specs/refactor/unified_keeper_turn_flow_design_spec.md) | implemented | [繁體中文](specs/refactor/unified_keeper_turn_flow_design_spec_zh.md) |
| [Agentic Keeper architecture](specs/refactor/agentic_keeper_design_spec.md) | implemented | [繁體中文](specs/refactor/agentic_keeper_design_spec_zh.md) |
| [Native asynchronous provider and I/O contracts](specs/refactor/async_provider_performance_design_spec.md) | implemented | [繁體中文](specs/refactor/async_provider_performance_design_spec_zh.md) |
| [Historical post-v1.0 integration ledger](specs/refactor/main_post_v1.0_into_main_v2_design_spec.md) | historical | [繁體中文](specs/refactor/main_post_v1.0_into_main_v2_design_spec_zh.md) |
| [Turn safety, recoverable results and measured latency](specs/refactor/turn_safety_and_latency_design_spec.md) | partial; S0/S1/S3 implemented, S2 reverted | [繁體中文](specs/refactor/turn_safety_and_latency_design_spec_zh.md) |
| [Splitting app/keeper.py by responsibility](specs/refactor/keeper_module_split_design_spec.md) | implemented | [繁體中文](specs/refactor/keeper_module_split_design_spec_zh.md) |
| [Splitting app/discord_bot.py](specs/refactor/discord_transport_split_design_spec.md) | implemented | [繁體中文](specs/refactor/discord_transport_split_design_spec_zh.md) |
| [Splitting supervisor.run_turn and ordering the reply steps](specs/refactor/reply_pipeline_design_spec.md) | implemented | [繁體中文](specs/refactor/reply_pipeline_design_spec_zh.md) |
| [/coc system subcommands as a table of handlers](specs/refactor/system_command_table_design_spec.md) | implemented | [繁體中文](specs/refactor/system_command_table_design_spec_zh.md) |
| [One home for a turn's locks, an order test, and a held-too-long report](specs/refactor/turn_scope_design_spec.md) | implemented | [繁體中文](specs/refactor/turn_scope_design_spec_zh.md) |

## maintenance

| Spec | Status | Language |
| --- | --- | --- |
| [Bilingual specification alignment](specs/maintenance/documentation_alignment_design_spec.md) | implemented | [繁體中文](specs/maintenance/documentation_alignment_design_spec_zh.md) |
| [Combat and mechanics coverage the Camp Sunny soak never exercised](specs/maintenance/combat_mechanics_coverage_design_spec.md) | implemented | [繁體中文](specs/maintenance/combat_mechanics_coverage_design_spec_zh.md) |
| [Reading boolean settings one way](specs/maintenance/boolean_settings_design_spec.md) | implemented | [繁體中文](specs/maintenance/boolean_settings_design_spec_zh.md) |

## References

- [API](references/API.md)
- [carry_audit](references/carry_audit.md)
- [gameplay_style](references/gameplay_style.md)
- [keeper_skill](references/keeper_skill.md)
- [log_backed_turn_consistency_source_review](references/log_backed_turn_consistency_source_review.md)
- [player_command_reference](references/player_command_reference.md)
- [prep_persistence](references/prep_persistence.md)
- [role_card_template](references/role_card_template.md)
- [rules_reference](references/rules_reference.md)
- [scenario_template_reference_review](references/scenario_template_reference_review.md)
- [scenario_zh_external_preparation](references/scenario_zh_external_preparation.md)

## Architecture

- [main_v2 architecture overview and deep review (player experience first)](architecture/main_v2_architecture_review.md) · [繁體中文](architecture/main_v2_architecture_review_zh.md)

## Guides

- [setup](guides/setup.md)
- [gameplay](guides/gameplay.md)
- [configuration: what each switch changes for players](guides/configuration_profiles.md)

## Historical records

- [Changelog](changelog.md)
- [Raw evaluations](evaluations)
- [Camp Sunny validation: root causes](validation/camp_sunny_fix_root_cause.md), [targeted regression](validation/camp_sunny_targeted_regression.md), [latency before/after](validation/camp_sunny_latency_before_after.md), [findings](validation/camp_sunny_findings.json) (Traditional Chinese; the real-runtime stages were not run)
- [Machine-readable spec catalog](specs/catalog.json)
