# Spec: Discord 公開頻道回覆文字內容記錄

## Changeset Tracking
- **main_v2 start**: origin/main_v2:5f7f685
- **implementation end**: 本次實作 commit（見 `git log enhancement/discord-reply-text-logging -1`）；四項檢查（ruff/mypy/compileall/pytest）皆通過，388 tests passed

## Purpose & Scope

目前 `app/discord_bot.py` 的 `_make_reply()`（公開頻道回覆）只用 `observability.span("discord.reply", ...)` 記錄結構化 metrics（訊息數、chunk 數、bytes），**從未記錄實際發送的文字內容本身**。這代表「這次 Keeper 到底回覆了什麼故事內容」完全沒辦法從 log 回溯，只能看到「送了幾則訊息、多少 bytes」這種統計數字。

這個專案已經有一個處理相同問題的先例：`app/keeper.py:2568` 的 `search_scenario` 查詢字串記錄——用一般的 `_logger.info()`（受 `LOG_TEXT_ENABLED` 控制，跟 `LOG_ENABLED` 控制的結構化 metrics 是獨立的兩個開關）補上結構化事件答不出來的問題。這次照同樣模式，補上公開頻道回覆的文字內容記錄。

**範圍明確限定：** 只記錄公開頻道回覆，不動私訊（`_send_dm`）跟圖片傳遞（`send_image`/`_send_dm_image`）——這兩者這次不在範圍內。

## Changes

### v1（初版）

在 `app/discord_bot.py` 的 `_make_reply()` 入口處呼叫 `_logger.info("discord_reply text=%r", text)`，記錄切塊前的完整原始文字一次，不是每個 Discord 長度限制的 chunk 各記一次——chunk 切分是 Discord API 的技術限制，不是「這輪 Keeper 說了什麼」語意上有意義的分段點。

### v2（code review 後修正，兩項發現）

**發現 1（真實缺口）：** `app/discord_bot.py` 還有一個跟 `_make_reply()` 結構幾乎一模一樣的雙胞胎函式 `_make_interaction_reply()`（用於按鈕/互動回覆），v1 只改了 `_make_reply()`，漏了這一份。玩家點擊技能檢定、幸運骰按鈕時，敘事結果是透過 `_make_interaction_reply()`（`CheckButton.callback`、`LuckDecisionButton.callback`）用 `interaction.followup.send` 發到公開頻道的——這是 COC 遊戲裡非常常見的操作路徑，v1 完全沒記錄到。

**發現 2（安全性問題，非單純效能）：** v1 直接呼叫 `_logger.info(...)`，把「要不要記錄」完全交給 `app/logging_config.py::configure_logging()` 下游安裝的 `_ChannelFilter` 去判斷。但 `configure_logging()` 在 `LOG_ENABLED` 跟 `LOG_TEXT_ENABLED` **都**是 false 時會直接 return，完全不安裝這個 filter。如果 host 應用在呼叫 `configure_logging()` 之前，自己已經配置好一個 root handler（例如某些嵌入式執行環境的預設 INFO handler），這個 log 呼叫依然會傳播到那個 host 既有的 handler，即使 `LOG_TEXT_ENABLED=false`，完整故事文字還是會被那個 handler 記錄下來——**這違背了「關閉 `LOG_TEXT_ENABLED` 就不會記錄敏感故事內容」的明文承諾**，不只是浪費 CPU/記憶體而已。

**修正方式：** 抽出共用 helper `_log_reply_text(text)`，內部**直接檢查 `config.LOG_TEXT_ENABLED`**，不呼叫 `_logger.info` 就完全不建構、不排入 log queue——不管 host 的 logging 環境如何預先配置，這個判斷都不會被繞過。`_make_reply()` 跟 `_make_interaction_reply()` 都改呼叫這個共用函式，避免重蹈「兩份複製貼上程式碼只改一份」的覆轍。

## Data Sensitivity（已跟使用者確認）

記錄完整原文（不截斷、不 hash），因為目的是本機/開發環境的完整 debug 回溯。正式環境若不想把玩家看到的故事內容寫進 log，關閉 `LOG_TEXT_ENABLED` 即可——v2 修正後這個開關是在呼叫點直接把關，保證關閉時絕對不會外洩，不再依賴下游 handler 配置。

## Testing

`tests/test_discord_reply_text_log.py`：
- `_make_reply()`：`LOG_TEXT_ENABLED=true` 時記錄完整文字；`LOG_TEXT_ENABLED=false` 時完全不呼叫 logger；獨立於 `LOG_ENABLED`；長文字切塊時只記一次原文
- `_make_interaction_reply()`：同樣驗證 `LOG_TEXT_ENABLED` 開/關兩種狀態（發現 1 的 regression test）
