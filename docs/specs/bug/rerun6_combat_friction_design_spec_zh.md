# 一場 Codex run 裡的三種戰鬥回合失敗：把鬥毆技能當武器、重試時重查被擋、身上的槍被判歧義

[English](rerun6_combat_friction_design_spec.md)

狀態：**已實作**。基底：`main_v2` 的 `8bcf892`。

## 問題

2026-10-10 在 `8bcf892` 上跑的四人《鬼屋》run（Codex `gpt-6-luna`，200 回合）有 9 個 fallback 回合，其中 6 個來自程式裡的三個原因：

| 回合 | 玩家看到 | 原因 |
|---|---|---|
| 37、38、52 | 「處理這個行動的工具失敗了」 | 玩家說「我以格鬥（鬥毆）攻擊」，守密人把技能名「格鬥（鬥毆）」當成 `weapon_reference` 傳入。徒手武器認得「徒手」「拳頭」「brawl」，卻不認得技能本身的名字，`declare_combat_action` 以「Unknown weapon; explicit definition required」停下，戰鬥卡在 `NEEDS_RULING`。第 38 回合再用同一個行動 ID 換武器重新宣告，被拒絕。 |
| 29 | 「系統發生內部錯誤」 | Executor 第一次嘗試沒改到任何狀態、回傳 `incomplete`，於是這回合重試。重試的第一個請求用同樣參數再查一次敵人資料，`codex_provider` 以 `codex_duplicate_tool_attempt` 擋下：它記錄的呼叫涵蓋整個回合，重試也算在內。當時請求才開始 12.9 秒，和逾時無關。 |
| 28 | 「處理這個行動的工具失敗了」 | 守密人用 `get_weapon_definition` 查 `.38 Revolver`。查詢只看通用目錄，目錄裡有好幾把左輪、沒有 .38，於是回「Ambiguous weapon reference」；但 Evelyn 身上就帶著角色卡自己的 `.38 Revolver`（`pregen_weapon_stats_design_spec`），`declare_combat_action` 也解析得到。拒絕的原因放在 `reason` 而不是 `error`，工具摘要只寫「失敗：未知錯誤」。 |

## 修改

- **鬥毆技能就是徒手攻擊。** 目錄的 `i.weapon.brawl` 加上別名「格鬥（鬥毆）」「格鬥(鬥毆)」「鬥毆」「空手」和 `fist`。不加單獨的「格鬥」：依包含比對它會搶走「格鬥刀」。目錄裡所有既有名稱與別名仍解析到原本的武器（測試逐一檢查整份目錄）。目錄版本不變：沒有任何規則數值改動。
- **回合重試可以重查。** `codex_provider.run_conversation` 裡，查詢類工具（`INFORMATION_QUERY_TOOLS`：各種 `get_*`、`search_scenario`、`search_memory`）若在同一回合較早的對話裡查過，會再送一次。supervisor 只在第一次嘗試完全沒碰遊戲狀態時才重試，而查詢不改任何東西，所以不會重播任何事。同一個對話裡重複同樣的查詢仍會被擋；擲骰和會改狀態的工具在一個回合裡永遠不會送兩次。
- **武器查詢先看調查員自己的武器。** `get_weapon_definition` 多一個選填的 `investigator`；不填就用行動玩家自己的角色。該角色身上的武器照 `declare_combat_action` 的方式解析（角色卡定義、釘選的武器實例），結果標 `owned: true`；否則照舊查通用目錄。拒絕時現在帶 `error`：「查不到確定的武器「左輪」（Ambiguous weapon reference）；候選：…。調查員身上的武器請填 investigator；徒手攻擊填「徒手」。」

## 不改的部分

- 第 71 回合：守密人被公開 1D100 的拒絕（`d100_against_a_characteristic_is_a_check_design_spec`）擋下後，改用 `secret: true` 擲同一個 1D100，這是允許的，但仍沒有檢定可結算。200 回合只發生一次，守密人也需要暗骰，維持原狀。
- 擲骰與改狀態工具的重複規則，以及整個回合共用的工具預算。

## 驗證

- `tests/test_combat_rules.py`：鬥毆技能的六種寫法都解析到 `i.weapon.brawl`；「格鬥刀」不會；目錄所有名稱與別名都解析到自己。
- `tests/test_codex_provider.py`：同一回合的第二個對話可以重複查詢；回合內重複的改狀態呼叫、同一對話內重複的查詢仍會被擋。
- `tests/test_combat_wiring.py`：角色卡的 `.38 左輪` 用名稱、以及填了 `investigator` 的「左輪」都查得到；替身上沒有武器的人查時，`error` 列出候選。
