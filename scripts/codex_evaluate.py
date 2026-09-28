"""Run 25 complete synthetic cases per transport against real ChatGPT OAuth.

No production .env or data, no Discord messages. Five cases x five repetitions.
Run each transport in a separate process so provider config and dice fixtures
cannot cross-contaminate. Outputs contain synthetic text, never credentials.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--transport', choices=['exec', 'app-server'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--kinds', nargs='+', choices=['check_success', 'check_failure', 'pickup', 'pending_pickup', 'ooc', 'pending'],
                        default=['check_success', 'check_failure', 'pickup', 'ooc', 'pending'])
    parser.add_argument('--trace', action='store_true', help='Record synthetic model decisions and validation diagnostics')
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Refusing to overwrite an evaluation')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='coc-codex-eval-') as tmp:
        os.environ.update(PYTHON_DOTENV_DISABLED='1', LLM_PROVIDER='codex', ANALYSIS_PROVIDER='openai',
            CODEX_TRANSPORT=args.transport, CODEX_REASONING_EFFORT='medium', CODEX_MODEL='gpt-6-luna', CODEX_TIMEOUT='120',
            MAX_TOOL_ITERATIONS='6', MAX_TOOLS_PER_TURN='4', SCENARIO_RAG_ENABLED='false',
            DATA_DIR=tmp+'/groups', DB_PATH=tmp+'/state.db', BACKUP_DIR=tmp+'/backups',
            SCENARIO_LIBRARY_DIR=tmp+'/scenarios', IMPORT_DIR=tmp+'/imports',
            OPENAI_API_KEY='', ANTHROPIC_API_KEY='', GEMINI_API_KEY='', DISCORD_BOT_TOKEN='')
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from dataclasses import asdict

        from codex_pipeline_fixture import run_pipeline

        from app import observability
        from app.providers import codex_provider
        from app.providers.codex_transport import AppServerTransport, ExecTransport
        from app.services import turn_resolution
        logging.basicConfig(level=logging.ERROR)
        metrics = {}
        trace_enabled = args.trace
        original_event = observability.event
        def measured_event(name, **fields):
            if name == 'codex.decision.final_retry':
                metrics['final_retries'] += 1
            if name == 'codex.decision.incomplete_retry':
                metrics['incomplete_retries'] += 1
            if name == 'codex.decision.rejected':
                metrics['rejected_proposals'] += 1
            return original_event(name, **fields)
        observability.event = measured_event
        original_validation = turn_resolution.validate_resolution
        def traced_validation(*values, **options):
            result = original_validation(*values, **options)
            if trace_enabled:
                metrics['validations'].append(asdict(result))
            return result
        turn_resolution.validate_resolution = traced_validation
        original_conversation = codex_provider.run_conversation

        async def measured_conversation(*args, **kwargs):
            original_tool = args[5]
            async def measured_tool(name, arguments):
                result = await original_tool(name, arguments)
                metrics['tools'].append({'name': name, 'arguments': arguments,
                                        'ok': bool(result.get('ok')), 'pending': bool(result.get('pending')),
                                        **({'receipt': result} if trace_enabled else {})})
                return result
            args = (*args[:5], measured_tool, *args[6:])
            return await original_conversation(*args, **kwargs)
        codex_provider.run_conversation = measured_conversation
        transport_class = ExecTransport if args.transport == 'exec' else AppServerTransport
        original_request = transport_class.request

        async def measured_request(self, prompt, schema):
            metrics['requests'] += 1
            metrics['input_bytes'] += len(prompt.encode()) + len(json.dumps(schema).encode())
            started = time.monotonic()
            try:
                response = await original_request(self, prompt, schema)
                if trace_enabled:
                    payload = json.loads(prompt)
                    metrics['decisions'].append({'stage': payload['response_stage'], 'response': response,
                        'dynamic_system': payload['dynamic_system'], 'receipts': payload['current_conversation'],
                        'tools': [t['name'] for t in payload['tools']]})
                return response
            finally:
                metrics['request_seconds'].append(round(time.monotonic() - started, 3))
        transport_class.request = measured_request

        async def run():
            for repetition in range(args.repeats):
                for kind in args.kinds:
                    metrics.clear()
                    metrics.update(requests=0, tools=[], input_bytes=0, request_seconds=[], rejected_proposals=0, incomplete_retries=0, final_retries=0)
                    if trace_enabled:
                        metrics.update(decisions=[], validations=[])
                    start = time.monotonic()
                    try:
                        result = await run_pipeline(kind)
                    except Exception as exc:  # noqa: BLE001 - record failed cases and continue the evaluation
                        result = {'ok': False, 'kind': kind, 'failures': [type(exc).__name__]}
                    calls = metrics['tools']
                    expected = {'check_success': 'skill_check', 'check_failure': 'skill_check', 'pickup': 'add_carried_item', 'pending_pickup': 'add_carried_item'}
                    if kind in expected:
                        target = [tool for tool in calls if tool['name'] == expected[kind]]
                        tool_correct = len(target) == 1 and target[0]['ok']
                        if kind.startswith('check_') and target:
                            tool_correct = tool_correct and target[0]['arguments'].get('skill') == '偵查'
                    else:
                        tool_correct = not any(tool['name'] not in {'get_character_sheet', 'search_scenario', 'search_memory'} for tool in calls)
                    allowed_calls = {'get_character_sheet', 'search_scenario', 'search_memory'} | ({expected[kind]} if kind in expected else set())
                    tool_correct = (tool_correct and metrics['rejected_proposals'] == 0
                                    and all(t['ok'] and t['name'] in allowed_calls for t in calls))
                    result.update(transport=args.transport, repetition=repetition+1, **metrics,
                                  tool_correct=tool_correct, elapsed_seconds=round(time.monotonic()-start, 3))
                    with args.output.open('a') as stream:
                        stream.write(json.dumps(result, ensure_ascii=False)+'\n')
                    print(json.dumps({k: result[k] for k in ('transport','repetition','kind','ok','tool_correct','requests','elapsed_seconds','failures')}, ensure_ascii=False), flush=True)
        asyncio.run(run())


if __name__ == '__main__':
    main()
