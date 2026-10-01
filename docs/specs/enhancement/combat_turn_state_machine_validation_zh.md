# Combat implementation 驗證

[英文 canonical report](combat_turn_state_machine_validation.md) · [核准 design](combat_turn_state_machine_design_spec_zh.md)

操作者於 2026-10-01 授權實作。T1–T5 已在 `f1be316` 合併；T6 以真正 repository transaction／玩家 controls 驗證。OCR／PDF 不在此改動範圍。T7 最終 review／發布 checks 尚未完成。

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

T6 full-suite checkpoint：**1821 passed、2 skipped、152 subtests passed**，15.02 秒。之後新增 source-stop、unsupported NPC cancellation、distinct-healer public regressions 已逐項通過。最終 T7 totals 與 ruff／mypy／compileall／diff checks 將於完整 integration review 後記錄；dependency deprecation warnings 保留在 logs。

T6 尚待 exact initiative-advance retry 回傳原 NPC interaction receipt；目前嚴格 public regression 保存中，由 action runner owner 修正 durable receipt binding，沒有放寬 assertion。
