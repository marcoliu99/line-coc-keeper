import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app import config
from app.providers import admission, openai_provider, retry, turn_budget
from app.services import input_budget


@pytest.fixture(autouse=True)
def isolated_controllers(monkeypatch):
    monkeypatch.setattr(admission, '_controllers', {})
    monkeypatch.setattr(retry, '_admission_semaphores', {})
    monkeypatch.setattr(config, 'OPENAI_RATE_LIMIT_SCOPE', '')
    monkeypatch.setattr(config, 'OPENAI_ADAPTIVE_ADMISSION_ENABLED', True)


def headers(remaining='10', reset='9s'):
    return {'x-ratelimit-limit-tokens': '100', 'x-ratelimit-remaining-tokens': remaining,
            'x-ratelimit-reset-tokens': reset}


def test_header_refill_reservations_and_stale_snapshots():
    controller = admission.Admission()
    with patch.object(admission.time, 'monotonic', return_value=100):
        controller.observe(headers())
        assert controller.delay(30) == pytest.approx(2)
    with patch.object(admission.time, 'monotonic', return_value=102):
        assert controller.delay(30, reserve=True) == 0
        assert controller.delay(20) == pytest.approx(2)
        controller.observe(headers('90', '1s'))  # late optimistic response cannot erase reservations
        assert controller.delay(20) >= 2
        controller.defer(7)
        controller.observe(headers('99', '100ms'))
        assert controller.delay(1) >= 7
    with patch.object(admission.time, 'monotonic', return_value=104):
        controller.defer(1)
        assert controller.delay(1) >= 5


@pytest.mark.parametrize('remaining,reset', [('NaN', '1s'), ('-1', '1s'), ('1', 'secret'), ('1', '0s')])
def test_bad_headers_do_not_create_quota(remaining, reset):
    controller = admission.Admission()
    controller.observe(headers(remaining, reset))
    assert not controller.quotas and controller.delay(999999) == 0


def test_unknown_context_uses_rpm_and_cooldown_but_never_zero_token_estimate():
    controller = admission.Admission()
    controller.observe(headers('0', '10s'))
    assert controller.delay(None) == 0
    assert controller.delay(20) > 0
    with pytest.raises(ValueError):
        controller.delay(101)
    controller.defer(1)
    assert controller.delay(None) > 0


def test_shared_scope_is_explicit(monkeypatch):
    assert admission.controller('a') is not admission.controller('b')
    monkeypatch.setattr(config, 'OPENAI_RATE_LIMIT_SCOPE', 'shared-pool')
    assert admission.controller('a') is admission.controller('b')


def test_history_budget_keeps_complete_sections_and_does_not_mutate(monkeypatch):
    monkeypatch.setattr(input_budget, '_encoding', lambda _: None)
    history = [{'role': role, 'content': f'{i}-' + 'x'*100} for i in range(6) for role in ('user', 'assistant')]
    before = list(history)
    selected = input_budget.select_history(history, 'unknown', 1, keep_turns=2)
    assert selected == history[-4:] and history == before
    assert input_budget.select_history(history, 'unknown', 0) == history
    # Tool-call and tool-output records stay in the same user-led section.
    section = [{'role': 'user', 'content': 'act'}, {'role': 'assistant', 'tool_calls': ['call']},
               {'role': 'tool', 'tool_call_id': 'call', 'content': 'result'}]
    assert input_budget.select_history(history + section, 'unknown', 1, keep_turns=1) == section


def test_cancelled_admission_does_not_hold_http_slot():
    async def exercise():
        controller = admission.Admission()
        controller.defer(60)
        fn = AsyncMock()
        task = asyncio.create_task(retry.async_call_with_retry(fn, provider='openai', operation='test', admission=controller))
        await asyncio.sleep(.01)
        sem = retry._admission_semaphore_for('openai')
        assert sem._value == retry.OPENAI_MAX_CONCURRENT_REQUESTS
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        fn.assert_not_awaited()
        assert sem._value == retry.OPENAI_MAX_CONCURRENT_REQUESTS
    asyncio.run(exercise())


def test_exhausted_429_still_cools_other_requests(monkeypatch):
    class RateLimitError(Exception):
        status_code = 429
        response = SimpleNamespace(headers={'retry-after': '20'})
    monkeypatch.setattr(retry, 'LLM_MAX_RETRIES', 0)
    async def exercise():
        controller = admission.Admission()
        with pytest.raises(RateLimitError):
            await retry.async_call_with_retry(AsyncMock(side_effect=RateLimitError()), provider='openai', operation='test', admission=controller)
        assert controller.delay(None) > 19
        fn = AsyncMock()
        token = turn_budget._deadline.set(time.monotonic() + .01)
        try:
            with pytest.raises(turn_budget.TurnDeadlineExceeded):
                await retry.async_call_with_retry(fn, provider='openai', operation='test', admission=controller)
            fn.assert_not_awaited()
        finally:
            turn_budget._deadline.reset(token)
    asyncio.run(exercise())


