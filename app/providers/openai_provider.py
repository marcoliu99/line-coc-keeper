"""OpenAI provider adapter — uses the **Responses API**
(`client.responses.create(...)`), not the older Chat Completions API.

This project originally used Chat Completions (matching markitdown-ocr's
hard-coded shape — see app/markitdown_shim.py, which still targets that on
purpose). But OpenAI's own current Python library docs
(https://developers.openai.com/api/docs/libraries?language=python — checked
directly, not assumed from memory) show `client.responses.create(model=...,
input=...)` as the recommended call for new integrations, so the Keeper path
was rewritten to match. The exact field names below (FunctionToolParam's
flat {type, name, description, parameters} shape, ResponseFunctionToolCall's
{call_id, name, arguments}, FunctionCallOutput's {type: "function_call_output",
call_id, output}) were confirmed against this project's installed `openai`
SDK's actual type stubs (openai/types/responses/*.py), not guessed from the
docs page alone.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable

from app import config, observability
from app.config import (
    KEEPER_REASONING_EFFORT,
    KEEPER_TEMPERATURE,
    LLM_MAX_RETRIES,
    LLM_REQUEST_TIMEOUT_SECONDS,
    LLM_RETRY_BASE_DELAY_SECONDS,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    PROVIDER_SHUTDOWN_GRACE_SECONDS,
)
from app.providers import retry
from app.providers.client_lifecycle import AsyncClientLifecycle

# Populated per-process the first time the API rejects one of these — see
# _create_response.
_unsupported_params: set[str] = set()
_client_lifecycle = AsyncClientLifecycle("openai", PROVIDER_SHUTDOWN_GRACE_SECONDS)


async def _close_client(client) -> None:
    close = getattr(client, "aclose", None) or getattr(client, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


def _create_client():
    import openai

    return openai.AsyncOpenAI(api_key=OPENAI_API_KEY, max_retries=0)


async def _close_lifecycle_client(client, _owner) -> None:
    await _close_client(client)


async def get_async_client():
    """Return an event-loop-scoped, lazily initialized OpenAI client."""
    return await _client_lifecycle.get_or_create(_create_client, _close_lifecycle_client)


async def shutdown_async_client() -> None:
    await _client_lifecycle.shutdown(_close_lifecycle_client)


@contextlib.asynccontextmanager
async def _request_scope():
    client, state = await _client_lifecycle.acquire(_create_client, _close_lifecycle_client)
    try:
        yield client
    finally:
        _client_lifecycle.release(state)


def _create_response(client, *, _log_iteration: int | None = None, **kwargs):
    """Some model tiers (reasoning-focused releases in particular) reject
    certain optional parameters outright with a 400 instead of silently
    ignoring them — confirmed for `temperature`; `reasoning` is the same
    class of per-model-support risk. This project stays model-agnostic on
    purpose (OPENAI_MODEL is whatever's configured in .env, not hardcoded
    here — see that setting's own caveat), so rather than hardcoding which
    models support what, detect a rejection once per process and stop
    sending that specific parameter for the rest of this run. `while True`
    terminates naturally: each pass either returns, raises (the failure
    wasn't one of the two tracked params and wasn't a retryable transient
    failure either), removes one of at most two trackable params from
    kwargs, or backs off and retries a transient connection/server error
    (bounded by LLM_MAX_RETRIES via `connection_attempt` below) — so it's
    always either succeeded or raising within a bounded number of passes.

    Connection-retry logic lives inline here rather than going through
    app/providers/retry.py's call_with_retry — this function already runs
    its own custom while-loop for the unsupported-parameter case, and
    nesting a second independent retry loop inside it would just be two
    overlapping retry mechanisms fighting over the same exception. Reuses
    retry.is_retryable() for the classification so all three providers
    agree on what counts as transient."""
    for param in _unsupported_params:
        kwargs.pop(param, None)
    connection_attempt = 0
    while True:
        observability.increment_metric("iteration_count")
        observed = config.LOG_ENABLED
        started = time.perf_counter() if observed else 0.0
        reasoning = kwargs.get("reasoning") or {}
        observability.event(
            "llm.request.started",
            provider="openai",
            model=kwargs.get("model"),
            api_operation="responses.create",
            iteration=_log_iteration,
            timeout_ms=LLM_REQUEST_TIMEOUT_SECONDS * 1000,
            reasoning_effort=reasoning.get("effort") if isinstance(reasoning, dict) else None,
            tool_count=len(kwargs.get("tools") or []),
        )
        try:
            response = client.responses.create(**kwargs)
        except Exception as exc:
            exc_text = str(exc).lower()
            offending = next((p for p in ("temperature", "reasoning") if p in kwargs and p in exc_text), None)
            is_connection_retry = (
                offending is None and connection_attempt < LLM_MAX_RETRIES and retry.is_retryable(exc)
            )
            if observed:
                duration_ms = (time.perf_counter() - started) * 1000
                if offending is not None:
                    observability.increment_metric("retry_count")
                    observability.event(
                        "llm.retry",
                        level=logging.WARNING,
                        provider="openai",
                        model=kwargs.get("model"),
                        duration_ms=duration_ms,
                        removed_parameter=offending,
                        error_type=type(exc).__name__,
                        status="error",
                    )
                elif is_connection_retry:
                    observability.increment_metric("llm_retry_count")
                    observability.event(
                        "llm.request.retry",
                        level=logging.WARNING,
                        provider="openai",
                        model=kwargs.get("model"),
                        api_operation="responses.create",
                        duration_ms=duration_ms,
                        attempt=connection_attempt + 1,
                        max_attempts=LLM_MAX_RETRIES,
                        error_type=type(exc).__name__,
                        status="error",
                    )
                else:
                    observability.event(
                        "llm.request.failed",
                        level=logging.ERROR,
                        provider="openai",
                        model=kwargs.get("model"),
                        duration_ms=duration_ms,
                        error_type=type(exc).__name__,
                        status="error",
                    )
            if offending is not None:
                _unsupported_params.add(offending)
                kwargs.pop(offending, None)
                continue
            if is_connection_retry:
                connection_attempt += 1
                time.sleep(LLM_RETRY_BASE_DELAY_SECONDS * (2 ** (connection_attempt - 1)))
                continue
            raise
        else:
            if observed:
                observability.event(
                    "llm.request.completed",
                    provider="openai",
                    model=kwargs.get("model"),
                    api_operation="responses.create",
                    iteration=_log_iteration,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    status="success",
                    **observability.usage_fields(response),
                )
            return response


def _is_invalid_previous_response_id_error(
    exc: Exception,
    openai_module,
    previous_response_id: str,
) -> bool:
    sdk_error_types = tuple(
        error_type
        for name in ("BadRequestError", "NotFoundError")
        if isinstance(error_type := getattr(openai_module, name, None), type)
    )
    if sdk_error_types and not isinstance(exc, sdk_error_types):
        return False

    message = str(exc).lower()
    unsupported_markers = (
        "unsupported parameter",
        "cannot be used",
        "can't be used",
        "request shape",
        "invalid request",
        "schema",
    )
    if any(marker in message for marker in unsupported_markers):
        return False

    invalid_markers = (
        "invalid",
        "not found",
        "does not exist",
        "doesn't exist",
        "no such",
        "unknown",
        "expired",
        "stale",
    )

    previous_id_markers = (
        "previous_response_id",
        "previous response",
        "previous_response",
        previous_response_id.lower(),
    )
    if any(marker in message for marker in previous_id_markers) and any(
        marker in message for marker in invalid_markers
    ):
        return True

    response_missing_markers = (
        "response not found",
        "response does not exist",
        "response doesn't exist",
        "no such response",
        "unknown response",
        "response id not found",
        "response_id not found",
        "response id does not exist",
        "response_id does not exist",
    )
    return any(marker in message for marker in response_missing_markers)


async def _create_response_async(_client=None, *, _log_iteration: int | None = None, **kwargs):
    """Async Responses API helper preserving unsupported-parameter fallback."""
    for param in _unsupported_params:
        kwargs.pop(param, None)
    logical_request_id = observability.new_id("llm")
    while True:
        request_metrics: dict[str, int | None] = {}
        reasoning = kwargs.get("reasoning") or {}
        with observability.context(provider_request_id=logical_request_id), observability.span(
            "llm.request",
            provider="openai",
            model=kwargs.get("model"),
            api_operation="responses.create",
            logical_request_id=logical_request_id,
            iteration=_log_iteration,
            timeout_ms=LLM_REQUEST_TIMEOUT_SECONDS * 1000,
            reasoning_effort=reasoning.get("effort") if isinstance(reasoning, dict) else None,
            tool_count=len(kwargs.get("tools") or []),
            metrics=request_metrics,
        ):
                try:
                    async with _request_scope() as client:
                        async def request_once():
                            async with asyncio.timeout(LLM_REQUEST_TIMEOUT_SECONDS):
                                return await client.responses.create(**kwargs)

                        response = await retry.async_call_with_retry(
                            request_once, provider="openai", operation="responses.create",
                            request_id=logical_request_id,
                        )
                except Exception as exc:
                    exc_text = str(exc).lower()
                    offending = next(
                        (p for p in ("temperature", "reasoning") if p in kwargs and p in exc_text),
                        None,
                    )
                    if offending is None:
                        raise
                    _unsupported_params.add(offending)
                    kwargs.pop(offending, None)
                    observability.event(
                        "llm.retry",
                        level=logging.WARNING,
                        provider="openai",
                        model=kwargs.get("model"),
                        removed_parameter=offending,
                        error_type=type(exc).__name__,
                        status="error",
                    )
                    continue
        return response


async def run_conversation(
    static_system: str,
    dynamic_system: str,
    tools: list[dict],
    history: list[dict],
    new_message: str,
    execute_tool: Callable[[str, dict], Awaitable[dict]],
    max_iterations: int,
    previous_response_id: str = "",
    on_response_id: Callable[[str], None] | None = None,
    enable_wrapup: bool = True,
) -> str:
    if not OPENAI_API_KEY:
        return "（尚未設定 OPENAI_API_KEY，守密人無法回應，請管理員檢查 .env 設定）"

    import openai

    # Responses API tools are flat (no nested "function" wrapper, unlike Chat
    # Completions) — see FunctionToolParam in the SDK's type stubs.
    openai_tools = [
        {
            "type": "function",
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        }
        for t in tools
    ]

    # instructions is the Responses API's dedicated system-prompt field —
    # unlike the Chat Completions provider, this doesn't need a "system" role
    # message mixed into the input list.
    instructions = f"{static_system}\n\n{dynamic_system}"

    if previous_response_id:
        input_items: list[dict] = [{"role": "user", "content": new_message}]
        active_previous_response_id: str | None = previous_response_id
    else:
        input_items = [{"role": entry["role"], "content": entry["content"]} for entry in history]
        input_items.append({"role": "user", "content": new_message})
        active_previous_response_id = None

    # Omitted entirely (not sent as an empty/None value) when
    # KEEPER_REASONING_EFFORT="" — that's the escape hatch back to the old
    # "don't touch this parameter at all" behavior for anyone who wants it.
    reasoning_kwargs = {"reasoning": {"effort": KEEPER_REASONING_EFFORT}} if KEEPER_REASONING_EFFORT else {}

    final_text = "（守密人一時語塞，請再說一次剛才的行動）"
    for iteration in range(max_iterations):
        request_kwargs = {
            "model": OPENAI_MODEL,
            "instructions": instructions,
            "input": input_items,
            "tools": openai_tools,
            "temperature": KEEPER_TEMPERATURE,
            **reasoning_kwargs,
        }
        if active_previous_response_id:
            request_kwargs["previous_response_id"] = active_previous_response_id
        try:
            response = await _create_response_async(_log_iteration=iteration, **request_kwargs)
        except Exception as exc:
            if (
                iteration == 0
                and previous_response_id
                and active_previous_response_id == previous_response_id
                and _is_invalid_previous_response_id_error(exc, openai, previous_response_id)
            ):
                observability.event(
                    "llm.fallback", level=logging.WARNING, provider="openai",
                    fallback_kind="previous_response_id", reason="invalid_previous_response_id",
                )
                input_items = [{"role": entry["role"], "content": entry["content"]} for entry in history]
                input_items.append({"role": "user", "content": new_message})
                active_previous_response_id = None
                request_kwargs["input"] = input_items
                request_kwargs.pop("previous_response_id", None)
                response = await _create_response_async(_log_iteration=iteration, **request_kwargs)
            else:
                raise

        function_calls = [item for item in response.output if item.type == "function_call"]

        if not function_calls:
            final_text = (response.output_text or "").strip() or final_text
            if on_response_id is not None:
                on_response_id(response.id)
            break

        next_input_items: list[dict] = []
        for fc in function_calls:
            args = json.loads(fc.arguments or "{}")
            result = await execute_tool(fc.name, args)
            next_input_items.append({
                "type": "function_call_output",
                "call_id": fc.call_id,
                "output": json.dumps(result, ensure_ascii=False),
            })
        active_previous_response_id = response.id
        input_items = next_input_items
    else:
        # Every iteration up to max_iterations returned tool calls — the
        # Keeper never got a turn to produce actual narration, even though
        # the tool calls it did make (start_combat, add_npc_to_combat, HP
        # changes, ...) already executed and saved for real. Returning the
        # placeholder here would silently leave state and narration out of
        # sync (the player never told combat started, etc.), so spend one
        # more request with tools disabled to force a plain-text wrap-up of
        # whatever just happened instead.
        #
        # Gated by enable_wrapup because this text isn't always what ends up
        # in front of the player: app/agents/executor.py's Supervisor-path
        # caller discards run_conversation's return value entirely (only the
        # tool calls' side effects matter there) and app/agents/supervisor.py
        # always runs a separate Narrator call afterward regardless of how
        # Executor's turn went — so for that caller, this whole extra request
        # would be a real API call (with the retry/timeout budget that
        # implies) whose output could never reach the player. Only
        # app/keeper.py's legacy run_turn path (its own single combined
        # tool+narration call, no separate Narrator) actually needs this.
        if enable_wrapup:
            wrapup_kwargs = {
                "model": OPENAI_MODEL,
                "instructions": (
                    f"{instructions}\n\n"
                    "（系統提示：本回合的工具呼叫額度已用完，接下來不能再呼叫任何工具。"
                    "請根據上面剛執行的工具結果，直接用一段文字向玩家說明剛才發生的事，"
                    "不要再嘗試呼叫工具。）"
                ),
                "input": input_items,
                "previous_response_id": active_previous_response_id,
                "temperature": KEEPER_TEMPERATURE,
                **reasoning_kwargs,
            }
            try:
                wrapup_response = await _create_response_async(_log_iteration=max_iterations, **wrapup_kwargs)
                # Reading .output_text is kept inside this try, not a
                # separate else clause, so a malformed/incomplete/safety-
                # filtered wrap-up response also falls back to the
                # placeholder instead of propagating out of run_conversation
                # uncaught (same class of bug fixed for gemini_provider.py's
                # .text property in an earlier review round — missed here
                # and in anthropic_provider.py at the time, now fixed in all
                # three).
                wrapup_text = (wrapup_response.output_text or "").strip()
                if wrapup_text:
                    final_text = wrapup_text
                    if on_response_id is not None:
                        on_response_id(wrapup_response.id)
            except Exception:  # noqa: BLE001 - fall back to placeholder text rather than fail the turn
                observability.event(
                    "llm.turn.wrapup_failed", level=logging.WARNING, provider="openai",
                )

    return final_text


def analyze_image(png_bytes: bytes, tool: dict, prompt_text: str) -> dict | None:
    """Vision + a single forced tool call via the Responses API — used by
    app/scene_map.py's analyze_page_image, not the Keeper conversation loop
    above. Image input uses the {"type": "input_image", "image_url": <data
    URL>} shape (ResponseInputImageContentParam) and tool_choice forces the
    one tool by name (ToolChoiceFunctionParam) — both confirmed against this
    project's installed `openai` SDK type stubs. Returns the tool call's
    parsed arguments dict, or None on any failure (no OPENAI_API_KEY, the
    call raised, or no matching function_call came back)."""
    if not OPENAI_API_KEY:
        return None
    try:
        import base64

        import openai

        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        image_b64 = base64.standard_b64encode(png_bytes).decode("utf-8")
        response = _create_response(client,
            model=OPENAI_MODEL,
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt_text},
                    {"type": "input_image", "image_url": f"data:image/png;base64,{image_b64}"},
                ],
            }],
            tools=[{
                "type": "function",
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            }],
            tool_choice={"type": "function", "name": tool["name"]},
        )
        for item in response.output:
            if item.type == "function_call" and item.name == tool["name"]:
                return json.loads(item.arguments or "{}")
        return None
    except Exception:  # noqa: BLE001 - provider response shapes vary across SDK versions.
        return None


def analyze_text(text: str, tool: dict, prompt_text: str) -> dict | None:
    """Text-only sibling of analyze_image above — a single forced tool call,
    no image. Used by app/pregen_extractor.py. Returns the tool call's parsed
    arguments dict, or None on any failure (no OPENAI_API_KEY, the call
    raised, or no matching function_call came back)."""
    if not OPENAI_API_KEY:
        return None
    try:
        import openai

        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        response = _create_response(client,
            model=OPENAI_MODEL,
            input=[{"role": "user", "content": f"{prompt_text}\n\n{text}"}],
            tools=[{
                "type": "function",
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            }],
            tool_choice={"type": "function", "name": tool["name"]},
        )
        for item in response.output:
            if item.type == "function_call" and item.name == tool["name"]:
                return json.loads(item.arguments or "{}")
        return None
    except Exception:  # noqa: BLE001 - provider response shapes vary across SDK versions.
        return None
