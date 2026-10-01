# 查表式戰鬥回合與 provisional settlement

狀態：操作者於 2026-10-01 以 `$implement-spec` 明確授權實作；Q1–Q19 已確認。Branch：`enhancement/combat-turn-state-machine`；base：`main_v2`／`189bc8e`。

## 目標與範圍

AI 理解意圖與敘事；程式依 reviewed catalog、已記錄骰值、明確 combat state 解決 mechanics。Model prose／caller-supplied damage 不構成機械權威。第一版支援近戰、閃避／反擊、單發射擊、傷害、重傷／瀕死；連射、全自動、戰技或尚未支援特殊攻擊進 NEEDS_RULING。不猜武器、射程、數值或 severity。本 branch 不混入 PR155 OCR 工作。

**武器傷害值查表、其他傷害值查表都在設計範圍內**。能查骰式不等於每種特殊傷害都已能自動完整結算；適用類別與特殊條件仍要受明確規則／裁定限制。

## 已確認的選擇

| 問題 | 已選契約 |
|---|---|
| Q1 | 玩家預設自己觸發 bot 擲骰，保留 autoroll；明確 PLAYER_ROLL 等待。NPC 由 server 擲骰。 |
| Q2 | 整場 provisional，settlement 才改 persistent Character。 |
| Q3 | HP、Luck、SAN、MP、ammo、傷勢、治療等戰鬥變更都共用 working state。 |
| Q4 | 每個完成 action 保存 checkpoint、骰值與等待步驟，restart 不默默重擲。 |
| Q5 | 普通玩家不能 rollback；pending 檢定／Luck／傷勢未完成不能結算。操作者為 bot Keeper。 |
| Q6 | 手動指玩家按既有 bot 檢定操作，不是輸入實體骰結果。 |
| Q7 | 缺少或 ambiguous input 就 NEEDS_RULING。 |
| Q8 | 一般資源修改在戰鬥期間導向 working state，留 event；不繞過，也不全面拒絕。 |
| Q9 | MVP 近戰、閃避／反擊、單發、傷勢；其他先裁定。 |
| Q10 | Pending 清空後出 preview，指定操作者確認才 atomic commit。操作者為 bot Keeper。 |
| Q11 | 修正 append event、保留原骰、重算受影響狀態；重擲必須明確指定。 |
| Q12 | Timeout 不代替玩家選擇或擲骰；既有 autoroll 保留。 |
| Q13 | 只有目前 actor／wait owner 能推進；duplicate 回原結果，不排未來行動。 |
| Q14 | 指定操作者可在完整 action 之間調整順序並記錄，不打斷 pending action。 |
| Q15 | 查詢顯示 effective working state，標示未結算；操作者可看 persistent 差異。 |
| Q16 | Persistent character 有外部變更則停止 settlement，明確 reconciliation，不覆蓋。 |
| Q17 | 同一調查員只能參與一場未結算戰鬥。 |
| Q18 | Bot Keeper 確認 settlement／rollback，不要求註冊人類 KP Assistant 或取得其批准。 |
| Q19 | 可在傷勢／effects 未終止時結算，但未來義務必須轉入 durable postcombat tracking。 |

## 兩張表與 source authority

WeaponDefinition 是武器類型規則；WeaponInstance 是持有的具體武器、ammo、override。Stable ID、canonical name、alias 與 example 分開。明確 scenario definition 優先於 pinned definition 與 generic catalog；exact／唯一對應才解析，模糊「手槍」回 candidates 並等待裁定。Migration 保留目前 name→ammo inventory，不能從名稱偷偷猜武器種類。

Definition 包含 skill ID、attack mode、damage expression、DB policy、impale／extreme-success、distance-dependent damage bands、ammo usage 與 provenance／catalog version。霰彈槍依可信距離／裁定選 damage band；目前 abstract range_bands 不等於公尺數。骰式採 bounded parser，不 eval 任意 expression。

