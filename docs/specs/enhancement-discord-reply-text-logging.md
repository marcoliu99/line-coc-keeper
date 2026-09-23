# Spec: Discord 公開頻道回覆文字內容記錄

## Changeset Tracking
- **main_v2 start**: origin/main_v2:5f7f685
- **implementation end**: 本次實作 commit（見 `git log enhancement/discord-reply-text-logging -1`）；四項檢查（ruff/mypy/compileall/pytest）皆通過，385 tests passed（含新增 3 個測試）

## Purpose & Scope

目前 `app/discord_bot.py` 的 `_make_reply()`（公開頻道回覆）只用 `observability.span("discord.reply", ...)` 記錄結構化 metrics（訊息數、chunk 數、bytes），**從未記錄實際發送的文字內容本身**。這代表「這次 Keeper 到底回覆了什麼故事內容」完全沒辦法從 log 回溯，只能看到「送了幾則訊息、多少 bytes」這種統計數字。

這個專案已經有一個處理相同問題的先例：`app/keeper.py:2568` 的 `search_scenario` 查詢字串記錄——用一般的 `_logger.info()`（受 `LOG_TEXT_ENABLED` 控制，跟 `LOG_ENABLED` 控制的結構化 metrics 是獨立的兩個開關）補上結構化事件答不出來的問題。這次照同樣模式，補上公開頻道回覆的文字內容記錄。

**範圍明確限定：** 只記錄公開頻道回覆（`_make_reply`/`reply()`），不動私訊（`_send_dm`）跟圖片傳遞（`send_image`/`_send_dm_image`）——這兩者這次不在範圍內。

## Changes

`app/discord_bot.py` 的 `_make_reply()`：在函式入口處（切塊、實際送出之前）呼叫 `_logger.info("discord_reply text=%r", text)`，記錄切塊前的完整原始文字一次，不是每個 Discord 長度限制的 chunk 各記一次——chunk 切分是 Discord API 的技術限制，不是「這輪 Keeper 說了什麼」語意上有意義的分段點。

這條 log：
- 獨立於 `LOG_ENABLED`（那個開關只控制結構化 metrics span，兩個分支路徑——`LOG_ENABLED` 開/關——都會執行這行）
- 受 `LOG_TEXT_ENABLED` 間接控制（`app/logging_config.py::configure_logging()` 只有在 `LOG_ENABLED` 跟 `LOG_TEXT_ENABLED` 都是 false 時才完全不設定任何 log handler；只要 `LOG_TEXT_ENABLED=true`，這條 `_logger.info` 就會正常輸出）

## Data Sensitivity（已跟使用者確認）

記錄完整原文（不截斷、不 hash），因為目的是本機/開發環境的完整 debug 回溯。正式環境若不想把玩家看到的故事內容寫進 log，關閉 `LOG_TEXT_ENABLED` 即可完全跳過這條 log（連同其他所有一般文字 log）。

## Testing

`tests/test_discord_reply_text_log.py`：
- `LOG_ENABLED=true` 時記錄完整文字
- `LOG_ENABLED=false` 時**仍然**記錄（證明這條 log 不受 `LOG_ENABLED` 控制）
- 長文字被切成多個 Discord chunk 時，只記錄一次切塊前的原文，不是每個 chunk 各記一次
