"""Codex decisions, validated locally, executed exclusively by the host callback."""
from __future__ import annotations

import asyncio
import contextvars
import functools
import json
import time
import uuid
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import ParamSpec, TypeVar

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from app import config, observability
from app.providers import turn_budget
from app.providers.codex_transport import AppServerTransport, CodexError, ExecTransport

CODEX_MODEL = config.CODEX_MODEL
SUPPORTS_DYNAMIC_TOOLS = True
SUPPORTS_RESPONSE_STAGE = True
P = ParamSpec('P')
T = TypeVar('T')


@dataclass
class TurnBudget:
    turn_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    tools_used: int = 0
    attempted: set[str] = field(default_factory=set)


_current: contextvars.ContextVar[TurnBudget | None] = contextvars.ContextVar('codex_turn', default=None)
_semaphores: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_active: weakref.WeakSet[asyncio.Task] = weakref.WeakSet()


async def shutdown_async_client() -> None:
    loop = asyncio.get_running_loop()
    current = asyncio.current_task()
    pending = [task for task in _active if task is not current and task.get_loop() is loop]
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


def with_codex_turn(fn: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    @functools.wraps(fn)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        token = _current.set(TurnBudget())
        try:
            return await fn(*args, **kwargs)
        finally:
            _current.reset(token)
    return wrapped


def semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _semaphores:
        _semaphores[loop] = asyncio.Semaphore(config.CODEX_MAX_CONCURRENCY)
    return _semaphores[loop]


def response_schema(tools: list[dict]) -> dict:
    # Root object + nested anyOf is accepted by Codex structured outputs.
    final = {'type': 'object', 'properties': {
        'type': {'type': 'string', 'enum': ['final']},
        'content': {'type': 'string'},
    }, 'required': ['type', 'content'], 'additionalProperties': False}
    variants = [final]
    if tools:
        # Arguments are encoded as a JSON string on the wire: existing optional
        # CoC fields need not become required by strict structured outputs.
        variants.append({'type': 'object', 'properties': {
            'type': {'type': 'string', 'enum': ['tool_call']},
            'name': {'type': 'string', 'enum': [tool['name'] for tool in tools]},
            'arguments_json': {'type': 'string'},
        }, 'required': ['type', 'name', 'arguments_json'], 'additionalProperties': False})
    return {'type': 'object', 'properties': {'decision': {'anyOf': variants}},
            'required': ['decision'], 'additionalProperties': False}


def _unique_object(pairs: list[tuple]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def parse_decision(text: str, tools: list[dict]) -> dict:
    def reject_constant(_value: str) -> None:
        raise ValueError('nonfinite number')
    obj = json.loads(text, object_pairs_hook=_unique_object, parse_constant=reject_constant)
    Draft202012Validator(response_schema(tools)).validate(obj)
    decision = obj['decision']
    if decision['type'] == 'final':
        if not decision['content'].strip():
            raise ValueError('empty final')
        return decision
    arguments = json.loads(decision.pop('arguments_json'), object_pairs_hook=_unique_object,
                           parse_constant=reject_constant)
    tool = next(t for t in tools if t['name'] == decision['name'])
    schema = dict(tool['input_schema'])
    # Deny host-only fields even when an older schema omits this constraint.
    schema.setdefault('additionalProperties', False)
    Draft202012Validator(schema).validate(arguments)
    if not isinstance(arguments, dict):
        raise TypeError('arguments must be an object')
    if any(key.startswith('_') for key in arguments):
        raise ValueError('host-only argument')
    return {**decision, 'arguments': arguments}


async def run_conversation(
    static_system: str, dynamic_system: str, tools: list[dict], history: list[dict],
    new_message: str, execute_tool: Callable[[str, dict], Awaitable[dict]],
    max_iterations: int, enable_wrapup: bool = True,
    response_stage: str = 'default', tools_for_request: Callable[[], list[dict]] | None = None,
) -> str:
    budget = _current.get() or TurnBudget()
    started = time.monotonic()
    deadline = started + config.CODEX_TIMEOUT
    transport = AppServerTransport() if config.CODEX_TRANSPORT == 'app-server' else ExecTransport()
    transcript: list[dict] = []
    repaired = False
    iterations = 0
    status = 'error'
    conversation_id = uuid.uuid4().hex

    def remaining() -> float:
        local = deadline - time.monotonic()
        if local <= 0:
            raise TimeoutError('codex_conversation_deadline')
        return turn_budget.remaining(local) or local

    async def bounded(factory):
        limit = remaining()
        return await asyncio.wait_for(factory(), limit)

    gate = semaphore()
    acquired = False
    task = asyncio.current_task()
    if task is not None:
        _active.add(task)
    try:
        await bounded(gate.acquire)
        acquired = True
        observability.event('codex.admitted', turn_id=observability.current_context().get("turn_id") or budget.turn_id,
                            provider_conversation_id=conversation_id, queue_ms=int((time.monotonic() - started) * 1000))
        for index in range(max_iterations):
            iterations += 1
            current_tools = tools_for_request() if tools_for_request else tools
            # Intersect dynamic tools with the caller's initial permissions.
            allowed = {t['name'] for t in tools}
            current_tools = [t for t in current_tools if t['name'] in allowed]
            if budget.tools_used >= config.MAX_TOOLS_PER_TURN or (enable_wrapup and index == max_iterations - 1):
                current_tools = []
            prompt = json.dumps({
                'instructions': 'Respond with {"decision": ...}. For tools encode arguments as arguments_json. '
                                'Use only listed game tools. Never use native Codex tools. '
                                'If no tools remain, report only verified results and unfinished work.',
                'static_system': static_system, 'dynamic_system': dynamic_system,
                'history': history, 'new_message': new_message, 'tools': current_tools,
                'current_conversation': transcript, 'response_stage': response_stage,
                'remaining_tool_budget': max(0, config.MAX_TOOLS_PER_TURN - budget.tools_used),
            }, ensure_ascii=False)
            raw = await bounded(lambda prompt=prompt, current_tools=current_tools: transport.request(prompt, response_schema(current_tools)))
            try:
                decision = parse_decision(raw, current_tools)
            except (ValueError, TypeError, ValidationError, StopIteration, RecursionError):
                if repaired:
                    raise CodexError('codex_invalid_decision') from None
                repaired = True
                transcript.append({'role': 'host', 'error': 'invalid_decision',
                    'instruction': 'Return one valid decision matching the supplied schema and tool arguments.'})
                continue
            if decision['type'] == 'final':
                status = 'success'
                return decision['content']
            name, arguments = decision['name'], decision['arguments']
            fresh = tools_for_request() if tools_for_request else tools
            if name not in {t['name'] for t in fresh} or budget.tools_used >= config.MAX_TOOLS_PER_TURN:
                raise CodexError('codex_tool_not_allowed')
            identity = json.dumps([name, arguments], sort_keys=True, ensure_ascii=False)
            if identity in budget.attempted:
                # Same-state retries are unsafe even after a tool exception. The
                # caller may have committed before raising; require a new action.
                raise CodexError('codex_duplicate_tool_attempt')
            remaining()  # Do not start another mutation after deadline.
            budget.attempted.add(identity)
            budget.tools_used += 1
            try:
                # Do not cancel a committed/worker-thread mutation on LLM timeout.
                # Existing gateway owns cancellation-safe persistence.
                result = await execute_tool(name, arguments)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - contain host tool failures without replay
                result = {'ok': False, 'error': 'tool_execution_failed',
                          'completion_uncertain': True, 'retry_allowed': False}
            transcript.append({'role': 'tool', 'name': name, 'arguments': arguments, 'result': result})
        raise CodexError('codex_iteration_limit')
    except asyncio.CancelledError:
        status = 'cancelled'
        raise
    finally:
        try:
            await transport.close()
        finally:
            if acquired:
                gate.release()
            if task is not None:
                _active.discard(task)
            observability.event('codex.conversation', provider_conversation_id=conversation_id,
                turn_id=observability.current_context().get('turn_id') or budget.turn_id, transport=config.CODEX_TRANSPORT, model=CODEX_MODEL,
                stage=response_stage, status=status, reasoning_effort=config.CODEX_REASONING_EFFORT,
                requests=iterations, tools=budget.tools_used,
                duration_ms=int((time.monotonic() - started) * 1000))
