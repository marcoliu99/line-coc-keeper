# 擲過骰才停下的回合，不再說劇本沒有足夠內容

[English](unfinished_after_a_result_wording_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `c46e341`。來自 2026-10-12 Camp Sunny rerun7（第 76、80 輪）。

## 問題

第 76 輪：隊伍開車逃離；執行器搜尋了劇本（搜尋結果就是「Fleeing Camp Sunny」那一段），擲了開車檢定（失敗），查了 Billy 的數值表，然後以 `incomplete` 結束。玩家先看到骰子結果，接著讀到「這次行動尚未完整處理。… 劇本裡沒有足夠的內容可以據以裁決這個行動，守密人沒有替它編造」。第 80 輪在五個理智檢定之後也是一樣。劇本明明有這一段，骰子結果也算數，所以這句話是錯的，還叫玩家改問別的。

## 變更

`turn_fallback.guidance(reason, hints, acted=...)`：回合已經擲骰、結算檢定或改了遊戲狀態（`dice_rolled`、`resolved`、`state_changed`），而原因是文字會怪劇本的那幾種（`no_scenario_evidence`、`executor_no_action`、`unsupported_action`）時，改告訴玩家「剛才的結果已經記錄，但這次行動的後續守密人還沒處理完；請接著說明你想怎麼做。」場景提示照舊接在後面。什麼都沒做的回合維持原本的文字，戰鬥中仍用戰鬥的說明。

## 沒有改的

執行器為什麼在骰子擲完後把這種回合判成 `incomplete`，紀錄沒寫，仍然不知道。`retry_after_lookups_design_spec_zh.md` 已讓模型給的理由寫進文字記錄。

## 驗證

`tests/test_scene_hints.py`：三種「已經做過事」的徵象都得到新說法，而且都不含「劇本裡沒有足夠的內容」；什麼都沒做的回合維持舊文字。
