# advance_combat_turn 的拒絕訊息說明該怎麼做

## 問題

在 Haunting 的一次測試中，敵人先攻，守密人一開始的幾個回合都在試著跳過牠的回合：對敵人呼叫 `advance_combat_turn skip=true`，連續三次被拒絕「Only the acting investigator can skip their own turn」，每次都是玩家看到「處理這個行動的工具失敗了」的失敗呼叫。另一次呼叫被拒絕「Managed resource mutation requires an explicit stable event_id」，訊息沒有提到是哪個工具，也沒有範例，守密人只好猜一個 id 再呼叫一次。

## 修改

* 對敵人或盟友使用 `skip=true`：改說該怎麼做：先 `plan_enemy_turn`、再 `run_enemy_combat_plan`，之後不帶 `skip` 推進。跳過其他玩家的回合，訊息不變。
* 沒有帶 `event_id`：說明 `advance_combat_turn` 需要穩定的 `event_id`，給出格式（`<combat_id>:advance:round<N>:<actor>`），並說明只有重試同一次呼叫時才沿用同一個 id。

## 不做

不替守密人跳過或推進，也不自己編 id：自己編的 id 會讓重複的呼叫推進兩次。

## 測試

`tests/test_combat_wiring.py`：沒有 `event_id` 的呼叫，與對敵人的 `skip`，都以新的措辭被拒絕。
