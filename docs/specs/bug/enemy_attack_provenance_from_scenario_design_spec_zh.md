# 劇本索引裡有的敵人，攻擊來源由劇本提供

[English](enemy_attack_provenance_from_scenario_design_spec.md) | [文件索引](../../README_zh.md)

分類：`bug`。狀態：**implemented**。基準：`main_v2` 的 `910b133`。

## 問題

在 `main_v2` `5c57ab3` 上的《The Haunting》四人實跑，先攻能前進了（第 5 輪、25 次 `advance_combat_turn`，見[戰鬥回合一定能結束](combat_turn_always_endable_design_spec_zh.md)），但沒有任何敵人出過手：老鼠群每次攻擊都停在「NPC attack requires verified scenario provenance」（`combat_flow.run_enemy_plan`）然後被取消。敵人要出手，戰鬥卡的 `source` 必須有 `url`、`revision`、`sha256`，而沒有任何地方提供：登記工具的 `source` 要模型自己填，模型根本不知道真實的版本或雜湊，守密人提示也沒說要填什麼。

## 修改

`add_npc_to_combat` 或 `initialize_combat` 登記敵人時，如果劇本的 NPC 索引有這隻，缺少的來源由已載入的劇本補上：`url` 為 `scenario:<劇本庫 ID>`、`revision` 為目前章節、`sha256` 為目前的來源雜湊、`extreme_rule` 為 `maximum`。模型自己給的值優先。名稱比對方式和索引血量修正相同（先完全比對，再模糊比對），但門檻較低（0.5，血量修正維持 0.6；專案負責人決定接受的取捨：模型自己編、只和索引 NPC 共用一個字的敵人（鼠王對鼠群）同樣是 0.5，也會拿到來源，實跑若發現誤判再收緊到完全比對或索引別名）。同樣，登記時沒給自己數字的攻擊（交給引擎預設的 25%／1D3）也會拿到來源：模型有時候確實沒給，全部拒絕的話戰鬥又會卡住；實跑若發現憑空的傷害再收緊，因為守密人對生物的稱呼不一定和索引一樣：老鼠對鼠群剛好是 0.5。索引裡沒有的敵人什麼都不補，仍然會停下來要裁定，所以模型自己編的數值不會被當成有來源。

## 不做

索引裡沒有的敵人不補來源；不改攻擊方式、距離或彈藥規則；`extreme_rule` 固定 `maximum`（該貫穿的攻擊仍須由模型自己給 `source`）。《The Haunting》的 NPC 索引有沒有老鼠，取決於抽取結果，這裡不做保證。

## 測試

`tests/test_enemy_source_from_index.py`：索引裡的敵人拿到來源且攻擊能到玩家選擇階段、索引沒有的仍然暫停、模型自己的來源優先、`initialize_combat` 同樣有效，以及較寬鬆的名稱能比對到、無關的名稱不會。

## 後續：未宣告的攻擊方式

實跑（《The Haunting》的老鼠，兩個 `engaged` 攻擊）通過來源檢查後，卡在「NPC attack mode is not explicit/supported」，因為只有單一 `engaged` 攻擊才預設為近戰。現在敵人攻擊的 `attack_mode` 沒寫或不支援一律視為近戰（攻擊或卡片上明確寫 `single_shot` 的仍照彈藥與距離檢查），卡片層級的 `extreme_rule` 也套用到每個攻擊，除非該攻擊自己有設定。目前戰鬥沒有距離的概念，所以不用 `range_band` 區分遠程與近戰。接受的取捨：模型沒標 `single_shot` 的遠程攻擊會當近戰處理，總比戰鬥卡死好。
