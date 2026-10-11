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
- **敵人的攻擊沒有劇本出處，就當肉搏。** 戰鬥卡沒有出處時，`run_enemy_plan` 會停在 `NPC attack requires verified scenario provenance` 暫停戰鬥，而且任何 resume 參數都救不了：暫停的動作沒有武器，resume 拿空的武器名去查，回 `Unknown weapon; explicit definition required`，只有取消能成功（Lightless 第 38 到 43 回合）。現在沒有出處的攻擊，用戰鬥卡自己帶的數值當作肉搏出手，極限成功規則取 `maximum`，玩家照一般攻擊選擇防禦。有出處的卡不變，包括沒有彈藥的單發射擊，仍然要裁定。
- **敵人引擎玩不了的特殊能力，自己跳過並回傳可敘述的結果。** `run_enemy_plan` 原本停在 `NPC special/movement plan requires an explicit ruling` 暫停戰鬥。現在敵人這一回合當作跳過：用計畫的公開提示敘述，不套用任何機制（毒針的效果不會生效，只剩敘事），戰鬥繼續。回傳說明這件事（`special_given_up`、`narration`，並註明若劇本寫明效果，用對應的工具套用）。重送會得到同一份回傳。
- **用玩家的說法叫生物，也找得到數值表。** `get_enemy_stat_block` 原本只用名字或別名完全相符去對劇本的 NPC 索引。現在也接受用詞相同、只是順序或括號不同的名字（忽略名字後面桌上用的字母或數字），所以「幼體（深海夢魘）」能對到列有英文標題當別名的「深海夢魘（幼體）」。絕不用相似度比對：「深潛者」和「深潛者（幼體）」很像，卻是不同的生物，拿錯數值表會讓守密人登記到別種生物的 HP 和攻擊。兩個條目用詞相同時，名字有歧義，視為找不到。完全對不上的名字仍然失敗並列出所有標題。
- **工具失敗當下就留下紀錄。** `turn.observed` 多了 `ok`；呼叫失敗時再附上戰鬥停在的階段。原因（工具錯誤的前 300 字）以一行警告寫進文字 log：`tool <名稱> failed: <錯誤>`，因為結構化事件不放自由文字。

## 不變更

- **留下來的裁定由守密人判斷。** 距離超過極限射程、有距離卻沒有基礎射程、距離無效、敵人射擊沒有彈藥，仍然各自暫停戰鬥，等守密人判斷。守密人的上下文帶有每個未完成的動作和它在等什麼的原因（`keeper_only_combat.actions` 裡的 `needs_ruling`），所以守密人讀得到要裁定什麼；有一個測試固定敵人沒有彈藥這個情況。
- **守密人替已經行動完的角色再宣告一次攻擊**（Camp Sunny 三次）是守密人的錯，不變。

## 驗證

- 射擊沒給距離在任何基礎射程下都是一般難度，包括沒有基礎射程；霰彈槍沒給距離取最近一段；無效或衝突的距離、有距離卻沒有基礎射程，仍需裁定（`tests/test_combat_rules.py`）。
- 玩家的單發射擊沒給距離，會被宣告並以一般難度擲骰；給 `distance_yards=60` 就不是；給 `distance_yards=1000` 仍需裁定且不消耗彈藥（`tests/test_combat_state_machine_integration.py`、`tests/test_combat_mechanics_coverage.py`）。敵人的單發射擊沒給距離，會被宣告並等玩家選擇防禦（`tests/test_combat_flow.py`）。
- 原本靠「沒給距離」製造裁定的測試，改用超過極限射程的距離，仍檢查擁有的武器鍵在重載後保留。
- 敵人這一回合是特殊能力時，會不擲骰地跳過並附上敘述，重送得到同一份回傳，也能正常推進（`tests/test_combat_state_machine_integration.py`）。
- 沒有出處的敵人在戰鬥開始時以肉搏出手，等玩家選擇防禦，沒有待決的裁定（`tests/test_combat_state_machine_integration.py`、`tests/test_enemy_source_from_index.py`）；跳過回合的測試改成用空槍讓下一個敵人的回合需要裁定（`tests/test_combat_skip_turn.py`）。
- 「幼體（深海夢魘）」「幼體（深海夢魘）A」「深海夢魘（幼體）」和英文標題都回傳 Youngling 的數值表；無關的名字、很像但不同的名字（「深潛者」「深潛者頭目」）、有歧義的名字仍然失敗（`tests/test_combat_turn_friction.py`）。
- 工具失敗時，結構化事件寫 `ok` 和 `phase`、不含錯誤文字，另有一行文字 log 警告寫原因（`tests/test_turn_phases.py`）。
