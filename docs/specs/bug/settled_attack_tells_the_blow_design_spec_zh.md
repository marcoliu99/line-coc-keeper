# 引擎當場結算的攻擊，一行固定句說出結果

[English](settled_attack_tells_the_blow_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `c85efbd`。

## 問題

The Lightless Beacon 2026-10-11 fresh5（autoroll 開）：第 39 回合 Thomas 一槍把幼體從 4 HP 打到 0，引擎接著播了混血對 Mei Lin 的攻擊；玩家讀到的是「Thomas，你的回合尚未到來；先等 Mei Lin 選擇並擲出眼前的防禦檢定。」，擊殺一個字都沒有。第 49 回合 Celia 的拳頭命中造成 3 點，Executor 卻回 `deferred`，重試拿到通用提示，玩家讀到「Celia，你的拳頭尚未揮出」。第 33、41、52、54 回合同樣沒提剛結算的那一擊。

`turn_delivery.observe_tool` 刻意不替 `declare_combat_action` 產生公開文字（固定的「戰鬥機制操作已記錄」被當成噪音），所以引擎在同一次呼叫裡結算的攻擊只剩 Narrator 會講，而回覆是等待句或 deferral 時 Narrator 就漏掉它。

## 變更

- **結算的攻擊有一行固定句。** `declare_combat_action` 或 `run_combat_action` 回傳 `completed` 且 `result` 帶 `hit` 時，觀察到的結果是公開的：「⚔️ 命中，幼體（深海夢魘） 受到 9 點傷害，倒下了。」、「⚔️ 這一擊沒有命中。」、「⚔️ 命中，但傷害全被 … 的護甲擋下。」、「⚔️ 對方反擊得手，Celia 受到 2 點傷害。」或「⚔️ 武器故障，這一擊沒有打出去。」；暫定標記跟其他戰鬥結果一樣接在後面。還在等玩家擲骰的攻擊照舊不說，擲骰回覆會講。
- **攻擊已結算還交 deferral，重試直接給答案。** `final_feedback` 新增 `attack_settled_not_deferred`：攻擊是已提交的變更，改交 `resolved` 並引用那次宣告，敘事先說這一擊再說輪到誰。

## 不變更

- 其他戰鬥工具仍不產生固定句；戰鬥由敘事來講。

## 驗證

`tests/test_settled_attack_tells_the_blow.py`：命中並倒下、命中未倒、落空、護甲擋下、反擊得手、武器故障；等擲骰的攻擊維持內部；`_attack_settled` 只對帶結果的已完成收據為真。
