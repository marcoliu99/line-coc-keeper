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

import json
import logging
import time
from typing import Callable

from app import config, observability
from app.config import (
    KEEPER_REASONING_EFFORT,
    KEEPER_TEMPERATURE,
    LLM_MAX_RETRIES,
    LLM_RETRY_BASE_DELAY_SECONDS,
    OPENAI_API_KEY,
    OPENAI_MODEL,
)
from app.providers import retry

# Populated per-process the first time the API rejects one of these — see
# _create_response.
_unsupported_params: set[str] = set()


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


def run_conversation(
    static_system: str,
    dynamic_system: str,
    tools: list[dict],
    history: list[dict],
    new_message: str,
    execute_tool: Callable[[str, dict], dict],
    max_iterations: int,
    previous_response_id: str = "",
    on_response_id: Callable[[str], None] | None = None,
) -> str:
    if not OPENAI_API_KEY:
        return "（尚未設定 OPENAI_API_KEY，守密人無法回應，請管理員檢查 .env 設定）"

    import openai

    client = openai.OpenAI(api_key=OPENAI_API_KEY)

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
            response = _create_response(client, _log_iteration=iteration, **request_kwargs)
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
                response = _create_response(client, _log_iteration=iteration, **request_kwargs)
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
            result = execute_tool(fc.name, args)
            next_input_items.append({
                "type": "function_call_output",
                "call_id": fc.call_id,
                "output": json.dumps(result, ensure_ascii=False),
            })
        active_previous_response_id = response.id
        input_items = next_input_items

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
    except Exception:
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
    except Exception:
        return None
