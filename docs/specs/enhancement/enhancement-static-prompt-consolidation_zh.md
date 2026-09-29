# Static prompt 整併：先去重複，再切「敘事／機制政策」雙語層

[English](enhancement-static-prompt-consolidation.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**implemented**；整合分支以 2026-09-29 的 `main_v2` `5199139` 為基準。已整合任務 0、1、2、3、5、6、7；任務 4 由任務 0 取代。

敘事語氣仍用繁體中文。工具路由及四條可切換的防劇透／隱私規則使用英文。任務 0 的操作權限、A／B 類修錯與 KP Assistant 語意保留。`/coc correct` 仍是玩家爭議的 OOC 升級管道；單憑申報不會成為正典。已確立的引擎狀態只能透過擁有該狀態的正式工具修改。

六個已審查分支依序整合：操作權限（0）、正典／隱私（5）、autoroll（1）、戰鬥路由與沉睡敵人甦醒（2／3）、裝備（6）、骰子（7）。任務 0／5 重疊處保留任務 0 的權限和修錯語意，把可切換的可見性規則翻成英文，並保留任務 5 的四條明文規則。任務 7 的骰子契約放入現行 KP Assistant 機制區塊。

任務 0 先前的真實 provider 16 輪模擬沒有執行例外，但發現另一項權限缺口：`add_carried_item` 不在 KP Assistant 的允許工具集合，因此 KP 無法補登已取得的鑰匙。本次整合不擴大該權限。

## 背景

`app/keeper.py` 的 `_build_static_prompt` 每一回合都會整包重送：9,463 字元／**7,108 tokens**（o200k_base），51 條頂層規則，還不含動態附加的 `combat_block`、`kp_assistant_block` 等區塊。一次真實正式環境的結構化 log 記錄到 `context_tokens_estimate: 29706`，對照 `context_ceiling: 32000`、`reserve_tokens: 6144`，算出來 `budget_tokens: 0`——static prompt 已經把那一回合的 RAG 劇本檢索預算完全擠光。促成這次調查的事故見 [bug-combat-reveal-beat-skipped-before-lethal-damage_zh.md](../bug/bug-combat-reveal-beat-skipped-before-lethal-damage_zh.md)。

兩條獨立的推理路線收斂到同一個修法方向：

1. 一個已驗證的 pilot：把 `combat_block`（正式戰鬥中的機制指令，純工具呼叫，不是玩家看的敘事）從中文翻成英文：1,275 → 986 tokens，降低 22.7%，翻譯前先跑過全部測試套件確認零依賴。
2. 一份獨立提出的整併建議，用不同路徑（先查文字重複，再把「敘事語氣」跟「工具呼叫／機制政策」分開）得出同一個架構結論——敘事語氣要留中文（直接影響生成的中文文字風格），機制政策給模型看，沒有留中文的理由。

## 現行契約

1. 會影響生成敘事品質的內容（Keeper 語氣、五感細節指示、節奏、聚焦單一角色、不用條列收尾）留繁體中文。這不是省 token 的目標——中文指示對「產出中文文字的風格」的控制力，實測上比「用英文要求輸出中文」更直接。

2. 純工具呼叫／機制狀態政策的內容（該呼叫哪個工具、參數語意、狀態權威規則）才是省 token 的目標。滿足以下兩個條件才翻英文：(a) 該段落對其原本中文字面沒有測試依賴，或依賴已一併遷移；(b) 重跑一次戰鬥／機制導向的模擬驗證（沿用既有 sim harness）確認沒有行為退化。系統實際會渲染或比對的字面 UI／按鈕字串（例如「閃避」「反擊」「撲向掩體」「（暫離）」）即使包在英文指令裡也維持原文，因為那些不是待翻譯的敘述，是系統本身產生或比對的精確字串。

3. 重複的修法是刪掉多餘的重述，不是判定規則不必要——不同規則的「條數」不會變少，變少的只是同一件事「被講幾次」。

4. 精簡或改寫一條規則之前，先問這條規則是不是「用文字禁令頂替一個本來該有的工具」。純文字的「不要做 X」只有在模型沒有更合理的替代做法時才會可靠地被壓下去；如果一個範圍界定清楚的工具本來就能正確引導同樣的意圖，優先修好／補上那個工具，而不是把文字收得更緊。具體證據：`app/keeper.py` 的裝備購買規則，截至 PR #91（2026-09-27，`bug/purchase-turn-provenance`）原本有一個能動的 `purchase_items` 工具，同一天被 revert；規則文字隨後收緊成明文禁止該工具原本用來建立結構的信用評級／現金推理（「不以信用評級、生活水準、價格或現金裁定是否可得」），但模型還是持續往那個方向推理。精簡任務 6 時已考慮這點：依使用者決定移除採買文字規則，沒有恢復購買工具或可負擔性規則。

## 已查證結果與整合項目

- **0，權限與修錯：** 系統操作主動且以工具為準；敘事仍受正典與防劇透限制。A 類理解／敘事錯誤可在後續回覆修正；B 類已確立狀態須由專用工具修正。`/coc correct` 保留作 OOC 爭議管道。
- **1，autoroll／pending：** 一份標準規則涵蓋開關與 Luck，特殊檢定附近保留短提醒。
- **2／3，戰鬥：** 已驗證的英文 `combat_block` 與精簡路由表涵蓋建立戰鬥、登記戰鬥員、防禦、彈藥、傷害與效果。同種多隻敵人須有不同名稱。依劇本觸發的甦醒／起身規則與已驗證修正相同。首次路由模擬漏呼叫 `add_npc_to_combat`；乾淨的 `main_v2` 對照組也漏呼叫，屬[既有缺陷](../bug/bug-active-enemy-registration-after-combat-start_zh.md)。路由現明確要求戰鬥開始後在同一工具序列登記在場敵人。
- **4，KP Assistant 權限層級：** 已由任務 0 取代，未另外加入一套層級規則。
- **5，正典／防劇透／隱私：** 原提案聲稱的獨立重複規則未獲證實。四條不同規則保留並翻成英文，防劇透與隱私開關相互獨立；任務 0 的權限及修錯語意也保留。
- **6，裝備：** 三條規則涵蓋審查時機、合理性與持有狀態。依使用者要求移除採買文字規則；保留遊戲內取得與持久物品工具。
- **7，骰子：** 精簡的 `purpose`／`roll_context` 契約附遊戲結算及 OOC 隨機各一例，置於現行 KP Assistant 機制區塊。

### PR review：戰鬥結束後的更正依據

原任務 3 的修正指示要 Keeper 在玩家指出漏講甦醒節拍時，查 `get_combat_status` 和 `get_character_sheet`；但 `end_combat` 會清空即時戰鬥狀態，角色卡也無法找回已倒下敵人的結算。整合後在 `GroupState` 保存一份有時間線及劇本歸屬的最終戰鬥紀錄。`get_combat_status` 可在戰後查到最近一次傷害與參戰者結局；隱私隔離開啟時，玩家視角不顯示敵人 HP。開始新戰鬥會清除紀錄。只有權威紀錄支持時，Keeper 才能只補敘事；缺少紀錄則保留原攻擊不重做，將未解爭議交給 `/coc correct`。不得重播傷害或重登記敵人來製造依據。

### 任務 0 草稿文字

以下保留歷史草稿。整合後 prompt 保留操作權限及 A／B 類修錯的核心語意；可切換的防劇透／隱私文字於任務 5 翻成英文。

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
_build_static_prompt -> 權限 + 檢定／戰鬥／裝備政策
                     -> 獨立的防劇透／隱私開關區塊
_build_dynamic_prompt(KP Assistant) -> 權限 + 精簡骰子機制
玩家／KP 更正 -> 已確立狀態由專用工具修改
              -> 未確立 A 類錯誤可只修正敘事
              -> 未解決 OOC 爭議走 /coc correct
```

## 實作與驗證

- 實作：[app/keeper.py](../../../app/keeper.py) 和 [app/keeper_prompt_policy.py](../../../app/keeper_prompt_policy.py)。
- Prompt 契約：`tests/test_static_prompt_*.py`、`tests/test_spoiler_policy.py`、`tests/test_narrative_boundary_prompts.py`。戰後回歸測試涵蓋致命傷害、`end_combat`、序列化、依角色投影歷史，以及時間線／新戰鬥的失效。
- 整合後完整 pytest、Ruff、mypy、compileall 均通過。隔離的五名調查員、16 輪 Codex 真實模擬有 16／16 次 router 呼叫完成，沒有 LLM 或工具錯誤；實際走到玩家檢定、待擲後續、建立戰鬥、登記敵人、傷害與先攻限制。因三次劇本搜尋先耗掉四次工具額度，`start_combat` 與 `add_npc_to_combat` 分散在連續兩回合。固定序列未走到沉睡敵人的致命甦醒節拍；僅能確認原句與 prompt 斷言保留，不能宣稱真實敘事已驗證該節拍。防劇透／隱私開關組合由整合 prompt 測試驗證，真實模擬沒有切換開關。

## 附錄：執行交接

以下交接紀錄早於任務 0／5／6 的最終決定；以上方整合項目為準。

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
