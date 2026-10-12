# 戰鬥武器目錄的中文名稱

[English](weapon_chinese_names_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `30d8094`，接在 `coc7_glossary_expansion_design_spec_zh.md` 之後，那份規格沒有收武器。

## 當初為什麼沒收，現在改了什麼

最先找到的英中武器表（Keeper_CoC-TRPG）依口徑把武器合併：英文標籤是自己取的（「Small Auto」），一格中文可能放好幾種武器，所以不用。這次用另外兩份：RuleWhisper 轉錄的中文規則書武器表（簡體，98 列，照規則書順序），以及規則書第 401 頁武器表的繁體翻譯（TRPGLine-system-rule，MIT，英文列在旁邊）。第二份的步槍表有列位移（英文的 `.30-06 Semi-Automatic Rifle` 沒有對應的中文列），有些名稱也不是規則書的（把栓動寫成「瓶式槍」，把 blackjack 寫成「海狼棒」），所以它只用來核對第一份，不單獨當來源。

維護者要求直接擴充專案自己的 `app/data/combat_weapons.json`，不另開檔案。

## 變更

`combat_weapons.json` 裡規則書有對應列的武器（49 件中的 48 件；徒手是技能，不是表中的一列）各加：

| 欄位 | 意義 |
|---|---|
| `name_zh` | 規則書的繁體名稱 |
| `name_zh_cn` | 規則書的簡體名稱，照 RuleWhisper |
| `name_sources` | `coc7_glossary/sources.json` 的來源編號（`s22` RuleWhisper、`s23` TRPGLine） |
| `name_zh_converted` | 繁體寫法是簡體用 OpenCC 轉來、還沒核對時才有；目前沒有武器帶這個標記 |
| `name_zh_generic` | 規則書那一列是一類武器，不是一件武器（小型刀具、大型劍，以及目錄裡 `Axe` 對到的伐木斧）時才有 |

- **名稱以規則書為準。** 兩份來源一致時（12 件，忽略異體字）直接用繁體原文。不一致時（34 件）原本用簡體規則書名稱轉繁體並標記，之後由維護者逐一核對：18 件改成台灣玩家慣用的名稱（Blackjack 悶棍、Rapier 細劍、Sword 劍、Rock, Thrown 投擲用石頭、栓動式與槓桿式步槍、Derringer 德林加、Lewis 路易士、Browning 白朗寧等），16 件確認不變，標記移除。維護者給的其他說法當作別名（皮夾鉛棍、刺劍、投石、標槍、截短型，以及湯普森的芝加哥打字機）；原先轉出來的名稱仍是玩家可以輸入的寫法。目錄的 `Bergmann MP181/MP2811` 依維護者的判斷是 MP18 I 與 MP28 II 的誤印，名稱寫成貝格曼 MP18 I / MP28 II，目錄的英文名稱不變。Rapier 不再是類別名稱（細劍指的就是這件武器），所以 `lookup_weapon` 認得它。弩和維克斯機槍不在 RuleWhisper，繁體名稱只來自第二份（2 件）。
- **戰鬥不受影響。** `weapon_catalog()` 建立定義前先丟掉這五個新欄位。每個 `name_zh` 也加進 `aliases`，玩家打規則書的名稱就找得到武器；測試確認每一個都對到自己的武器（自動武器、絞索照舊要守密人裁定）。
- **字典讀取，只供指名查詢。** `glossary_entries()` 為每件有名稱的武器加一筆 `weapon` 詞條，放在獨立的 `weapons` 表，用 `lookup_weapon()` 查。劇本文字比對、以及改寫模型詞條名稱用的是 `terms` 表；武器的英文標籤同時是姓氏（「Detective Thompson」）、牌戲（「blackjack」）或船首（bow），所以武器一件都不放進 `terms`。詞條內容：英文是目錄名稱和倒過來的寫法（「Spear, Thrown」與「Thrown Spear」），中文是 `name_zh` 和 `name_zh_cn`，標準名稱是 `name_zh`。有 `name_zh_generic` 的武器不收，所以「a small knife」不會被硬寫成小型刀具，「a sword」也不會變成大型劍。別名不當英文寫法：「axe」、「revolver」、「rifle」太常見，指不出單一武器。

沒收：目錄沒有的武器（電擊器、催淚噴霧、手榴彈、地雷、重武器）。它們缺的是規則，不是名稱，而目錄是戰鬥規則的資料。

## 驗證

`tests/test_coc7_glossary.py`：45 件以上的武器有名稱，且來源編號都存在；每個規則書名稱都對到自己的武器；目錄在沒有名稱欄位時照樣載入；武器可用英文和任一種字體查到；一類武器的名稱不會硬套到文字上。另有一個測試固定維護者給的名稱。
