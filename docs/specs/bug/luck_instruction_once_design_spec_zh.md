# Luck 指示在回覆已經說過時，又被加了一次

[English](luck_instruction_once_design_spec.md)

狀態：**已實作**。基底：`main_v2` 的 `e4a9a1f`。

## 問題

擲骰在等 Luck 決定時，回覆最後會有一行系統訊息：「骰值 N 還在等 Luck 決定：請按 Luck 按鈕，或輸入 /coc luck skip 保留原結果。」`rerun8_player_replies_design_spec` 讓敘事已經給了指令時，這一行不再重複，判斷方式是在敘事裡找指令，並且找到骰值的整數。

敘事幾乎每次都給了指令，卻幾乎從不寫骰值。2026-10-11 的四場 100 回合（Scritch Scratch、Haunting、Camp Sunny、Lightless）裡，這一行被加到 400 則回覆中的 82 則，而且這 82 則本來就有 `/coc luck skip`（Lightless 100 則裡有 46 則，Haunting 16、Camp Sunny 12、Scritch Scratch 8）。玩家讀完選項和花費，接著又讀一遍：

> …請使用 `/coc luck regular` 花費 28 點 Luck，或輸入 `/coc luck skip` 保留原骰結果。林間的後續仍未揭開。
> 骰值 93 還在等 Luck 決定：請按 Luck 按鈕，或輸入 /coc luck skip 保留原結果。

## 變更

- **回覆已經給了保留骰值的方法，就算說過。** 敘事含有 `/coc luck skip`，或含有 Luck 按鈕（「Luck 按鈕」「幸運按鈕」），`InteractionRef.told_in` 就為真。不需要骰值的數字：玩家需要的是怎麼決定，而敘事很少重複數字。敘事明確宣稱了和實際不同的骰值（骰到 6 卻寫「擲出 60」）時，那一行仍然保留，因為它是唯一帶引擎數字的文字；花費（「花費 6 點」）不算宣稱的骰值。
- **沒給保留方法的回覆，仍然加上這一行。** 敘事只列花費（`/coc luck regular（26 點）`），或只是提到 Luck，玩家還缺最基本的選項，所以照加。

## 不變更

- 待處理檢定的那一行（「已有待處理檢定／選擇，請使用檢定按鈕或 /coc check。」），以及敘事完全沒提時補上 Luck 指示的一致性修補（`prompt_config`）都不變；後者本來就把 `/coc luck` 和按鈕視為已說明。

## 驗證

`tests/test_rerun8_player_facing.py`：
- 敘事有 `/coc luck skip` 和花費、沒有骰值，不加那一行；提到按鈕的也不加。
- 敘事只列花費，或只是提到 Luck，仍然加那一行。
- 敘事同時給了指令和骰值不加，兩者都沒有就加，與以前相同。
