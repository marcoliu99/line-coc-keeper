# 後備開場在回合取用 timeline 的地方，再檢查一次 timeline

[English](fallback_opening_timeline_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `c6512c4`。來自 2026-10-14 對 `main_v2` 的稽核（Phase 2A 開場）。

## 問題

腳本開場是以抽取 token 的 timeline 提交（`expected_timeline`）。後備開場只把 token 的來源雜湊、context 與參與者交給 Keeper 管線，沒有交 timeline：回合從 `refresh_tool_state` 開始，用當時狀態的 timeline。管線提交時只拿回合自己的 timeline 與自己比，`opening_guard` 也沒有 timeline 條件。同一程序內有對話鎖擋住；但如果外部寫入者在最後一次 token 檢查與回合開始之間，替同一份劇本開了新遊戲，舊開場就會被提交到新 timeline。

## 變更

`_run_fallback` 在開始回合之前，把更新後狀態的 timeline 與 token 的比對，不同就回 `rejected / timeline_changed`（玩家本來就會收到的 timeline 變更原因）。沒有 token 的後備開場（沒有來源雜湊的舊狀態）不變。

## 驗證

`tests/test_game_opening_unlocked_extraction.py`：token 屬於舊 timeline 時不產生開場，並以 `timeline_changed` 拒絕；token 吻合時回合照常執行。前半在沒有這項變更時會失敗。
