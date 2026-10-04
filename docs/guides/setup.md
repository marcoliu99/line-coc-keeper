# Installation and operation

[繁體中文](setup_zh.md)

## Install

This bot supports Discord only. In Discord Developer Portal, create an application and bot, enable Message Content Intent, and invite it with channel visibility, message sending/history and attachment permissions. Store its token in DISCORD_BOT_TOKEN.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
cp .env.example .env
```

The development requirements provide tests and profiling tools. Keep .env and runtime data out of Git.

### Optional Paddle model migration on Python 3.13

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

The script copies complete models from `~/.cache/line-coc-keeper/paddleocr` and `~/.cache/line-coc-keeper/paddle-layout` by default. Use `--ocr-source` or `--layout-source` if prepared models are elsewhere. It never downloads models and retains both sources unless `--remove-source` is specified. If a source is missing, prepare it explicitly with `python scripts/setup_paddle_ocr.py --model-dir /Users/marcoliu/data/paddle/paddleocr` or `python scripts/setup_paddle_layout.py --model-dir /Users/marcoliu/data/paddle/paddle-layout` instead of running migration. After preparation, set in `.env`:

```dotenv
PDF_PADDLE_OCR_ENABLED=true
PDF_PADDLE_MODEL_DIR=/Users/marcoliu/data/paddle/paddleocr
PDF_PADDLE_LAYOUT_ENABLED=true
PDF_PADDLE_LAYOUT_MODEL_DIR=/Users/marcoliu/data/paddle/paddle-layout
```

## Configure

Set LLM_PROVIDER to anthropic, gemini or openai and provide its corresponding API key. Consult .env.example and app/config.py for exact models and defaults. Current defaults include KEEPER_REASONING_EFFORT=medium, MAX_TOOL_ITERATIONS=5, HIGH_ITERATION_WATERMARK=4 and optional scenario prewarm off. OPENAI_HISTORY_TOKEN_BUDGET=4000 and OPENAI_HISTORY_MIN_TURNS=2 constrain selected history; adaptive admission defaults on, and stage output caps default 0. Restart the bot after changing environment configuration.

## Logging and protection

LOG_ENABLED=false controls structured timing/usage; LOG_TEXT_ENABLED=true controls ordinary text logs. LOG_LEVEL and LOG_FORMAT configure output. SPOILER_PROTECTION_ENABLED, PRIVACY_ISOLATION_ENABLED and GUARD_ENABLED are independent and default true. Turning Guard off disables LLM repair, not deterministic validation, and currently allows invalid text through with a warning. SCENARIO_LIFECYCLE_KP_ONLY defaults false and can be enabled to restrict applicable lifecycle operations.

## Run and profile

Run directly or use lifecycle scripts:

```bash
python3 -m app.discord_bot
./scripts/start_bot.sh discord
./scripts/bot_status.sh
./scripts/stop_bot.sh <instance>
```

Discord sends attachments directly; no public webhook or image host is required. Profiling is opt-in: BOT_PROFILER=pyinstrument or py-spy; off is also supported. Runtime manifests/logs/profiles are under .runtime/bots. Missing profiling tools or failed attach do not silently degrade to an unprofiled bot. Stop archives configured logs/profile output.

## Tests and cleanup

Run python3 -m pytest in isolated data directories when production storage is present. Optional coverage uses --cov=app --cov-report=term-missing. scripts/clean_bot_data.sh --yes is a separate destructive operation: review its allowlist first. It removes configured game/database/backup/scenario/import data, not source, .env, environments or lifecycle manifests, and does not automatically stop a running bot.
