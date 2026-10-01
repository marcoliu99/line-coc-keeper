# Combat implementation 驗證

[英文 canonical report](combat_turn_state_machine_validation.md) · [核准 design](combat_turn_state_machine_design_spec_zh.md)

操作者於 2026-10-01 授權實作。T1–T5 已在 `f1be316` 合併，exact advance receipt follow-up 為 `d6f870c`；T6 以真正 repository transaction／玩家 controls 驗證。OCR／PDF 不在此改動範圍。T6 於 `29f7f2d` 合併；T7 review 修正已通過兩軸獨立 recheck，最終整合／發布 gates 另行記錄。

## 權威與持久化

Reviewed catalog、bounded server dice 與穩定 combat/action/check IDs 解決 mechanics；current actor／owned control 決定誰可以操作。玩家預設手動骰，autoroll 保留。未知武器、距離、模式、來源進 NEEDS_RULING；bot controller 明確取消 unsupported action 後可以推進，不猜傷害。

戰鬥 HP／Luck／SAN／MP、彈藥、狀態及傷勢都寫入 durable working snapshot。原有 name→ammo inventory 與 persistent character mirrors 在 settlement 前維持 baseline。Preview 不結束戰鬥；confirm 檢查 exact revision／absolute totals／baseline／obligation projection，再於同一 SQLite transaction 提交。Retry 不重扣成本；舊 receipt 不能關閉新戰鬥。外部角色改動需要明確 reconciliation，無關 checkpoint revision 不當成 resource conflict。

Correction 追加 evidence 並保留原骰。HP 增減兩種修正都要求 injury/action reconciliation，不能帶著 stale injury 發布。Legacy active tracker 不猜 prebattle baseline，explicit close 保存歷史與既有 committed character。

Future dying/effect obligations 保存來源、target、stop condition、logical trigger 與 receipts，跨 settlement／restart／下一場戰鬥延續。Due controls 阻止 rollback；zero-HP target 即使一開始不具 initiative 行動能力，也納入 working state。Rollback 還原原 schedule，保留 cached rolls；same-round catch-up 不重骰、不抹掉上一場後果。Stop 在新戰鬥內是 provisional，戰鬥外則持久化。First Aid 綁定 healer owner、patient、当前 dying obligations，不能換病人或重複消耗成功結果。

## 實測 seams

`tests/test_combat_state_machine_integration.py` 使用 isolated real SQLite、public tool handlers、public check/Luck router 與 repository reload。只 mock server RNG／外部敘事與傳輸，不替換 save/load、admission、resource mutation 或 settlement。Fixtures 是 controlled synthetic mechanics，不是真實玩家 session。

涵蓋 resource／inventory effective queries、manual／autoroll／Luck、NPC-first bootstrap、manual defense、單發彈藥、unsupported cancellation、exact retry／lost reply、ownership／privacy、conflicts、correction、legacy、atomic storage failure、舊 receipt／新戰鬥、prior effect／dying catch-up、不同 healer／patient 與 source-scoped stop。原 completed evidence／safety／handoff tests 保留敵方 privacy、armor／HP、provider failure assertions，改以 reviewed source route 取代 caller-supplied damage。

Manual melee trace 實際使用兩次 bot tool（initialize／declare）與兩次成功玩家 controls（roll／Luck skip）；NPC defense 由 deterministic runner 直接擲骰。Durable roll 後刻意讓 reply 失敗，再用同一 control 可顯示原骰值與 skill value，不重骰或重扣資源。這是執行過的 deterministic bot flow，不宣稱 LLM latency／token savings 或真實玩家效能。

## Catalog evidence

`coc7-reviewed-2026-10-01` 的 shipped weapon subset 有 45 筆：44 wiki weapons 與一筆獨立 human unarmed。Foundry pin `7974aaca08dd15e78959e71f8ce2e0a0ee008a01`；各 row 有 source hash／access date，排除 prototype/examples。此證據不是全部 compendiums 的完整驗證。Range 使用 yards，有 pinned language evidence。

Chaosium 六級：minor 1d3、moderate 1d6、severe 1d10、deadly 2d10、terminal 4d10、splat 8d10；snapshot SHA-256 `6498d208539738b1f0a04c764032110b1b5acf40f0d225163397b7630bbfdfbf`，2026-10-01 查閱。Fire／poison prose 不自動選 severity 或特殊規則。Runtime 只讀 shipped local data，不抓 mutable develop。Catalog／dice tests 驗證 shipped expressions、precedence／ambiguity、DB／range 與 bounded parser；沒有 commit 完整 copyrighted descriptions。

## 驗證狀態

T6 merged checkpoint `29f7f2d`：**1823 passed、2 skipped、152 subtests passed**。Review fixes 與舊 prompt expectation 更新後，固定 runtime/test checkpoint `314b52d`：**1851 passed、2 skipped、152 subtests passed**，44.31 秒。Integration 檔案包含 38 個 SQLite／transport／prompt cases 與 11 個 serialized-role codec cases。全 repo `ruff check .` 通過；`mypy app` 126 source files 通過；`python -m compileall app tests`、`git diff --check` 與 `git diff 189bc8e...HEAD --check` 通過。九個既有 dependency deprecation warnings 留在完整 output；pytest 時間不是實際玩家效能 benchmark。

