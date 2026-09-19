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

## 啟動

```bash
source .venv/bin/activate
python3 -m app.discord_bot
```

看到 `Discord bot 已上線` 即代表連線成功。Discord 直接附加圖片檔案，不需要 webhook、ngrok 或公開圖片網址。
