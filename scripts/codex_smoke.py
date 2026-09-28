"""Opt-in OAuth smoke test. No Discord, API keys, production data or .env loading.

Run: .venv/bin/python scripts/codex_smoke.py --transport exec
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--transport', choices=['exec', 'app-server'], default='exec')
    parser.add_argument('--model', default='gpt-6-luna')
    parser.add_argument('--scenario', choices=['text', 'check', 'pipeline'], default='text')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='coc-oauth-smoke-') as tmp:
        os.environ.update(PYTHON_DOTENV_DISABLED='1', LLM_PROVIDER='codex', ANALYSIS_PROVIDER='openai',
            CODEX_TRANSPORT=args.transport, CODEX_REASONING_EFFORT='medium', CODEX_MODEL=args.model, DATA_DIR=tmp+'/groups',
            DB_PATH=tmp+'/state.db', BACKUP_DIR=tmp+'/backups', SCENARIO_LIBRARY_DIR=tmp+'/scenarios',
            IMPORT_DIR=tmp+'/imports', OPENAI_API_KEY='', ANTHROPIC_API_KEY='', GEMINI_API_KEY='',
            DISCORD_BOT_TOKEN='')
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app.providers import codex_provider

        async def no_tools(name, arguments):
            raise AssertionError('Text smoke test must not execute tools')

        async def run():
            start = time.monotonic()
            if args.scenario == 'text':
                result = await codex_provider.run_conversation(
                    'Reply with exactly OAUTH_OK as final.content.', '', [], [], 'Reply now.', no_tools, 1)
                assert result == 'OAUTH_OK', 'Unexpected text response'
                report = {'ok': True, 'scenario': 'text', 'output': result}
            elif args.scenario == 'pipeline':
                from codex_pipeline_fixture import run_pipeline
                report = await run_pipeline()
            else:
                from codex_smoke_check import run_check
                report = await run_check(codex_provider)
            report.update(transport=args.transport, model=args.model, elapsed_seconds=round(time.monotonic()-start, 3))
            print(json.dumps(report, ensure_ascii=False))
        asyncio.run(run())


if __name__ == '__main__':
    main()
