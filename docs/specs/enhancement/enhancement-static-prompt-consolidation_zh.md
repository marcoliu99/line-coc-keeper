# Static prompt 整併：先去重複，再切「敘事／機制政策」雙語層

[English](enhancement-static-prompt-consolidation.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**backlog**。對照基準：`main_v2` 的 `07d55a7`（2026-09-29）。

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

## 已查證結果（2026-09-29 調查）

- **autoroll／pending 重複：證實。**「autoroll」這個字在 prompt 裡出現 11 次，分散在至少 7 條不同規則裡重講「autoroll 關閉才 pending，開啟才系統立即代擲」。至少有一句完全多餘、沒加任何工具專屬細節：「`skill_check`／`sanity_check` 在 autoroll 關閉（預設）時建立玩家擲骰 pending；只有 `autoroll on` 才會立即完成。」但其他幾處重述*不是*單純重複——它們把同一條規則掛在特定工具自己的規則旁邊（sanity_check、重傷 CON 檢定、孤注一擲）是為了就近提醒，這有實際價值，不該直接整條刪掉。這裡的整併是挑一個標準版本、把其餘壓成簡短交叉引用，不是無差別刪除。

- **spoiler／privacy「重複」：目前的查證結果不支持原提案的說法。** 被指為重複的那幾條規則（劇本機密、私密資訊／秘密目標、NPC 隊友保密、禁止後設說明）本身就是 `_spoiler_protection_prompt_rules()` 跟 `_privacy_isolation_prompt_rules()`（`app/keeper.py` 約 3191-3260 行）回傳的內容，在單一位置插入 prompt（約 3371、3405-3406、3418 行）。這次調查沒有在 static prompt 別處找到第二份獨立重述。動這塊之前，要先用完整規則原文（不是截斷的預覽清單）重新查一次，找出原提案講的具體重複點在哪裡，或確認真的沒有就放棄這一項。

## 提案分段（一塊一塊做，風險低到高排序）

每一項各自開分支／commit：先照 `combat_block` pilot 的做法，對全部測試套件 grep 確認該段落沒有中文字面依賴，再整併／翻譯，補上或更新鎖住新文字必要內容的 prompt 斷言測試，跑完整套檢查；凡是動到檢定／戰鬥機制的項目（1、3、4），合併前要沿用既有 sim harness（`sim_playtest.py`，可從 `/Users/marcoliu/.claude/jobs/c0193acc/sim/` 重用）跑一次聚焦檢定或戰鬥的短模擬驗證。

1. **autoroll／pending 檢定流程整合。** 把約 7 處重述收斂成一個標準版本；有實際就近提醒價值的工具專屬交叉引用留著、壓短。風險最低：純文字整併，邏輯不變，重複已查證。
2. **`combat_block` 翻英文。** 已完成 pilot（分支 `fix/combat-reveal-beat-before-lethal-damage`，token 降 22.7%，零測試依賴）；還差一次真實戰鬥模擬驗證行為沒有退化，驗完才算這項完成。
3. **戰鬥工具路由表。** 把散落的 start_combat／add_npc_to_combat／傷害工具／adjust_ammo 指示改成精簡的路由列表；同種怪物多隻要不同名稱這條保留成單一規則，不要埋在長段落敘述裡。
4. **KP Assistant 權限層級。** 把 KP Assistant 區塊改成明確的優先順序清單（引擎權威狀態 > KP 明確更正 > 劇本正典 > Keeper 敘事判斷），配簡短範例取代長篇說明。
5. **Canon boundary／spoiler／privacy 整併。** 卡在上面那項重新查證——先找到真正的重複點（如果有）才能動手；這塊把關爆雷／秘密目標正確性，風險比 1-4 高。
6. **裝備真實性審查（46-51 → 約 3 條）。** 何時要審查、三項合理性檢查、已登記／未登記物品的處理；把購買／取得規則併進第三點，不要獨立一條。
7. **`roll_dice` 段落精簡。** 收斂成 `purpose`／`roll_context`（`game_resolution` vs `ooc_randomizer`）的核心契約，各配一個範例；其餘交給工具描述本身。

## 流程與介面

```text
每個段落：對測試套件 grep 中文字面依賴 -> 整併／翻譯 -> prompt 斷言測試 -> 完整檢查 -> （動到機制的段落）模擬驗證 -> commit
```

## 實作與驗證

- [app/keeper.py](../../../app/keeper.py)
- 每個段落各自的 prompt 斷言測試（新增）
- 動到機制的段落使用 sim harness（本 repo 外部、可重用的腳本）

相關：[bug-combat-reveal-beat-skipped-before-lethal-damage_zh.md](../bug/bug-combat-reveal-beat-skipped-before-lethal-damage_zh.md)（促成這次調查的事故與 RAG 預算被擠光的證據）；[bug-combat-trigger-prompt-and-damage-tool-ambiguity_zh.md](../bug/bug-combat-trigger-prompt-and-damage-tool-ambiguity_zh.md)（一條必須留中文的規則範例——它存在的意義就是給中文語言判斷用的中文對照例句，屬於第 2 條契約的例外情況）。
