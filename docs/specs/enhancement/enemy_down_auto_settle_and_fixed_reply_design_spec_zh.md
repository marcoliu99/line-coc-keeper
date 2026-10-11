# 最後一個敵人倒下時戰鬥自動結算；固定回覆不再等 Narrator

[English](enemy_down_auto_settle_and_fixed_reply_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `25b6d13`。

## 問題

2026-10-11 的 main_v2 code review 找到兩個玩家白等的地方。

1. **最後一個敵人倒下，戰鬥卻還開著。** `_auto_advance` 只在收據上標 `settlement_ready`；守密人還得在之後的回合先預覽再確認結算，所以 2026-10-11 的 soak 在最後一個敵人倒下後都多等了一兩回合劇情才繼續。玩家方全倒時引擎早就會自己結算。
2. **引擎本來就要換掉的回覆，還是等了 Narrator。** `incomplete`／`blocked` 且沒有開戰的回合、沒改變任何狀態的 `deferred`、以及 `cancelled`，`enforce_mechanic_check_consistency` 都會用固定文字（警告、已確認的結果、接下來該做什麼）回覆，不管 Narrator 寫什麼；Supervisor 卻還是先跑 Narrator，每次約 5 秒，然後把敘事丟掉。

## 變更

- **敵人全倒就結算。** `CombatEngine.handle` 本來就在戰鬥自己的步驟（攻擊、防禦、推進、敵人計畫、裁定、效果）之後看玩家方有沒有全倒；現在也看敵方是否全部被擊倒，且沒有任何未完成的事（沒有互動、phase 是 READY、所有行動都完成），然後用同一條路結算：先預覽再確認，理由「敵方已全數倒下，戰鬥自動結算」。結算只把戰鬥中暫定的 HP、彈藥與義務寫回角色卡並關閉戰鬥；不碰劇情，劇情照常繼續（玩家方全倒就結束劇本的 house rule 不變，仍只適用玩家方）。收據會有 `settled` 與 `settled_reason`，給守密人的下一步提示會說戰鬥已結算、只要敘事並繼續、不要再結算一次。守密人自己改 HP 把敵人改倒的情況仍交給守密人，跟玩家方一樣。
- **固定回覆跳過 Narrator。** `prompt_config.narration_replaced` 列出回覆固定的 disposition；一般玩家回合（不是檢定結果的後續）遇到時 Supervisor 跳過 Narrator，由一致性步驟照舊寫出回覆。log 記為 `narrator.skipped`。

## 不變更

- 引擎拒絕結算時（暫定資源有衝突）守密人仍可手動結算；`settlement_ready` 這條路保留給這種情況。
- 在 incomplete 回合開戰的情況仍會跑 Narrator：敵人現身要先說，再說欠玩家的事。

## 驗證

- `tests/test_combat_turn_friction.py`：擊倒最後一個敵人的那一擊回來就是 `settled`，戰鬥 CLOSED 且已提交，劇本仍在進行，守密人的提示說已自動結算；在自動推進中倒下的一方也會結算；手動標成擊倒、或還有未完成行動時，引擎不結算。
- `tests/test_turn_stage_sequence.py`：blocked 回合不跑 Narrator；開戰的 blocked 回合會跑。
