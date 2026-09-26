import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('trial_support', Path(__file__).parents[1] / 'scripts/experiments/token_admission_support.py')
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)


def test_budget_persists_completed_reservations_and_ages_out(tmp_path):
    budget = support.WindowBudget(tmp_path / 'ledger', limit=100)
    assert budget.reserve_or_delay(60, 10) == 0
    assert support.WindowBudget(tmp_path / 'ledger', limit=100).reserve_or_delay(50, 11) == 59
    assert budget.reserve_or_delay(50, 70) == 0
    with pytest.raises(ValueError):
        budget.reserve_or_delay(101, 71)


def test_deadline_prevents_wait(tmp_path):
    budget = support.WindowBudget(tmp_path / 'ledger', limit=100)
    budget.reserve_or_delay(100, support.time.time())
    with pytest.raises(TimeoutError):
        asyncio.run(budget.acquire(1, support.time.monotonic() + .01))


def test_history_preserves_complete_user_sections():
    history = [{'role': role, 'content': str(i)} for i in range(8) for role in ('user', 'assistant')]
    assert support.recent_history(history) == history[6:]
    assert len(history) == 16


def test_continuation_keeps_inherited_input():
    meter = support.InputMeter(SimpleNamespace(encode=lambda text, **kwargs: list(text)))
    meter.begin('sys', 'state', [{'role': 'user', 'content': 'past'}], 'new')
    first = meter.measure({'input': [], 'tools': []})
    response = SimpleNamespace(id='r1', output=[SimpleNamespace(model_dump=lambda: {'type': 'function_call'})])
    meter.record(response, first)
    second = meter.measure({'previous_response_id': 'r1', 'input': [{'output': 'tool result'}], 'tools': []})
    assert second['history'] == first['history']
    assert second['prior_outputs'] > 0 and second['tool_results'] > 0
    assert sum(second.values()) > sum(first.values())
