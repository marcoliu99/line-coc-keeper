# 2026-10-10 四場 soak run 的四種回覆：有證據卻放棄、在等不存在的人、戰鬥提示對錯人、開戰敘事被蓋掉

[English](keeper_gives_up_and_combat_guidance_design_spec.md)

狀態：**已實作**。基底：`main_v2` 的 `0c53dcb`。

## 問題

在 `0c53dcb` 上跑的四場 100 輪 Codex run（Scritch Scratch、Camp Sunny、The Lightless Beacon、The Haunting；2026-10-10）每回合都完成了，但有四種回覆讓玩家讀起來不對：

| Run、回合 | 玩家看到 | 原因 |
|---|---|---|
| Camp Sunny 50 | 「劇本裡沒有足夠的內容可以據以裁決這個行動」 | 守密人搜了四次劇本，找到了那一段（「should one of the investigators become a pesky nuisance, he or she will be added to Billy's menu … they look to capture the investigators」），卻回 `incomplete`。沒有工具拒絕，所以為拒絕加的重試（`rerun8_player_replies_design_spec`）不會觸發；provider 自己的重試只在還沒呼叫任何工具時才會。這場 run 要測的戰鬥從頭到尾沒開始。 |
| Haunting rerun1 17 | 「還有尚未完成的檢定、Luck 決定或他人的行動；請先完成它」 | 守密人沒呼叫任何工具就回 `deferred`，但當時沒有任何人有待擲檢定或 Luck 決定。驗證器拒絕了這個延後（`deferral_not_verified`），而這個代碼被歸類成待處理狀態，玩家就被要求去完成一個不存在的檢定。 |
| Lightless 31–36 | 「戰鬥進行中，現在輪到「Julian Price」行動 … 還沒輪到你時，請稍候」，連續六輪 | 那句話指名的敵人從來沒登記（「深潛者混種」；戰鬥裡是四隻 Youngling），守密人因此擋下。戰鬥提示只說輪到誰，不知道是誰在問，而問的人就是 Julian。 |
| Haunting rerun1 28 | 「George Finch 的檢定已結算 … 這次行動尚未完整處理」 | 這句開啟了戰鬥：`initialize_combat` 成功、也擲了 SAN 檢定，但 SAN 檢定的 Luck 決定讓回合停在 `incomplete`，而 incomplete 回合的敘事會被警告取代。Corbitt 復活完全沒被說出來。延後那條路自 `rerun8_player_replies_design_spec` 起會保留敘事，這條路沒有。 |

## 變更

- **找到劇本文字的守密人再問一次。** Executor 的 `final_feedback` 裡，`model_incomplete` 的結論在下列情況可用掉既有的那一次重試：這回合所有呼叫都是成功的查詢（`INFORMATION_QUERY_TOOLS`），而且至少一次 `search_scenario` 有查到文字。提示會說查詢已有結果、狀態沒有改動，請依查到的內容用工具處理，或以 `no_mechanics` 引用那段敘事；只有查到的文字確實與行動無關時才保留 `incomplete`。沒有任何狀態改動，所以重試不會把任何事套用兩次。
- **在等不存在的人，就是沒有行動。** `turn_fallback.classify` 在回合沒呼叫工具、也沒有任何人有待擲檢定或 Luck 決定時，把 `deferral_not_verified` 歸為 `executor_no_action`。這個原因可以重試，supervisor 會再跑一次 Executor；再失敗，玩家看到的是「沒有行動」的文字，不是關於不存在檢定的說法。有待處理項目、或呼叫過工具時，仍歸為待處理狀態。
- **戰鬥提示知道是誰在問。** `combat_guidance` 多收行動角色；目前行動者就是提問者時，回「戰鬥進行中，現在輪到你（Julian Price）行動。目前的敵人：「Youngling（1）」…。請說明要攻擊哪一個、用什麼方式；指名的目標必須是上面列出的敵人。」，列出還站著的敵人。其他人仍被告知等待。`enforce_mechanic_check_consistency` 傳入 `turn_resolution.actor_character_id`。
- **開戰敘事在 incomplete 回合也保留。** incomplete 回合的呼叫裡有成功的 `initialize_combat` 或 `start_combat`、敘事又不是空的時，回覆先是敘事，再接確認行和警告，最後是還欠的東西（Luck 決定、待擲檢定）。和延後那條路同一個規則。

## 不變更

- 有擲骰或寫入的 incomplete 回合（Camp Sunny 23：先擲 SAN 檢定再 `incomplete`）不再多問：重試可能把擲骰的後果套用兩次。回覆已經顯示了結算結果。
- Lightless 那句指名了不存在的敵人，是測試腳本的錯。這次改的是讓拒絕有用，不是讓目標合法。
- 自動擲骰時多數回合以 Luck 提示結尾：那是規則上的玩家決定，真人桌上會按按鈕。

## 驗證

`tests/test_keeper_gives_up_and_combat_guidance.py`：
- 有查到文字的成功搜尋符合重試條件；沒有事件、結果為空、呼叫裡有寫入、或有拒絕，都不符合。
- `deferral_not_verified` 在沒工具、沒待處理項目時歸為 `executor_no_action`；別人有待擲檢定、或呼叫過工具時，歸為 `unresolved_pending_state`（`tests/test_turn_fallback.py` 的參數表為第一種情況更新）。
- 目前行動者會被告知輪到自己和哪些敵人還站著（倒下的不列出）；其他玩家被告知等待；回覆會傳入行動角色。
- 有成功 `initialize_combat` 的 incomplete 回合以敘事開頭，並仍帶 Luck 指示；沒有的只保留警告。
