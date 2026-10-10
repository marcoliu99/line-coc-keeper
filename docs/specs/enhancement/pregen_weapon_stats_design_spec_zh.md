# 預製調查員帶著自己的武器數值進遊戲

## 問題

《鬼屋》的預製調查員 Evelyn Carter 帶著一把 .38 左輪。審核過的武器目錄（`app/data/combat_weapons.json`）沒有 .38：它引用的來源 Foundry CoC7 wiki 武器表，手槍只有 .22、.25、.32/7.65mm、Luger 和 .45。所以以 `d6a03bb` 跑的 200 回合（2026-10-09）把她的槍當成目錄裡的 .32/7.65mm 左輪，傷害是 1D8，而不是角色卡寫的 1D10。傷害比角色卡低，敘事也講錯了槍名。引擎其實早就能讓角色擁有的武器用自己的定義、優先於目錄（`Character.weapon_instances`，由 `managed_combat._owned_weapon_evidence` 與 `combat_flow._weapon_actor_evidence` 讀取），只是從來沒有地方寫入。

## 修改

- `app/pregen_weapons.py` 依角色卡寫的數值，為每把武器建立一份審核過的定義（`definition_row`）：
  - 技能取自角色卡寫的技能；沒寫就從武器名稱推：手槍／左輪是手槍；步槍、霰彈槍、衝鋒槍、機槍、弓、投擲或各種格鬥專長也依此對應。傷害必須能用目錄的傷害語法解析（`combat_rules.validate_damage`）。
  - 傷害後面寫了「+DB」（或「+半DB」）就照寫的加傷害加值；沒寫的話，槍不加，投擲加一半，近戰全加。
  - 槍是 `single_shot` 攻擊，每發花一顆子彈，極難成功時穿刺；射程（碼，公尺會換算）、彈容量、故障值都照角色卡。故障值寫「00」視為 100。
  - 投擲武器是遠程的 `single_shot`，不耗彈藥；角色卡沒寫射程時，射程為投擲者的 STR/5。
  - 角色卡沒寫的部分，若名稱對得上目錄裡的武器，就沿用那一筆，包括慢速武器的裝填節奏（`rounds_per_shot`，例如弩的兩輪）。
  - 數值無法用來結算的武器（沒有傷害、像 4D6/2D6/1D6 這種依距離分段的霰彈槍傷害、名稱也推不出技能），不建立定義，照舊用目錄。
  - 別名包含角色卡上的原文名稱；左輪或手槍另外加上「左輪／手槍」與英文寫法，玩家說「左輪」就是開這把槍。
- 定義會釘住它所依據的文字：來源為 `scenario:pregen-sheet`，加上那段文字的 sha256 與日期。`combat_rules.parse_weapon_definition` 現在接受 `scenario:` 開頭的來源網址。
- 兩條匯入路徑都會帶入數值：
  - 手寫角色卡的武器區塊讀取 技能／傷害／射程／彈容量／故障。
  - 劇本擷取的回報工具新增 `weapons` 清單（名稱、技能、傷害、射程、彈容量、故障值，只抄卡上寫的）。
- `pregen_to_character` 把每份定義存成角色擁有的武器實例（`weapon_instances[名稱] = {definition_id, catalog_version, scenario_definitions: [定義]}`）；沒寫裝彈數的槍，依定義的彈容量裝滿。
- 玩家說的名稱不是實例的原名、但對得上它的定義時（「左輪」對「.38 左輪」），只要角色只有一把對得上，`managed_combat._owned_weapon_evidence` 就會找到那一把。

## 未做

- 這次改動前已擷取並儲存的預製角色沒有定義；重新擷取該劇本的預製角色後才會有。
- 同一個預製角色若同時有手寫角色卡與劇本擷取，仍以手寫卡的武器整份為準；只有擷取結果才有的定義不會帶過來。

## 測試

`tests/test_pregen_weapons.py`；用「左輪」以角色卡的 1D10 開槍的情況在 `tests/test_combat_wiring.py`。