Foundry compendiums 作參照，不 wholesale import。目錄有 en-items、en-skills、en-wiki-weapons；已讀 en-items 含 prototype／example weapons，不能看到 weapon row 就當 generic authority。Implementation 必須 pin source revision/hash、逐筆 review shipped definitions，runtime 不抓 mutable develop；目前 shipped subset 已驗證 45 筆：44 筆 pinned wiki weapons，加一筆獨立 reviewed human unarmed；revision `7974aaca08dd15e78959e71f8ce2e0a0ee008a01`，examples 已排除。此證據不代表全部 compendiums 都完成 review。詳見 [implementation validation](combat_turn_state_machine_validation_zh.md)。

Other Forms of Damage severity lookup：minor→1d3、moderate→1d6、severe→1d10、deadly→2d10、terminal→4d10、splat→8d10。Severity 由有權限的明確裁定或 verified scenario rule 選擇，不從自由敘事自動猜。記錄 effect ID／source／單次或每輪／trigger timing／defense／stop condition。特殊 poison／drowning 等保留自己的 CON／reduction／死亡例外；尚未支援時 NEEDS_RULING，不能用通用表抹掉特殊規則。

來源：[Chaosium damage／injury](https://cthulhuwiki.chaosium.com/rules/hit-points-wounds-and-healing.html#other-forms-of-damage-table)、[Foundry directory](https://github.com/Miskatonic-Investigative-Society/CoC7-FoundryVTT/tree/develop/compendiums)、[已讀 examples](https://github.com/Miskatonic-Investigative-Society/CoC7-FoundryVTT/blob/develop/compendiums/en-items.yaml)、操作者 draft `/Users/marcoliu/Downloads/coc7_combat_rules_state_machine_design.md`。2026-10-01 查閱；不複製完整 descriptions 到 catalog。

## 現有 code seam 與差異

app/combat.py 已有 enemy planning、effects／processed timings 與部分 major-wound pending guards，應保留。_sync_pc_hp 與既有 Luck／inventory mutation 立即改 persistent Character，需全部改 effective-resource seam。end_combat 現在清掉 CombatState，不能照搬到新 settlement／continuing-state contract。

app/dice.py 已有 structured RollResult，但一般 expression parser 只有單一 dice term 加 integer。在這個 seam 支援 bounded composite dice，不重造所有既有結果。穩定 action／interaction ID 必須在 RNG 前取得；retry 不能產生新 UUID 再重擲。結果與變更一起 durable save 後才 publish；resolve_enemy_action 不能直接相信 caller 的 hit／damage。

app/repositories/group_state.py 已有 group state_revision、state lock、BEGIN IMMEDIATE、character mirror atomic writes。重用這個 transaction seam；目前沒有 character revision API，不假設它存在。增加 baseline resource fingerprints／合適 revisions 判斷 settlement conflict。Checkpoint 自己增加 group revision 不等於角色外部修改。

既有 major-wound 判斷排除部分扣至零 HP 的 hit；依官方單次傷害門檻修正，区分 unconscious／major wound／dying／dead，不只 defeated。多次小傷害不能倒推成一次 major wound；CON 繼續 player-owned。既有 glossary 的重傷定義也要同步澄清。

## Durable state／transition proposal

Combat 包含 combat_id、schema／rule version、participant IDs、baseline snapshots／fingerprints、working resources／injuries、initiative／actor／round、actions、owned interaction、roll receipts、append-only events、effects／timing receipts、preview／final settlement receipt。僅保存必要戰鬥 evidence，不造 general event-sourcing framework。

READY → declaration／validation → 必要 PLAYER_CHOICE／PLAYER_ROLL → 可用 LUCK_DECISION → RESOLVE → 必要 INJURY_CHECK → action complete → next actor。NPC 既有 planner 支援的 mechanics 在 server 執行，遇 human boundary 暫停。Unknown input 進 NEEDS_RULING，不先扣資源。Pause 保留 action identity／骰值。

每個 transition 在既有 mutation admission／lock 下重驗 actor、interaction、revision，atomic 保存 receipts／checkpoint。Invalid／stale／out-of-turn 不耗 RNG、ammo 或 Luck。Save 後回覆前 crash，retry 仍取原結果；未保存 random draw 不算可 publish receipt。Runner 有有限 transition budget，避免 endless NPC／effect loop。

修正保留原 receipt 並 append override，重算不自動 replay RNG。若前面修正令後面 target／choices 不成立，pause 並明確 reconciliation，不能默默留不可能的行動或刪掉玩家決定。Rollback 保留 audit、release battle ownership、舊 controls 失效；不能抹掉已送出的訊息。

Settlement preview 綁 current battle revision／settlement ID，顯示 baseline→effective resources／injuries。Pending-empty、preview current、persistent baselines match 才能提交。Absolute final values、mirrors、receipt、closed battle 一個 transaction 寫入；repeat confirm 回原 receipt。Conflict 維持 open，reconciliation 後生成新 preview。Narration 不能獨立把 provisional mechanics 發布成 committed scenario fact。

## 已確定的權限與戰鬥後追蹤契約

本 spec 的操作者是 CONTEXT.md 定義的 **bot Keeper**。它可 review／confirm settlement、做明確且有依據的裁定、批准 rollback，不要求每場有人類 KP Assistant。玩家請求不直接獲得行政 mutation 權限：Keeper 必須發出明確、綁定本場的操作與理由。Code 仍檢查 battle identity、command ownership、pending、preview revision、baseline conflict、duplicate receipt，不靠 Keeper 敘事當通過證據。Rollback 不能偽裝成 retry 或玩家 cancel button。既有人類 KP steering／correction 保留；沒有新增必要人類角色。這是此次明確 combat 選擇，不是從 ADR-0001 的 narrative correction 權限推論而來。

Preview／confirmation phase 仍明確且 durable，但由 bot 確認，不增加等待人類批准的步驟。Conflict／unresolved inputs 仍能阻止 deterministic transition，即使 Keeper 呼叫 confirm。

Pending-empty 指**目前到期、尚未完成**的玩家 choice／check、Luck、injury transition／effect application 都處理完，不要求所有傷勢痊癒或未來 effects 終止。瀕死或仍受 effect 的調查員可以結算，但 settlement 必須 atomic 保存傷勢並把所有未來義務轉入 structured postcombat tracking。

Postcombat record 保留 participant／effect ID、injury state、下一個 logical-game-time trigger、condition／stop rule、rule source、processed timing／roll receipts。後續瀕死 CON 仍 player-owned（或既有 autoroll）；持續傷害依 reviewed rule 與 durable receipt 解決。以明確遊戲時間／round 推進，不是 wall-clock timer。Settlement 不重設 timing、不跳過 due check、不重複 tick，也不多送免傷間隔。未完成的 postcombat player check 暫停相關推進。只有 transfer 已 durable 才清 CombatState。Restart 不重擲；下一場接納原有傷勢／義務，不複製 effect 或重設傷勢。

結算後，義務處理依普通 persistent-state mutation contract；若已进入新戰鬥，相關 resource／injury 變更導向新 working state。保留來源 battle receipts。未提交戰鬥 rollback 不 transfer provisional obligations，也不將其發布為 committed canonical fact。

目前訪談沒有剩餘 business-decision 問題。Schema／parser／catalog verification 是 implementation seam 要驗證的工作，不把這些 technical details 丟給玩家決定。

## Delivery／testing

先完成 spec／glossary／ADR，確認 shared understanding 後，再授權 implementation。Incremental seams：catalog lookup → bounded dice／receipts → effective resources／checkpoint → supported action runner → injury/effects → settlement／correction／resume。不得上線「HP provisional 但 Luck／ammo 還直接寫入」的半套。Legacy active battle 要 explicit schema migration／admission，不猜 reconstructed baseline 或丟 pending。

測 scenario override／weapon ambiguity、DB／composite dice／shot distance、severity 不猜值、defense ties／autoroll、actor／check ownership、所有資源／治療 routing、zero-HP injury、effect timing once、各 roll／Luck／checkpoint／settlement crash boundary、retry 不重扣、external conflicts、one-battle admission、append corrections／rollback stale controls、pending settlement、atomic postcombat transfer、瀕死後續檢定／restart timing／再進戰鬥與 rollback 不 transfer、enemy privacy、既有 narrative authority。Implementation 跑 full pytest／ruff／mypy／compileall／diff-check。Tool-round-trip performance 要用 traces 實測，不把 draft estimates 当已證明效果。

Implementation tickets / 工作圖: [EN](combat_turn_state_machine_tasks.md), [繁中](combat_turn_state_machine_tasks_zh.md).
