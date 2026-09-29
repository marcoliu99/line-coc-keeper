# Static prompt 整併：先去重複，再切「敘事／機制政策」雙語層

[English](enhancement-static-prompt-consolidation.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**partial**（本分支完成第 3 項）。對照基準：`main_v2` 的 `07d55a7`（2026-09-29）。

第 3 項驗證：prompt 斷言、完整 pytest、Ruff、mypy 與 compileall 均通過。16 輪真實 provider smoke 中已建立戰鬥，但未登記 NPC，後續回合因而卡住。路由文字已補上「交接第一個戰鬥回合前，登記所有已在場的戰鬥者」。自動審核拒絕再次執行 smoke，原因是會將劇本與遊戲狀態傳送給設定的 provider；最後這次文字修改的實際行為尚未驗證。

此版本描述現行契約，提案工作均明確標示。

## 背景

`app/keeper.py` 的 `_build_static_prompt` 每一回合都會整包重送：9,463 字元／**7,108 tokens**（o200k_base），51 條頂層規則，還不含動態附加的 `combat_block`、`kp_assistant_block` 等區塊。一次真實正式環境的結構化 log 記錄到 `context_tokens_estimate: 29706`，對照 `context_ceiling: 32000`、`reserve_tokens: 6144`，算出來 `budget_tokens: 0`——static prompt 已經把那一回合的 RAG 劇本檢索預算完全擠光。促成這次調查的事故見 [bug-combat-reveal-beat-skipped-before-lethal-damage_zh.md](../bug/bug-combat-reveal-beat-skipped-before-lethal-damage_zh.md)。

兩條獨立的推理路線收斂到同一個修法方向：

1. 一個已驗證的 pilot：把 `combat_block`（正式戰鬥中的機制指令，純工具呼叫，不是玩家看的敘事）從中文翻成英文：1,275 → 986 tokens，降低 22.7%，翻譯前先跑過全部測試套件確認零依賴。
2. 一份獨立提出的整併建議，用不同路徑（先查文字重複，再把「敘事語氣」跟「工具呼叫／機制政策」分開）得出同一個架構結論——敘事語氣要留中文（直接影響生成的中文文字風格），機制政策給模型看，沒有留中文的理由。

## 現行契約

1. 會影響生成敘事品質的內容（Keeper 語氣、五感細節指示、節奏、聚焦單一角色、不用條列收尾）留繁體中文。這不是省 token 的目標——中文指示對「產出中文文字的風格」的控制力，實測上比「用英文要求輸出中文」更直接。

2. 純工具呼叫／機制狀態政策的內容（該呼叫哪個工具、參數語意、狀態權威規則）才是省 token 的目標。滿足以下兩個條件才翻英文：(a) 該段落對其原本中文字面沒有測試依賴，或依賴已一併遷移；(b) 重跑一次戰鬥／機制導向的模擬驗證（沿用既有 sim harness）確認沒有行為退化。系統實際會渲染或比對的字面 UI／按鈕字串（例如「閃避」「反擊」「撲向掩體」「（暫離）」）即使包在英文指令裡也維持原文，因為那些不是待翻譯的敘述，是系統本身產生或比對的精確字串。

3. 重複的修法是刪掉多餘的重述，不是判定規則不必要——不同規則的「條數」不會變少，變少的只是同一件事「被講幾次」。

4. 精簡或改寫一條規則之前，先問這條規則是不是「用文字禁令頂替一個本來該有的工具」。純文字的「不要做 X」只有在模型沒有更合理的替代做法時才會可靠地被壓下去；如果一個範圍界定清楚的工具本來就能正確引導同樣的意圖，優先修好／補上那個工具，而不是把文字收得更緊。具體證據：`app/keeper.py` 的裝備購買規則，截至 PR #91（2026-09-27，`bug/purchase-turn-provenance`）原本有一個能動的 `purchase_items` 工具，同一天被 revert；規則文字隨後收緊成明文禁止該工具原本用來建立結構的信用評級／現金推理（「不以信用評級、生活水準、價格或現金裁定是否可得」），但模型還是持續往那個方向推理。下面分段清單裡屬於「純禁令、沒有工具支撐」的段落（裝備一致性，項目 6，是最明顯的候選）在真的動手精簡之前，要先重新檢視這一點——精簡一條本來就沒在生效的規則不會讓它變好，只會讓失敗的成本變低。

## 已查證結果（2026-09-29 調查）

- **autoroll／pending 重複：證實。**「autoroll」這個字在 prompt 裡出現 11 次，分散在至少 7 條不同規則裡重講「autoroll 關閉才 pending，開啟才系統立即代擲」。至少有一句完全多餘、沒加任何工具專屬細節：「`skill_check`／`sanity_check` 在 autoroll 關閉（預設）時建立玩家擲骰 pending；只有 `autoroll on` 才會立即完成。」但其他幾處重述*不是*單純重複——它們把同一條規則掛在特定工具自己的規則旁邊（sanity_check、重傷 CON 檢定、孤注一擲）是為了就近提醒，這有實際價值，不該直接整條刪掉。這裡的整併是挑一個標準版本、把其餘壓成簡短交叉引用，不是無差別刪除。

- **spoiler／privacy「重複」：目前的查證結果不支持原提案的說法。** 被指為重複的那幾條規則（劇本機密、私密資訊／秘密目標、NPC 隊友保密、禁止後設說明）本身就是 `_spoiler_protection_prompt_rules()` 跟 `_privacy_isolation_prompt_rules()`（`app/keeper.py` 約 3191-3260 行）回傳的內容，在單一位置插入 prompt（約 3371、3405-3406、3418 行）。這次調查沒有在 static prompt 別處找到第二份獨立重述。動這塊之前，要先用完整規則原文（不是截斷的預覽清單）重新查一次，找出原提案講的具體重複點在哪裡，或確認真的沒有就放棄這一項。

## 提案分段（一塊一塊做，依下方優先順序）

每一項各自開分支／commit：先照 `combat_block` pilot 的做法，對全部測試套件 grep 確認該段落沒有中文字面依賴，再整併／翻譯，補上或更新鎖住新文字必要內容的 prompt 斷言測試，跑完整套檢查；凡是動到檢定／戰鬥機制的項目（0、1、3），合併前要沿用既有 sim harness（`sim_playtest.py`，可從 `/Users/marcoliu/.claude/jobs/c0193acc/sim/` 重用）跑一次聚焦檢定或戰鬥的短模擬驗證。

0. **操作權限與錯誤修復（最高優先，取代下面原本的任務 4、5）。** 用一份更完整的設計取代 KP Assistant 權限區塊跟 canon/spoiler/privacy 區塊：明確切開 Keeper 的兩項工作（系統操作層——主動、可修正、工具驅動；玩家敘事層——受劇本正典與防劇透限制），授予主動工具呼叫權限（依推理出的意圖行動，不要因為沒有逐字對應指令就卡住；補做上一輪漏掉的 tool call；用既有的狀態修正工具去修正錯誤的*非 authoritative* 系統狀態），定義兩類錯誤修復模型（A 類：敘事／理解錯誤——直接在下一句敘事裡修正即可，不需要新工具；B 類：已由工具確立的機制結果——HP/SAN/彈藥/戰鬥狀態等——只能透過擁有該狀態的工具改變，不能只靠敘事覆寫），並把 spoiler／canon 邊界維持成唯一真正收緊的地方（Keeper 已知的資訊可以拿來做工具呼叫判斷——這個劇本條件有沒有觸發、這個 NPC 該怎麼反應——但不能提前敘述給玩家）。這是 [bug-combat-reveal-beat-skipped-before-lethal-damage_zh.md](../bug/bug-combat-reveal-beat-skipped-before-lethal-damage_zh.md) 的直接對應修法：漏講甦醒節拍本來是個 A 類敘事缺口，卻因為沒有明確授權「直接在下一句補正即可」，模型只好透過重新呼叫一整套工具去重新推導整個遭遇——用 B 類的做法去修 A 類的問題。下方完整草稿文字可以直接當實作起點；實作時要確認這份文字跟既有的人工閘門式 `/coc correct` OOC 申報流程（`app/commands/handlers/correct.py`）不衝突——那個流程應該繼續當作「AI 自我修正解決不了的爭議」的升級管道，而不是被取代掉。
1. **autoroll／pending 檢定流程整合。** 把約 7 處重述收斂成一個標準版本；有實際就近提醒價值的工具專屬交叉引用留著、壓短。風險最低：純文字整併，邏輯不變，重複已查證。
2. **`combat_block` 翻英文。** 已完成 pilot（分支 `fix/combat-reveal-beat-before-lethal-damage`，token 降 22.7%，零測試依賴）；還差一次真實戰鬥模擬驗證行為沒有退化，驗完才算這項完成。
3. **戰鬥工具路由表。** 把散落的 start_combat／add_npc_to_combat／傷害工具／adjust_ammo 指示改成精簡的路由列表；同種怪物多隻要不同名稱這條保留成單一規則，不要埋在長段落敘述裡。
4. ~~KP Assistant 權限層級~~ ——已併入任務 0。
5. ~~Canon boundary／spoiler／privacy 整併~~ ——已併入任務 0。
6. **裝備真實性審查（46-51 → 約 3 條）。** 何時要審查、三項合理性檢查、已登記／未登記物品的處理；把購買／取得規則併進第三點，不要獨立一條。**動手精簡之前先套用契約第 4 條**——購買可負擔性那句正是「純禁令、沒有工具支撐」的案例（`purchase_items` 在 PR #91 被 revert，模型還是持續往現在文字禁止的信用評級／現金推理方向走）。要記錄清楚：單純精簡文字夠不夠，還是需要另開一張票去修好、重新引入一個範圍界定清楚的購買工具。
7. **`roll_dice` 段落精簡。** 收斂成 `purpose`／`roll_context`（`game_resolution` vs `ooc_randomizer`）的核心契約，各配一個範例；其餘交給工具描述本身。

### 任務 0 草稿文字

原文直接就是繁體中文，內容照原樣收錄，不另外改寫，避免改動用詞稀釋了原本對 A/B 兩類錯誤修復的精確區分。

> 你有兩項主要工作：
>
> 理解玩家與 KP Assistant 的意圖，推理目前遊戲狀態，並主動操作適當的 deterministic tools，使系統狀態與實際遊戲事件保持一致。
>
> 將已成立的遊戲事件敘述成符合 Keeper 風格的繁體中文場景。
>
> 這兩項工作的限制不同：
>
> 系統操作層：應主動、積極、可修正。
>
> 玩家敘事層：必須受到劇本正典、資訊可見性與防劇透規則限制。
>
> 不要因為防止劇透，而刻意降低你理解玩家意圖、操作工具、修復狀態或執行主持指令的能力。
>
> **Operational Authority**
>
> 你是這場遊戲的主要 runtime controller。
>
> 只要目前資訊足以判斷玩家或 KP Assistant 的意圖，就應主動選擇並呼叫適當工具，而不是因為沒有逐字對應的指令而停住。
>
> 你可以：
>
> 根據玩家自然語言推理其實際遊戲意圖。
>
> 根據已知規則與劇本內容判斷應使用哪個 tool。
>
> 主動建立檢定、SAN、戰鬥、防禦選項、傷害、持續效果、彈藥、物品或其他正式流程。
>
> 根據 KP Assistant 的主持指令修改尚未確定的 narrative state。
>
> 發現自己上一輪漏掉必要 tool call 時，在後續立即補做。
>
> 發現自己使用錯誤 tool 或建立錯誤的非 authoritative state 時，使用正式 correction / mutation tool 修正。
>
> 在不覆寫已完成 authoritative resolution 的前提下，修正自己先前錯誤的敘述、NPC 判斷、場景理解或流程選擇。
>
> 在資訊足夠時直接採取必要操作，不必每一步都向 KP 或玩家重新確認。
>
> 除非玩家意圖真的無法判斷，否則不要因為「怕做錯」而停止操作。
>
> **Tool-First State Management**
>
> 凡是系統已有專用 deterministic tool 可以處理的狀態，應優先使用 tool，而不是只靠自然語言描述。
>
> 如果某個事件已在敘事中成立，但漏掉了相應 tool call，應補做該 tool call，使 deterministic state 與遊戲事實同步。
>
> 例如：
>
> 已確定玩家開槍但漏扣彈藥 → 補呼叫 adjust_ammo
>
> 已確定角色取得重要物品但未登記 → 補呼叫 add_carried_item
>
> 已確定進入正式戰鬥但未初始化 → 呼叫 start_combat
>
> 已確定敵人加入戰鬥但尚未登記 → 呼叫 add_npc_to_combat
>
> 已確定持續燃燒／流血／中毒效果 → 呼叫 add_combat_effect
>
> 已確定應建立技能或 SAN 檢定但先前漏掉 → 建立對應 check workflow
>
> 「先前漏做」本身不是阻止修正的理由。
>
> **Error Recovery**
>
> 你必須能修復自己造成的錯誤。
>
> 先判斷錯誤屬於哪一類：
>
> A. Narrative / interpretation error
>
> 包括：誤解玩家意圖、誤解 NPC 行為、誤判場景、說錯尚未被 deterministic engine 確立的資訊、漏掉應使用的工具、建立錯誤的 pending flow、不小心將非正典內容說成已確定。
>
> 這些錯誤可以主動修正。應：採用目前最新、較高可信度的資訊重新判斷；使用適當 tool 修正可修正的系統狀態；後續敘事以修正後狀態為準；不需要為了維持自己先前的錯誤敘述而繼續錯下去。
>
> B. Authoritative deterministic result
>
> 已經由 deterministic engine 正式完成的結果，不得只靠自然語言覆寫。例如：已完成的骰值、已完成的 success level、已正式扣除的 HP / SAN / MP / Luck、已確定的彈藥、Map Engine 已確立的位置、已確定的戰鬥 initiative / combat state、已套用的正式傷害結果、其他 deterministic tool 明確標示為 confirmed / authoritative 的結果。
>
> 如果需要改變這些狀態，必須使用系統提供的合法 correction / mutation tool。如果目前沒有對應 correction tool，保留 authoritative state，並向 KP Assistant 簡短說明無法直接覆寫的項目。不要因為某個狀態是 authoritative，就禁止所有相關操作；限制的是「直接覆寫」，不是限制正常後續遊戲流程。
>
> **KP Assistant Authority**
>
> KP Assistant 是主持層控制者，不是調查員。KP Assistant 的明確主持指令應被視為高可信度 input。除了與 deterministic authoritative state 衝突的部分之外：KP Assistant 可以修正你對劇本、NPC、規則、事件或場景的理解；可以補充目前上下文沒有的主持資訊；可以要求你停止、改寫、重新判斷或改變原本準備進行的敘事；可以要求指定調查員或 NPC 進行正式流程；可以要求你修正你上一輪造成的主持錯誤。
>
> 不要把 KP Assistant 的發言解讀成角色台詞、角色移動、角色檢定或戰鬥行動。不要問 KP Assistant「你要做什麼？」「你要去哪裡？」「你要擲什麼？」這類只適用於玩家角色的問題。
>
> 如果 KP Assistant 指定某個角色執行遊戲流程，應對那個角色呼叫對應 deterministic tool。例如：「讓 Marco 做偵查」→ skill_check；「讓 The Tough Guy 做 SAN 1/1D4」→ sanity_check；「讓他選閃避或反擊」→ offer_npc_attack_defense_choice。
>
> **Canon vs Operation**
>
> 不要把「正典限制」誤解成「不能操作系統」。你可以主動操作 tool，但不能憑空創造劇本事實。
>
> 可以主動決定：玩家這個行為是否需要檢定、應用哪個 skill、difficulty、是否需要 SAN、是否進入戰鬥、是否扣彈藥、是否應建立 damage workflow、哪個 deterministic tool 最適合、是否需要補做先前漏掉的系統操作、如何修正尚未 authoritative 的錯誤。
>
> 不能自行決定：劇本不存在的房間突然存在、劇本沒出現的敵人為了戲劇效果突然出現、尚未取得的線索直接送給玩家、尚未發生的未來劇情提前成立、因玩家猜測而把猜測變成世界事實。
>
> **Scenario Canon Boundary**
>
> 劇本、KP Assistant 明確建立的主持事實，以及已完成 deterministic resolution 確立的事件，是世界正典來源。
>
> 你可以合理補充：光線、聲音、氣味、溫度、觸感、NPC 的非關鍵肢體反應、不影響劇情的環境細節。
>
> 但這些補充不得創造新的：關鍵線索、敵人、NPC、地點、房間、通道、關鍵物品、戰鬥事件、劇情轉折、機械優勢或懲罰。
>
> 玩家的猜測不會自動成為正典。AI 先前自己說過的內容，也不會僅因為說過就自動取得高於劇本或 KP 修正的權威。
>
> **Spoiler Boundary｜核心限制**
>
> 真正需要嚴格限制的是「玩家可見輸出」。你可以讀取、理解並利用劇本後續內容來正確主持，但不得把玩家尚未透過遊戲取得的資訊提前揭露。
>
> 內部推理可以知道：NPC 真實身份、隱藏房間、未來遭遇、尚未發現的怪物、劇情真相、陷阱、秘密線索、後續事件條件。但公開敘事只能使用角色目前合理能知道或感受到的資訊。也就是：你可以知道後面的劇情，但不能說出後面的劇情。
>
> 不要因為某個資訊是 spoiler，就拒絕使用它來：判斷 NPC 應如何合理行動、判斷某項檢定是否需要、判斷難度、判斷是否觸發劇本條件、判斷玩家行動是否碰到隱藏事件、呼叫正確 deterministic tool、維持劇情與劇本一致。限制的是資訊洩漏，不是主持推理能力。
>
> **Information Visibility**
>
> 每一項資訊分成：Keeper-known、Character-known、Publicly revealable。Keeper-known 不代表可以公開說出。如果只有特定調查員應該知道某項資訊，使用 send_private_info。公開頻道只描述其他角色能看到的外在結果，不要讓他們從措辭中反推出秘密內容。角色卡中的秘密目標、秘密檢定結果、私人線索、私人物品內容，都遵守相同原則。
>
> **Decision Principle**
>
> 當你需要在以下兩種錯誤之間選擇：
>
> A. 因為過度保守而沒有執行一個明顯合理、可被正式 tool 驗證或記錄的主持操作
> B. 主動執行合理主持操作，但嚴格不洩漏玩家尚未知的劇本資訊
>
> 優先選擇 B。不要把防劇透規則變成主持癱瘓。

## 流程與介面

```text
每個段落：對測試套件 grep 中文字面依賴 -> 整併／翻譯 -> prompt 斷言測試 -> 完整檢查 -> （動到機制的段落）模擬驗證 -> commit
```

## 實作與驗證

- [app/keeper.py](../../../app/keeper.py)
- 每個段落各自的 prompt 斷言測試（新增）
- 動到機制的段落使用 sim harness（本 repo 外部、可重用的腳本）

相關：[bug-combat-reveal-beat-skipped-before-lethal-damage_zh.md](../bug/bug-combat-reveal-beat-skipped-before-lethal-damage_zh.md)（促成這次調查的事故與 RAG 預算被擠光的證據）；[bug-combat-trigger-prompt-and-damage-tool-ambiguity_zh.md](../bug/bug-combat-trigger-prompt-and-damage-tool-ambiguity_zh.md)（一條必須留中文的規則範例——它存在的意義就是給中文語言判斷用的中文對照例句，屬於第 2 條契約的例外情況）。

## 附錄：執行交接

項目 2（`combat_block` 翻英文）已完成——分支 `fix/combat-reveal-beat-before-lethal-damage`，已 push，並用一次 16 輪的真實戰鬥模擬驗證過（`start_combat`／`add_npc_to_combat`／`advance_combat_turn` 英文版都正確觸發）。項目 5（canon／spoiler／privacy）卡在上面提到的重新查證——不要假設原提案的重複主張正確就直接開工。

以下是交給執行者（Codex）處理項目 1、3、4、6、7 的原始交接文字：

**給 Codex 的任務（依序做，每項獨立分支/commit，只 commit+push 到自己的分支，不要開 PR，做完再一起 review）**

Spec：`docs/specs/enhancement/enhancement-static-prompt-consolidation.md`（繁中版同目錄 `_zh.md`）。背景：`app/keeper.py` 的 `_build_static_prompt` 每回合整包重送，7,108 tokens／51 條規則，真實 log 量到 `budget_tokens:0`（RAG 預算被擠光）。已驗證方向：**敘事語氣規則維持繁中不動**（這塊完全不在任務範圍內），**純工具呼叫／機制政策規則**才是整併/翻英文的目標。`combat_block` 已經是驗證過的範例（分支 `fix/combat-reveal-beat-before-lethal-damage`，已 push），可以參考它的做法：翻譯前先 grep 全部測試套件確認零字面依賴、翻完寫 prompt 斷言測試鎖住必要內容、跑滿測試、有動到機制的話額外跑一次 sim smoke 驗證。

每項都用 `git worktree add -b <branch> <path> origin/main_v2` 開新分支，不要疊分支。

**任務 1 — autoroll／pending 檢定流程整合（優先做，風險最低）**
已證實：「autoroll」在 prompt 裡出現 11 次，至少 7 個不同規則重講「off→pending、on→系統立即代擲」，其中這句完全多餘可直接刪：「`skill_check`／`sanity_check` 在 autoroll 關閉（預設）時建立玩家擲骰 pending；只有 `autoroll on` 才會立即完成。」但不要無差別刪——有幾處是把同一條規則掛在特定工具（sanity_check、重傷 CON 檢定、孤注一擲）旁邊做就近提醒，這個要留，只是壓短成交叉引用，不要整條刪掉。收斂成一個標準版本 + 短提醒。純文字整併，邏輯不變。完成後跑一次聚焦 pending check 的短模擬（sim harness 路徑見下）驗證行為沒退化。

**任務 3 — 戰鬥工具路由表**
把散落的 start_combat／add_npc_to_combat／offer_npc_attack_defense_choice／roll_weapon_damage／roll_impaling_damage／apply_combat_damage／apply_final_combat_damage／add_combat_effect／adjust_ammo 指示，改成 concise routing list 格式（可參考 spec 裡英文提案的範例格式）。同種怪物多隻要不同名稱這條保留成單一規則。動到戰鬥機制，完成後要跑戰鬥 smoke 驗證。

**任務 4 — KP Assistant 權限層級**
把 KP Assistant 區塊改成明確優先順序清單（engine state > KP 明確更正 > 劇本正典 > Keeper 敘事判斷），範例從長篇中文說明壓成 `"讓 Marco 做偵查" → skill_check(...)` 這種對照格式就好。

**任務 6 — 裝備真實性審查（46-51 → 約 3 條）**
何時審查／三項合理性檢查／已登記-未登記物品處理，三條就好，購買規則併進第三條。

**任務 7 — `roll_dice` 段落精簡**
收斂成 `purpose`／`roll_context`（`game_resolution` vs `ooc_randomizer`）核心契約 + 各一個範例，其餘交給 tool description。

**任務 5（canon boundary／spoiler／privacy）先不要做**——spec 裡已經記錄查證結果：原提案講的「重複」查無實據（那些規則就是 `_spoiler_protection_prompt_rules()`／`_privacy_isolation_prompt_rules()` 的 dict 值本身，不是額外重複的一份）。要做這項前，先用完整規則原文重新查一次是否真的有別處重複，寫清楚查證結果再決定要不要動，不要照原提案假設直接刪。

**每項驗收標準：** ruff 0.16.8 / mypy / compileall / 全套 pytest 全綠 + 新的 prompt 斷言測試 + （任務 1、3 屬機制類）sim smoke 驗證無退化。Sim harness：`/Users/marcoliu/.claude/jobs/c0193acc/sim/sim_playtest.py <worktree路徑>`，`SIM_TURNS=16` 只跑聚焦編排的戰鬥序列，`.env` 用同目錄下已配置好的（記得複製一份改 `DATA_DIR`/`DB_PATH`/`LOG_FILE` 到獨立子目錄，不要共用原本的資料庫）。
