# 戰鬥回合：一鍵防守、回合一定結束得了、守密人知道下一步

[English](combat_turn_friction_design_spec.md) | [文件索引](../../README.md)

分類：`enhancement`。狀態：**已實作**。基於 `main_v2` 的 `ac6c943` 加上 PR #241。

## 問題

一次攻防要玩家打一句話、按兩到五次按鈕、等六到八次依序的模型請求，而且下列任何一件事都會讓戰鬥停住：

- 防守按鈕寫著「選擇並擲 閃避」，卻只記錄選擇；擲骰要再按一次，幸運再一次。提示沒說是誰在攻擊，「要擲到多少」的提示也從未出現，因為管理式引擎從不設定 `attacker_tier`。
- `advance_combat_turn` 在守密人漏掉 `event_id` 時被拒（PR #241 修角色指稱之前一局就被拒 12 次），重複使用同一個 id 則默默重播舊的推進。
- 只登記 HP 的敵人（`/coc combat addnpc`，或沒有攻擊的戰鬥卡）不能行動（`run_enemy_plan` 拒絕不完整的卡）也不能跳過（敵方回合的跳過會被指回敵人流程），先攻就凍住。NPC 友軍的回合根本結束不了。
- 推進明明已經換人，卻在下一位敵人的回合跑不起來時被回報成失敗，守密人重試，玩家看到「工具失敗了」。
- 回合驗證只把宣告攻擊、登記、結算或跳過算成回合效果；單純推進、敵人計畫或裁定都算「未驗證」，玩家拿到通用的退路訊息。
- 檢定結算後的敘事者從未被告知要推進；戰鬥停在已完成行動的人身上，直到有人再打字。
- 戰鬥中的幸運提示把等級列成 `regular`／`hard`；靜態提示寫「declare_combat_action 然後 run_combat_action」但宣告本身就會執行行動；`adjust_character` 把守密人導向管理式戰鬥會拒絕的傷害工具。

CoC 7e 規則本身不變：閃避與反擊仍是防守方的選擇、以對抗擲骰解決，幸運仍由玩家決定，只有引擎本來就無法執行的回合可以放棄。

## 修改

- `ManagedCombatChecks._choose` 在同一筆交易裡直接擲剛登記的防守檢定。一次點擊完成選擇與擲骰；幸運決定若有提供仍是另一次點擊。選擇回執存下擲骰結果，重複點擊會重播。`_defense_choice` 在待處理選擇上記 `attacker_name` 與 `attacker_tier`；提示會說是誰攻擊、每個選項需要什麼。
- `advance_combat_turn` 省略 `event_id` 時自動推導為 `<combat_id>:advance:round<N>:<行動者戰鬥者 ID>:<該行動者本輪已完成的行動數，不含跳過>`。同一次推進的重試會重播（跳過不計入行動數，所以它的重試推導出同一個 id）；同一輪被 `set_initiative` 調回、又做了另一個行動的行動者會得到新 id。行動者指稱經 `combat.resolve_actor_reference` 解析（先比對目前行動者自己的欄位，再全域查找；PR #241 的規則，現在引擎與工具共用）。守密人不會再因為漏掉 id 被拒。同一行動者在被重排的同一輪跳過兩次仍會推導出同一個 id，第二次會重播第一次。
- `skip=true` 接受目前行動的 NPC 友軍，以及引擎無法執行其回合的目前敵人（`combat.enemy_turn_blocker`：沒有卡，或攻擊與能力都沒登記的 `incomplete` 卡）；結果的 `skipped` 欄位（放在下一位行動者回傳的內容旁邊）說明原因與符合規則的替代做法（用 `add_npc_to_combat` 登記劇本的攻擊）。有攻擊的敵人仍不能跳過；調查員的回合仍只有玩家自己能跳。
- `prompt_config._combat_next_step` 把待履行的 CON 檢定（`INJURY_CHECK`）當成其他待擲骰一樣處理：不要推進。
- `combat_flow.advance_combat` 在下一位敵人的計畫或執行失敗時，回傳成功的轉換並附上 `enemy_turn`，而不是只回傳失敗。
- `turn_resolution._mutation_evidence` 把下列引用過的工具算成回合效果：結束了行動玩家自己戰鬥者回合（回合數或目前行動者改變）的 `advance_combat_turn`、攻擊目標是該玩家且已完成或正在等他選擇／擲骰的 `run_enemy_combat_plan`、以及該玩家戰鬥者參與的行動的 `resolve_combat_ruling`。推進別人的回合不是這位玩家的行動；守密人該用 `deferred` 回答。
- `prompt_config` 依戰鬥回執在已結算檢定區塊加上【戰鬥下一步】：行動完成就推進（附要傳的參數）、另一位玩家的選擇或擲骰待處理就不要推進、暫停就先裁定。結算後敘事者的指示也這麼說。
- 戰鬥提示的幸運等級改為一般成功／困難成功／極限成功。提示文字改為 `declare_combat_action` 會執行行動、`run_combat_action` 只用來恢復；`adjust_character` 把敵人傷害指向戰鬥流程；管理式戰鬥一定拒絕的五個工具在描述開頭先說明。
- 戰鬥中的 `/coc combat next` 回覆戰鬥狀態與目前輪到誰，而不是單純拒絕。

## 未做

- 推進仍由守密人決定；引擎不會在行動完成後自行推進。
- 調查員的反擊仍用鬥毆與 1D3，而不是手上的武器。
- 對遠程攻擊選「不閃躲」會直接結算、沒有敘事回合。
- `/coc combat damage`／`end` 的說明仍描述戰鬥中會被拒絕的指令。

## 測試

`tests/test_combat_turn_friction.py`；`tests/test_combat_engine.py` 的 B 系列情境改為一次呼叫完成防守。
