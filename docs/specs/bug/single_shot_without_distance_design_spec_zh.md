# 玩家單發射擊沒說距離就暫停整場戰鬥，而且 log 看不出戰鬥工具為什麼失敗

[English](single_shot_without_distance_design_spec.md)

狀態：**已實作**。基底：`main_v2` 的 `e4a9a1f`。

## 問題

在 `d18fffd` 上跑的 2026-10-11 soak run（Camp Sunny rerun2、The Lightless Beacon rerun3）裡，戰鬥有好幾回合動不了，每位玩家讀到的都是「戰鬥暫停中，要等守密人裁定後才能繼續」或「處理這個行動的工具失敗了」。

- **Camp Sunny 第 77 到 99 回合。** Reidel 第一槍宣告成單發射擊，沒有距離。引擎對此的回應是一個裁定（`Single shot requires trusted physical distance and reviewed base range`），把整桌的階段設成 `NEEDS_RULING`。他之後的五句話都被拒絕，Phil 也是。裁定只有守密人能清，卻花了很多回合。
- **Lightless 第 38 到 42 回合。** 戰鬥從敵人的開場攻擊開始，那是一根引擎不支援的毒針，同樣讓戰鬥暫停。每位玩家的射擊都被「Resolve the current action before declaring another」擋下，直到守密人在第 43 回合取消裁定。之前守密人的兩次裁定為什麼失敗，log 裡沒有。
- **Log。** runtime log 只用工具名稱記一筆 `turn.observed`，看得出工具跑了，看不出它失敗，也看不出原因。上面 Lightless 的成因是靠測試腳本的備註和引擎原始碼拼出來的。

## 變更

- **玩家的單發射擊沒說距離，就當作一般難度。** 行動者是玩家角色、又沒給距離時，`combat_flow` 把這一槍當作在武器基礎射程內，戰鬥繼續。給了距離就照舊使用，距離超過武器的極限射程仍需裁定。場景讓目標很遠時，守密人仍可傳入 `distance_yards`。暫停的射擊被裁定恢復時也是同一條規則。
- **工具失敗時，runtime log 寫下原因。** `turn.observed` 多了 `ok`；呼叫失敗時再附上錯誤文字（前 300 字）和戰鬥停在的階段。被 gateway 拒絕的呼叫（`llm.tool.rejected`）也附上它的訊息。這些是規則文字，不是玩家寫的話。

## 不變更

- **敵人的單發射擊沒給距離，仍然要裁定。** 敵人由守密人操作，守密人知道場景；原有的測試維持不變。
- **傷害隨距離變化的武器**（霰彈槍）仍然需要距離，跟以前一樣：那是傷害規則，不是射程難度。
- **引擎玩不了的敵人開場攻擊**（Lightless 的毒針）仍然會暫停戰鬥、仍然等守密人。這份 spec 只讓下一次 run 的 log 看得出守密人的裁定卡在哪裡；引擎要不要自己跳過這類行動，是另一個決定。

## 驗證

- 玩家的單發射擊沒給距離，會被宣告並以一般難度擲骰；給 `distance_yards=60` 就不是一般難度；給 `distance_yards=1000` 仍需裁定，且不消耗彈藥（`tests/test_combat_state_machine_integration.py`、`tests/test_combat_mechanics_coverage.py`）。
- 原本靠「沒給距離」製造裁定的測試，改用超過極限射程的距離，仍檢查擁有的武器鍵在重載後保留。
- 敵人的單發射擊沒給距離，仍回 `NEEDS_RULING`（`tests/test_combat_flow.py`，未改）。
- 工具失敗時 log 寫 `ok`、`error`、`phase`；成功的只寫 `ok`；gateway 拒絕會寫訊息（`tests/test_turn_phases.py`）。
