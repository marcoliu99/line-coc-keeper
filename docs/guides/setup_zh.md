# 安裝與維運

[English](setup.md)

## 安裝

本 Bot 只支援 Discord。在 Discord Developer Portal 建立 application／bot、開啟 Message Content Intent，邀請時給頻道可見、傳訊／歷史與附件權限，把 token 存在 DISCORD_BOT_TOKEN。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
cp .env.example .env
```

開發依賴提供測試與 profiling 工具；.env 與 runtime 資料不要進 Git。

### Python 3.13 的可選 Paddle 模型搬移

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -r requirements-pdf-ocr.txt
python scripts/migrate_paddle_models.py --dry-run \
  --ocr-dest /Users/marcoliu/data/paddle/paddleocr \
  --layout-dest /Users/marcoliu/data/paddle/paddle-layout
python scripts/migrate_paddle_models.py \
  --ocr-dest /Users/marcoliu/data/paddle/paddleocr \
  --layout-dest /Users/marcoliu/data/paddle/paddle-layout
```

預設從 `~/.cache/line-coc-keeper/paddleocr` 與 `~/.cache/line-coc-keeper/paddle-layout` 複製完整模型。若模型在別處，使用 `--ocr-source`、`--layout-source` 指定。script 不下載模型，且只有明確加 `--remove-source` 才刪除舊來源。若來源缺失，應另外明確執行 `python scripts/setup_paddle_ocr.py --model-dir /Users/marcoliu/data/paddle/paddleocr` 或 `python scripts/setup_paddle_layout.py --model-dir /Users/marcoliu/data/paddle/paddle-layout`，不要用 migration 下載。準備完成後，在 `.env` 設定：

```dotenv
PDF_PADDLE_OCR_ENABLED=true
PDF_PADDLE_MODEL_DIR=/Users/marcoliu/data/paddle/paddleocr
PDF_PADDLE_LAYOUT_ENABLED=true
PDF_PADDLE_LAYOUT_MODEL_DIR=/Users/marcoliu/data/paddle/paddle-layout
```

## 設定

LLM_PROVIDER 設為 anthropic、gemini 或 openai 並提供對應 API key；精確模型／預設參考 .env.example 與 app/config.py。目前預設 KEEPER_REASONING_EFFORT=medium、MAX_TOOL_ITERATIONS=5、HIGH_ITERATION_WATERMARK=4，劇本預熱關閉；OPENAI_HISTORY_TOKEN_BUDGET=4000、OPENAI_HISTORY_MIN_TURNS=2 限制選用歷史，自適應准入預設開啟、階段輸出上限預設 0。環境設定變更後重新啟動。

## Log 與保護

LOG_ENABLED=false 控制結構化 timing／usage，LOG_TEXT_ENABLED=true 控制一般文字 log，LOG_LEVEL／LOG_FORMAT 控制輸出。SPOILER_PROTECTION_ENABLED、PRIVACY_ISOLATION_ENABLED、GUARD_ENABLED 獨立且預設 true；關 Guard 只停 LLM 修復，不停確定性驗證，現行行為會警告並放行無效文字。SCENARIO_LIFECYCLE_KP_ONLY 預設 false，可啟用以限制適用生命週期操作。

## 執行與 profiling

可直接執行或用生命週期腳本：

```bash
python3 -m app.discord_bot
./scripts/start_bot.sh discord
./scripts/bot_status.sh
./scripts/stop_bot.sh <instance>
```

Discord 直接傳附件，不需公開 webhook 或圖片主機。Profiling 可選 BOT_PROFILER=pyinstrument 或 py-spy，也支援 off；runtime manifest／log／profile 在 .runtime/bots。缺 profiling 工具或 attach 失敗時，不會默默變無 profiling 啟動；停止時封存設定的 log／profile。

## 測試與清理

有正式資料時，先使用隔離資料目錄再執行 python3 -m pytest；可加 --cov=app --cov-report=term-missing。scripts/clean_bot_data.sh --yes 是獨立破壞性操作，先看 allowlist；會刪設定的遊戲／資料庫／備份／劇本／匯入資料，不刪原始碼、.env、虛擬環境或 lifecycle manifest，也不自動停止執行中 Bot。