def test_deadline_bounds_api_attempt_and_releases_slot():
    async def exercise():
        calls = 0
        async def slow():
            nonlocal calls
            calls += 1
            await asyncio.sleep(10)
        token = turn_budget._deadline.set(time.monotonic() + .02)
        try:
            with pytest.raises(turn_budget.TurnDeadlineExceeded):
                await retry.async_call_with_retry(slow, provider='openai', operation='test')
            assert calls == 1
            assert retry._admission_semaphore_for('openai')._value == retry.OPENAI_MAX_CONCURRENT_REQUESTS
        finally:
            turn_budget._deadline.reset(token)
    asyncio.run(exercise())


def test_sdk_success_headers_are_observed_without_network(monkeypatch):
    import openai
    from openai import _base_client
    httpx = getattr(_base_client, "httpx2", None) or _base_client.httpx
    async def exercise():
        async def respond(request):
            return httpx.Response(200, headers=headers('20', '8s'), json={
                'id': 'resp_test', 'object': 'response', 'created_at': 0, 'model': config.OPENAI_MODEL,
                'status': 'completed', 'output': [], 'parallel_tool_calls': False,
                'tool_choice': 'auto', 'tools': [], 'usage': {'input_tokens': 80, 'output_tokens': 0, 'total_tokens': 80},
            })
        transport = httpx.MockTransport(respond)
        real_factory = openai.DefaultAsyncHttpxClient
        monkeypatch.setattr(openai, 'DefaultAsyncHttpxClient', lambda **kwargs: real_factory(transport=transport, **kwargs))
        monkeypatch.setattr(openai_provider, 'OPENAI_API_KEY', 'test-key')
        client = openai_provider._create_client()
        try:
            response = await client.responses.create(model=config.OPENAI_MODEL, input='test')
            assert response.status == 'completed'
            controller = admission.controller(config.OPENAI_MODEL)
            assert controller.quotas['tokens'].remaining == 20
            assert controller.delay(30) > 0
        finally:
            await client.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("status", ["incomplete", "failed", "cancelled"])
def test_incomplete_response_never_executes_tools_or_saves_id(monkeypatch, status):
    from unittest.mock import Mock
    response = SimpleNamespace(status=status, incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        output=[SimpleNamespace(type="function_call", name="remove_carried_item", arguments="{}", call_id="c1")], id="bad")
    create = AsyncMock(return_value=response)
    monkeypatch.setattr(openai_provider, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(openai_provider, "_create_response_async", create)
    execute, save = AsyncMock(), Mock()
    with pytest.raises(openai_provider.IncompleteResponseError):
        asyncio.run(openai_provider.run_conversation("static", "state", [], [], "act", execute, 3, on_response_id=save))
    execute.assert_not_awaited()
    save.assert_not_called()
    assert create.await_count == 1


@pytest.mark.parametrize("cap", [0, 1500])
def test_output_cap_and_inherited_usage_cover_tool_continuation(monkeypatch, cap):
    first = SimpleNamespace(status="completed", output=[SimpleNamespace(type="function_call", name="test", arguments="{}", call_id="c1")],
        id="first", usage=SimpleNamespace(input_tokens=900, output_tokens=50))
    second = SimpleNamespace(status="completed", output=[], output_text="done", id="second", usage=None)
    create = AsyncMock(side_effect=[first, second])
    monkeypatch.setattr(openai_provider, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(openai_provider, "_create_response_async", create)
    monkeypatch.setattr(config, "OPENAI_EXECUTOR_MAX_OUTPUT_TOKENS", cap)
    asyncio.run(openai_provider.run_conversation("static", "state", [], [], "act", AsyncMock(return_value={"ok": True}), 2, response_stage="executor"))
    for call in create.call_args_list:
        assert call.kwargs.get("max_output_tokens", 0) == cap
        assert ("max_output_tokens" in call.kwargs) == bool(cap)
    continuation = create.call_args_list[1].kwargs
    assert continuation["previous_response_id"] == "first"
    assert continuation["_input_tokens_estimate"] == 950 + cap + input_budget.estimate(continuation["input"], openai_provider.OPENAI_MODEL)


def test_nested_stages_share_one_deadline(monkeypatch):
    monkeypatch.setattr(config, "LLM_TURN_DEADLINE_SECONDS", 10)
    seen = []
    @turn_budget.with_turn_deadline
    async def nested():
        seen.append(turn_budget._deadline.get())
    @turn_budget.with_turn_deadline
    async def turn():
        seen.append(turn_budget._deadline.get())
        await nested()
    asyncio.run(turn())
    assert seen[0] is not None and seen[0] == seen[1]
    assert turn_budget._deadline.get() is None
