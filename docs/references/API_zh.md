# Discord 與內部介面

[English](API.md)

## 入口

應用提供 Discord gateway adapter，沒有公開 REST API。以 `python3 -m app.discord_bot` 啟動；`app/discord_bot.py` 處理事件、附件、私訊、圖片與持久按鈕，`app/commands/router.py` 把正規化命令及一般訊息分派到 handler 或 agent。

## Agent 介面

`supervisor.run_turn` 處理 `player_action`、`resolved_check_followup`、`opening_fallback`；KP Assistant 獨立於 `app/agents/assistant.py`。權威工具共用 `keeper._execute_tool`，`keeper.run_turn` 已不存在。router 也處理購買／更正命令及區分 actor／subject 的 KP sudo；runtime callback 驗證角色、所有權與時間線。

## Help 與生成參考

`app/help_registration.py` 初始化 Help registry，`app/help_actions.py` 為每項建立可執行動作；表單收自由文字、清單選已知資源，設定需確認的重要動作先確認。重新生成權威中文命令參考：

```bash
python3 -m app.help_docs --output docs/references/player_command_reference_zh.md
```

英文參考保留命令 token 並解釋用途；registry 變更須同步兩版。

## 設定依據

實際預設以 `app/config.py` 為準，部署設定參考 `.env.example`。主要類別含 provider 金鑰／模型、SQLite 與劇本／匯入目錄、RAG／embedding／可選預熱、重試／timeout／期限／准入、歷史／輸出預算、log、劇透／隱私／Guard、備份及摘要頻率；不要以歷史規格預設覆蓋現行設定。