Exact initiative-advance／choice retry 回傳原 owned interaction response。Lost roll／choice delivery regressions 重新載入 durable state，確認原結果、不多骰且資源不變。兩軸獨立 recheck 已通過，零 remaining/new findings。最終 merged HEAD 的重新驗證結果如下；PR ready／cleanup 不代表授權 merge `main_v2` 或 deploy。

## Review findings 與修正

Standards 初審兩項：typed closed role/stage boundary（P2）與重複 range selection（P3）。`c5547ff` 將實際 `CombatState.actions` stage 與 check context/role 型別化，保留 `injury:<character_id>` 序列化；三條 ranged paths 共用小型 owning rules helper，各自 ammo/source admission 不變。Standards recheck：**0 remaining/new findings**。

Spec 初審兩項 P2：選擇成功但 reply 遺失的 exact receipt，以及 active/static/KP prompts 仍指向 legacy caller outcomes。`c5547ff` 保存 battle/timeline/owner/character/exact-choice response 與原 button reply；相同 owned input 重播原結果，foreign/不同選項/rollback/新戰鬥不能 replay。Prompts 改為 source-bound runner、owned controls、單次 ammo 與 preview/confirm，保留一般 authorized controller resource adjustment，不能代替 weapon adjudication。Spec recheck：**0 remaining/new confirmed findings**。`314b52d` 更新三份舊 prompt tests，保留 scenario trigger、privacy 與 correction assertions。

沒有真實玩家 session traces 可供效能比較；目前 evidence 限於 controlled synthetic mechanics／真實 SQLite 與 public bot/router flows，不宣稱 production session throughput、LLM latency 或 token 節省。

## 修改檔案清單

相對核准 base `189bc8e`，共 58 個檔案（包含 review fixes）：

- Core state/rules/dice: `app/combat.py`, `app/combat_flow.py`, `app/combat_resources.py`, `app/combat_rules.py`, `app/dice.py`, `app/models.py`.
- Catalog data: `app/data/combat_severities.json`, `app/data/combat_weapons.json`.
- Tools/player controls: `app/commands/handlers/buttons.py`, `app/commands/handlers/character.py`, `app/commands/handlers/combat.py`, `app/commands/handlers/system.py`, `app/commands/router.py`, `app/keeper_tools/character.py`, `app/keeper_tools/checks.py`, `app/keeper_tools/combat.py`, `app/keeper_tools/consequences.py`, `app/keeper_tools/inventory.py`, `app/keeper_tools/managed_combat.py`, `app/keeper_tools/registry.py`, `app/keeper_tools/resource_bridge.py`, `app/legacy_commands.py`.
- Agent/prompt/turn integration: `app/agents/context_builder.py`, `app/agents/executor.py`, `app/agents/narrator.py`, `app/agents/tool_gateway.py`, `app/keeper.py`, `app/keeper_prompt_policy.py`, `app/services/canonical_facts.py`, `app/services/prompt_config.py`, `app/services/turn_context.py`, `app/services/turn_delivery.py`, `app/services/turn_resolution.py`.
- Tests: `tests/test_combat_cards.py`, `tests/test_combat_flow.py`, `tests/test_combat_resources.py`, `tests/test_combat_rules.py`, `tests/test_combat_state_machine_integration.py`, `tests/test_combat_wiring.py`, `tests/test_completed_combat_evidence.py`, `tests/test_compound_dice.py`, `tests/test_keeper_tool_registry.py`, `tests/test_kp_assistant_v2.py`, `tests/test_major_wound_con_gate.py`, `tests/test_static_prompt_combat_routing.py`, `tests/test_static_prompt_integration.py`, `tests/test_static_prompt_operational_authority.py`, `tests/test_turn_consistency_handoff.py`, `tests/test_turn_safety.py`.
- Domain/spec/validation docs: `CONTEXT.md`, `docs/adr/0003-provisional-combat-settlement.md`, `docs/specs/catalog.json`, `docs/specs/enhancement/combat_turn_state_machine_design_spec.md`, `docs/specs/enhancement/combat_turn_state_machine_design_spec_zh.md`, `docs/specs/enhancement/combat_turn_state_machine_tasks.md`, `docs/specs/enhancement/combat_turn_state_machine_tasks_zh.md`, `docs/specs/enhancement/combat_turn_state_machine_validation.md`, `docs/specs/enhancement/combat_turn_state_machine_validation_zh.md`.

## 最終交付

最終整合 `bb53045` 重新完整驗證：**1851 passed、2 skipped、152 subtests**（13.96 秒），ruff、mypy（126 files）、compileall 與兩種 diff checks 均通過。PR #156 已 ready for review；七個乾淨且已合併的戰鬥實作者工作樹均已移除，保留整合工作樹與已推送分支。未合併主分支或部署。
