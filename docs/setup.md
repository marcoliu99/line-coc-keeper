# 安裝與設定教學

本專案現在只支援 Discord。

## 建立 Discord Bot

1. 前往 [Discord Developer Portal](https://discord.com/developers/applications) 建立 Application。
2. 在 **Bot** 頁面建立 Bot，取得 token，填入 `.env` 的 `DISCORD_BOT_TOKEN`。
3. 開啟 **Message Content Intent**。
4. 在 OAuth2 URL Generator 勾選 `bot`，授予至少 `View Channel`、`Send Messages`、`Read Message History`、`Attach Files`，把 Bot 邀請到伺服器。

## 設定專案

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

至少設定：

```dotenv
DISCORD_BOT_TOKEN=...
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
```

也可以選擇 `gemini` 或 `openai`，並設定對應的 API key。資料庫、模型、Scenario RAG 與 Keeper sampling 設定都在 `.env.example` 中有說明。

### 效能與開發 Log

專案提供兩個獨立的 log channel：

```env
# 結構化效能 log：request／AI／RAG／DB／Discord timing 與 token usage
LOG_ENABLED=false

# 其他 developer 的一般文字 debug／info／warning／error
LOG_TEXT_ENABLED=true
LOG_LEVEL=INFO
LOG_FORMAT=json
```

需要分析效能時開啟 `LOG_ENABLED=true`；只想暫時看文字 debug 時可以保持效能 log 關閉，改用 `LOG_TEXT_ENABLED=true` 與 `LOG_LEVEL=DEBUG`。正式環境若只想保留慢請求、fallback、retry 與錯誤，可使用 `LOG_LEVEL=WARNING`。完整欄位與流程見 [結構化效能與請求 Log 設計規格](structured_performance_logging_design_spec.md)。

## 啟動

```bash
source .venv/bin/activate
python3 -m app.discord_bot
```

看到 `Discord bot 已上線` 即代表連線成功。Discord 直接附加圖片檔案，不需要 webhook、ngrok 或公開圖片網址。

## 多 instance 與資料清理

若需要同時管理多個本地 Bot instance：

```bash
./scripts/start_bot.sh discord
./scripts/bot_status.sh
./scripts/stop_bot.sh <instance>
```

每個 instance 都有獨立 manifest 與 log。清理資料前請先確認腳本列出的 allowlist：

```bash
./scripts/clean_bot_data.sh --yes
```

此操作會清理 `DATA_DIR`、`DB_PATH`、備份、劇本庫與 `IMPORT_DIR`，不會刪除 `.env`、原始碼、虛擬環境或 lifecycle manifest，也不會自動停止正在執行的 Bot。
