# 戰鬥已正常開始，就不再要求重來

[English](fight_started_state_conflict_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `112a22a`。來自 2026-10-12 的 Haunting rerun3（第 28 輪）。

## 問題

隊伍拆開牆板，Corbitt 起身。執行器呼叫了 `get_enemy_stat_block` 和 `initialize_combat`，戰鬥已經在跑。它交回裁決時卻指名一個不存在的檢定或 Luck 決定（文字記錄現在寫得出來：「要求處理的檢定／Luck 決定不存在或已過期」，驗證碼 `pending_identity_mismatch`），於是這回合被歸為 `state_conflict`。玩家先讀到「戰鬥已然開始，Felix，你先行動」，緊接著又讀到「這次行動尚未完整處理…目前的狀態和這次行動不一致；請依目前劇情重新操作」，等於在戰鬥進行中叫大家重來。

## 變更

`prompt_config.fight_started_cleanly(result)` 在下列情況為真：這回合開了戰鬥（`combat_opened`）、原因是 `state_conflict`，而且回合沒留下任何待處理項目、也沒擲骰（沒有待處理檢定或 Luck 決定、沒擲骰、沒結算檢定）。一致性步驟就直接回傳敘事者的文字，不再附上「尚未完整處理」的提示。其他未完成的回合，以及擲過骰或還有檢定在等的回合，仍然有這段提示。

這個回合仍記錄為 `state_conflict`，測試報告的統計照舊。

## 驗證

`tests/test_turn_fallback.py`：這種回合只回敘事者的文字；有待處理檢定、擲過骰、結算過檢定，或原因不同，則仍保留提示。
