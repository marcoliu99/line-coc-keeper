# 沉睡敵人的甦醒節拍在致命結算前被跳過

[English](bug-combat-reveal-beat-skipped-before-lethal-damage.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。發現於真實正式環境對局（`main_v2`，對話 `ed20ad1edfb8`，2026-09-28）。

此版本描述現行契約，提案工作均明確標示。

## 事件經過

一名調查員對劇本描述為「原本靜止不動、要等受到威脅才會行動」的敵人舉槍。Keeper 正確地把「舉槍瞄準」判定為威脅，在開槍之前就先呼叫了 `start_combat`、`add_npc_to_combat`——這部分完全符合既有的開戰契約。

這一槍是極難成功。接下來大約相隔一分鐘出現兩段敘事：

1. 擲骰完成的瞬間：「槍聲大作，屍體仍伏在原處，煙霧尚未散盡」。
2. 傷害工具結算完（`roll_impaling_damage`、`damage_combatant`、`end_combat`——真實、正確計算出來的擊殺）之後：「他倒伏下去，再沒有動靜」。

這兩句話讀起來幾乎一樣，都是「一具不會動的屍體」。劇本明文要求的甦醒／起身畫面——也正是劇本用來觸發全員理智檢定的那個時間點——從頭到尾沒有被敘事出來。從玩家的角度看，什麼都沒發生。兩名玩家各自提出質疑（「屍體應該要動啦」、「屍體沒動，為什麼要SAN」），Keeper 接著把整個遭遇重跑一遍，而不是單純補上漏掉的敘事：第二次 `add_npc_to_combat`（生出一個跟已經倒下那隻不同的新敵人個體）、第二次貫穿傷害擲骰（23 點，跟原本的 22 點不同），以及後續好幾則自我更正的訊息。

## 現行契約

1. 當劇本明文描述某個沉睡中的敵人有甦醒／起身／開始行動的觸發條件時，第一次對它造成傷害或判定擊倒的那次敘述，必須把那個甦醒/起身的畫面一併寫出來——不能讓敘事從「仍然靜止不動」直接跳到「已經倒下」，導致玩家完全感知不到任何變化。

2. 當玩家指出某個敵人或事件「應該有反應」時，先用 `get_combat_status`／`get_character_sheet` 確認機制上已經發生了什麼，再補敘漏掉的畫面；不要重新呼叫 `start_combat`／`add_npc_to_combat`／傷害工具，把同一次攻擊在一個新的敵人個體上重新結算一次。

這條規則在 static prompt 裡是用英文寫的，跟旁邊約 30 條中文規則不同——因為它是只給模型看的工具呼叫指示，不是玩家會看到的敘事內容，用這個 session 的 provider tokenizer 量測，精簡後的英文版比同樣精簡的中文版少約 32% token，而 static prompt 每回合都會整包重送，這是真的省。因為是全新、之前沒調過的一條，不用權衡既有調校的一致性成本。

## 流程與介面

```text
沉睡敵人受到威脅 -> 開戰 -> 致命結算的同一段敘述裡同時交代甦醒/起身跟傷害
玩家指出漏掉的畫面 -> 先確認現有機制狀態 -> 只補敘事，不重複結算
```

## 實作與驗證

- [app/keeper.py](../../../app/keeper.py)
- [tests/test_combat_reveal_beat_before_lethal_damage.py](../../../tests/test_combat_reveal_beat_before_lethal_damage.py)

相關：[bug-combat-trigger-prompt-and-damage-tool-ambiguity_zh.md](bug-combat-trigger-prompt-and-damage-tool-ambiguity_zh.md) 處理的是相反、但相鄰的失敗模式（敘事懷疑錯誤地啟動了戰鬥）；本篇處理的是戰鬥已經正確開始，但致命結算跳過了劇本要求的敘事節拍。
