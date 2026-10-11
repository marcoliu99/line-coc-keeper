# 射擊沒說距離就暫停整場戰鬥、用玩家的說法叫生物卻找不到數值表、log 看不出戰鬥工具為什麼失敗

[English](single_shot_without_distance_design_spec.md)

狀態：**已實作**。基底：`main_v2` 的 `e4a9a1f`。

## 問題

在 `d18fffd` 上跑的 2026-10-11 soak run（Camp Sunny rerun2、The Lightless Beacon rerun3）裡，戰鬥有好幾回合動不了，每位玩家讀到的都是「戰鬥暫停中，要等守密人裁定後才能繼續」或「處理這個行動的工具失敗了」。原因都在文字 log 裡，每個回合為工具呼叫寫的那一行。

- **Camp Sunny 第 77 到 99 回合。** Reidel 第一槍宣告成單發射擊，沒有距離。引擎回應一個裁定（`Single shot requires trusted physical distance and reviewed base range`），把整桌的階段設成 `NEEDS_RULING`。他之後的幾句話和 Phil 的都被拒絕，直到守密人清掉它。
- **Lightless 第 31 到 43 回合。** 守密人要查「幼體（深海夢魘）」的數值表。劇本寫的是 `Youngling, deep-sea nightmare`，查不到，守密人再用英文標題問一次才找到。接著的戰鬥從一個缺少劇本出處的敵人攻擊開始，暫停等待裁定，每位玩家的射擊都被拒絕（`Resolve the current action before declaring another`），直到守密人取消它。之前的兩次裁定都失敗，原因是 `Unknown weapon; explicit definition required`。
- **Log。** 結構化的 `turn.observed` 只有工具名稱，看不出呼叫失敗。原因只在回合結束時寫的那一行文字 log 裡，逾時的回合不會寫。

## 變更

- **射擊沒說距離，就當作最近的射程，戰鬥繼續。** 不論誰開槍、武器有沒有基礎射程，命中都是一般難度；有距離分段的武器（霰彈槍）取最近的一段傷害。給了距離就照舊；距離超過武器極限射程、有距離卻沒有基礎射程可判斷、距離無效，仍需裁定。敵人的射擊沒給距離也不再是裁定，但沒有彈藥仍然是。這條規則放在 `combat_rules`（`resolve_range_difficulty`、`resolve_weapon_damage`），所以玩家宣告、恢復裁定、敵人規劃的射擊共用它。
- **用玩家的說法叫生物，也找得到數值表。** `get_enemy_stat_block` 原本只用名字或別名完全相符去對劇本的 NPC 索引。現在找不到時，退到最接近的索引條目，也就是 `add_npc_to_combat` 已經在用的查法，所以「幼體（深海夢魘）」能對到列有英文標題當別名的「深海夢魘（幼體）」。回傳會寫出找到的標題，猜錯看得出來；完全對不上的名字仍然失敗並列出所有標題。
- **工具失敗當下就留下紀錄。** `turn.observed` 多了 `ok`；呼叫失敗時再附上戰鬥停在的階段。原因（工具錯誤的前 300 字）以一行警告寫進文字 log：`tool <名稱> failed: <錯誤>`，因為結構化事件不放自由文字。

## 不變更

- **引擎玩不了的敵人開場攻擊**（Lightless 的毒針，且缺少劇本出處）仍然會暫停戰鬥、等守密人。引擎要不要自己跳過這類行動，是另一個決定。
- **守密人替已經行動完的角色再宣告一次攻擊**（Camp Sunny 三次）是守密人的錯，不變。

## 驗證

- 射擊沒給距離在任何基礎射程下都是一般難度，包括沒有基礎射程；霰彈槍沒給距離取最近一段；無效或衝突的距離、有距離卻沒有基礎射程，仍需裁定（`tests/test_combat_rules.py`）。
- 玩家的單發射擊沒給距離，會被宣告並以一般難度擲骰；給 `distance_yards=60` 就不是；給 `distance_yards=1000` 仍需裁定且不消耗彈藥（`tests/test_combat_state_machine_integration.py`、`tests/test_combat_mechanics_coverage.py`）。敵人的單發射擊沒給距離，會被宣告並等玩家選擇防禦（`tests/test_combat_flow.py`）。
- 原本靠「沒給距離」製造裁定的測試，改用超過極限射程的距離，仍檢查擁有的武器鍵在重載後保留。
- 「幼體（深海夢魘）」「深海夢魘（幼體）」和英文標題都回傳 Youngling 的數值表；無關的名字仍然失敗（`tests/test_combat_turn_friction.py`）。
- 工具失敗時，結構化事件寫 `ok` 和 `phase`、不含錯誤文字，另有一行文字 log 警告寫原因（`tests/test_turn_phases.py`）。
