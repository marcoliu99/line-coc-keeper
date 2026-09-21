# Discord Bot API 與內部介面

本專案只提供 Discord gateway adapter，不提供一般公開 REST API。

## Discord entry point

```bash
python3 -m app.discord_bot
```

`app/discord_bot.py` 負責 Discord event、附件、DM、圖片與 persistent buttons；command/game logic 位於 `app/commands/`、`app/legacy_commands.py` 與 `app/agents/`。

## Command routing

`app/commands/router.py` 接收 Discord adapter 正規化後的文字與 callback：

```python
async def handle_text_message(
    conversation_id: str,
    user_id: str,
    get_display_name,
    reply,
    send_dm,
    send_image,
    send_dm_image,
    text: str,
    format_mention=lambda owner_id: owner_id,
    is_keeper=False,
    allow_opaque_sudo_target=False,
) -> None
```

`/coc help` 由 Discord adapter 交給 `app/help_service.py`，其他 `/coc` command 依序交給 character、combat、map 與 system handlers。`/coc sudo` 由 router 以 actor／subject 分離後，重用既有 player handlers；Discord adapter 預設只接受 mention target，非 Discord adapter 測試時可明確傳入 `allow_opaque_sudo_target=True`。

`main_v2` 新增的 checkpoint、digest、scenario import/merge 與角色切換指令仍由同一個 Discord command router 處理；它們的權限由 system／character handler 執行時驗證，不是 HTTP API。

## Player help registry

玩家 help metadata 位於 `app/help_registry.py`，由 `app/help_registration.py` 以明確且可重複呼叫的中央入口初始化。新增玩家可輸入的 command 時新增 `HelpEntry`，其 entry path 必須正好是 `(category, command)` 兩段，再執行：

```bash
python3 -m app.help_docs --output docs/player_command_reference.md
```

這會更新 [玩家指令參考](player_command_reference.md)，並同步 Discord 的分類按鈕與三層導覽。

## Configuration

| 變數 | 用途 |
| --- | --- |
| `DISCORD_BOT_TOKEN` | Discord gateway token |
| `LLM_PROVIDER` | `anthropic`、`gemini` 或 `openai` |
| `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` / `OPENAI_API_KEY` | 對應 LLM provider 金鑰 |
| `DATA_DIR` | 群組圖片與資料目錄 |
| `DB_PATH` | SQLite database 路徑 |
| `SCENARIO_RAG_ENABLED` | 是否啟用 Scenario RAG |
| `SCENARIO_RAG_TOP_K` | Scenario RAG 每次最多回傳的結果數 |
| `SCENARIO_RAG_EMBEDDING_MODEL` / `SCENARIO_RAG_EMBEDDING_WEIGHT` | embedding 模型與混合 BM25 的權重 |
| `SCENARIO_RAG_PREWARM_ENABLED` / `SCENARIO_RAG_PREWARM_MAX_CONCURRENT` | 劇本啟用後是否背景預熱索引，以及預熱併發上限 |
| `MAX_TOOL_ITERATIONS` | 單次 provider conversation 的 tool loop 上限 |
| `LLM_REQUEST_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES` / `LLM_TIMEOUT_RETRIES` | LLM 單次 request timeout 與 retry budget |
| `EMBEDDING_REQUEST_TIMEOUT_SECONDS` | RAG/embedding source 的等待上限；超時回退 BM25 |
| `DISCORD_REQUEST_TIMEOUT_SECONDS` | 單次 Discord send/edit/followup 的等待上限，不重送訊息 |
| `TOOL_EXECUTION_TIMEOUT_SECONDS` / `PROVIDER_SHUTDOWN_GRACE_SECONDS` | tool/ provider shutdown 的取消與收尾邊界 |
| `BACKUP_DIR` / `BACKUP_INTERVAL_MINUTES` / `BACKUP_KEEP_COUNT` | SQLite 備份位置、週期與保留數量 |
| `SCENE_DIGEST_TURN_INTERVAL` | 場景摘要更新間隔 |
| `SCENARIO_LIBRARY_DIR` / `IMPORT_DIR` | 劇本庫與 KP 本機 PDF 匯入目錄 |

完整設定範例請看 `.env.example`。
