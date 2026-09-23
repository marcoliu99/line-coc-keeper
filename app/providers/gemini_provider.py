"""Google Gemini provider adapter (manual function-calling loop via google-genai).

Built from Google's published `google-genai` SDK docs (client.models.generate_content,
FunctionDeclaration/Tool, manual function-calling loop). Not exercised against a
live Gemini API key during development — the Gen AI SDK surface has moved fast, so
if this misbehaves, checking the exact attribute names against the current
`google-genai` docs/changelog before assuming the game logic is at fault is the
first debugging step.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

from app import observability
from app.config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    KEEPER_TEMPERATURE,
    LLM_REQUEST_TIMEOUT_SECONDS,
    LOG_INCLUDE_USAGE,
    LOG_SLOW_OPERATION_MS,
    PROVIDER_SHUTDOWN_GRACE_SECONDS,
)
from app.providers import retry
from app.providers.client_lifecycle import AsyncClientLifecycle

_client_lifecycle = AsyncClientLifecycle("gemini", PROVIDER_SHUTDOWN_GRACE_SECONDS)


async def _close_client(client) -> None:
    close = getattr(client, "aclose", None) or getattr(client, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


def _create_client():
    from google import genai

    owner = genai.Client(api_key=GEMINI_API_KEY)
    return getattr(owner, "aio", owner), owner


async def _close_lifecycle_client(client, owner) -> None:
    if client is not None:
        await _close_client(client)
    if owner is not None and owner is not client:
        await _close_client(owner)


async def get_async_client():
    """Return the Google GenAI async surface scoped to the current loop."""
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


async def run_conversation(
    static_system: str,
    dynamic_system: str,
    tools: list[dict],
    history: list[dict],
    new_message: str,
    execute_tool: Callable[[str, dict], Awaitable[dict]],
    max_iterations: int,
) -> str:
    if not GEMINI_API_KEY:
        return "（尚未設定 GEMINI_API_KEY，守密人無法回應，請管理員檢查 .env 設定）"

    from google.genai import types

    function_declarations = [
        types.FunctionDeclaration(
            name=t["name"],
            description=t["description"],
            parameters_json_schema=t["input_schema"],
        )
        for t in tools
    ]
    gemini_tools: list[Any] = [types.Tool(function_declarations=function_declarations)]
    config = types.GenerateContentConfig(
        system_instruction=f"{static_system}\n\n{dynamic_system}",
        tools=gemini_tools,
        temperature=KEEPER_TEMPERATURE,
    )

    contents: Any = []
    for entry in history:
        role = "model" if entry["role"] == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=entry["content"])]))
    contents.append(types.Content(role="user", parts=[types.Part(text=new_message)]))

    final_text = "（守密人一時語塞，請再說一次剛才的行動）"
    for iteration in range(max_iterations):
        observability.increment_metric("iteration_count")
        request_metrics: dict[str, int | None] = {}
        logical_request_id = observability.new_id("llm")
        with observability.context(provider_request_id=logical_request_id), observability.span(
            "llm.request",
            provider="gemini",
            model=GEMINI_MODEL,
            logical_request_id=logical_request_id,
            iteration=iteration,
            timeout_ms=LLM_REQUEST_TIMEOUT_SECONDS * 1000,
            tool_count=len(function_declarations),
            slow_threshold_ms=LOG_SLOW_OPERATION_MS,
            metrics=request_metrics,
        ):
            async with _request_scope() as client:
                async def request_once():
                    async with asyncio.timeout(LLM_REQUEST_TIMEOUT_SECONDS):
                        return await client.models.generate_content(
                            model=GEMINI_MODEL, contents=contents, config=config
                        )

                response = await retry.async_call_with_retry(
                    request_once, provider="gemini", operation="generate_content",
                    request_id=logical_request_id,
                )
            usage = getattr(response, "usage_metadata", None)
            if LOG_INCLUDE_USAGE:
                request_metrics.update(
                    input_tokens=getattr(usage, "prompt_token_count", None),
                    cached_input_tokens=getattr(usage, "cached_content_token_count", None),
                    output_tokens=getattr(usage, "candidates_token_count", None),
                    reasoning_tokens=getattr(usage, "thoughts_token_count", None),
                )
        if LOG_INCLUDE_USAGE:
            observability.event(
                "llm.usage",
                provider="gemini",
                model=GEMINI_MODEL,
                input_tokens=getattr(usage, "prompt_token_count", None),
                cached_input_tokens=getattr(usage, "cached_content_token_count", None),
                output_tokens=getattr(usage, "candidates_token_count", None),
                reasoning_tokens=getattr(usage, "thoughts_token_count", None),
            )
        candidate = response.candidates[0]
        contents.append(candidate.content)

        function_calls = response.function_calls or []
        if not function_calls:
            final_text = (response.text or "").strip() or final_text
            break

        response_parts = []
        for fc in function_calls:
            result = await execute_tool(fc.name, dict(fc.args or {}))
            response_parts.append(types.Part.from_function_response(name=fc.name, response=result))
        contents.append(types.Content(role="user", parts=response_parts))
    else:
        # Every iteration up to max_iterations returned function calls — the
        # calls themselves (start_combat, add_npc_to_combat, HP changes, ...)
        # already executed and saved for real, but the Keeper never got a
        # turn to narrate any of it. Spend one more request with no tools
        # offered to force a plain-text wrap-up instead of silently
        # returning the placeholder while state and narration diverge.
        wrapup_config = types.GenerateContentConfig(
            system_instruction=(
                f"{static_system}\n\n{dynamic_system}\n\n"
                "（系統提示：本回合的工具呼叫額度已用完，接下來不能再呼叫任何工具。"
                "請根據上面剛執行的工具結果，直接用一段文字向玩家說明剛才發生的事，"
                "不要再嘗試呼叫工具。）"
            ),
            temperature=KEEPER_TEMPERATURE,
        )
        try:
            logical_request_id = observability.new_id("llm")
            with observability.context(provider_request_id=logical_request_id), observability.span(
                "llm.request",
                provider="gemini",
                model=GEMINI_MODEL,
                logical_request_id=logical_request_id,
                iteration=max_iterations,
                timeout_ms=LLM_REQUEST_TIMEOUT_SECONDS * 1000,
                tool_count=0,
                slow_threshold_ms=LOG_SLOW_OPERATION_MS,
            ):
                async with _request_scope() as client:
                    async def wrapup_once(wrapup_config=wrapup_config):
                        async with asyncio.timeout(LLM_REQUEST_TIMEOUT_SECONDS):
                            return await client.models.generate_content(
                                model=GEMINI_MODEL, contents=contents, config=wrapup_config
                            )

                    wrapup_response = await retry.async_call_with_retry(
                        wrapup_once, provider="gemini", operation="generate_content",
                        request_id=logical_request_id,
                    )
        except Exception:  # noqa: BLE001 - fall back to placeholder text rather than fail the turn
            observability.event("llm.turn.wrapup_failed", level=logging.WARNING, provider="gemini")
        else:
            wrapup_text = (wrapup_response.text or "").strip()
            if wrapup_text:
                final_text = wrapup_text

    return final_text


def analyze_image(png_bytes: bytes, tool: dict, prompt_text: str) -> dict | None:
    """Vision + a single forced tool call — used by app/scene_map.py's
    analyze_page_image, not the Keeper conversation loop above. Forces the
    one tool via ToolConfig(function_calling_config=FunctionCallingConfig(
    mode="ANY", allowed_function_names=[...])) — same "not exercised against
    a live key" caveat as the rest of this module applies here. Returns the
    tool call's args dict, or None on any failure (no GEMINI_API_KEY, the
    call raised, or no matching function call came back)."""
    if not GEMINI_API_KEY:
        return None
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        function_declaration = types.FunctionDeclaration(
            name=tool["name"], description=tool["description"], parameters_json_schema=tool["input_schema"]
        )
        config = types.GenerateContentConfig(
            tools=[types.Tool(function_declarations=[function_declaration])],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=cast(Any, "ANY"), allowed_function_names=[tool["name"]]
                )
            ),
        )
        contents: Any = [types.Content(role="user", parts=[
            types.Part.from_bytes(data=png_bytes, mime_type="image/png"),
            types.Part(text=prompt_text),
        ])]
        with observability.span("llm.request", provider="gemini", model=GEMINI_MODEL, api_operation="generate_content"):
            response = client.models.generate_content(model=GEMINI_MODEL, contents=contents, config=config)
        for fc in response.function_calls or []:
            if fc.name == tool["name"]:
                return dict(fc.args or {})
        return None
    except Exception:  # noqa: BLE001 - provider response shapes vary across SDK versions.
        return None


def analyze_text(text: str, tool: dict, prompt_text: str) -> dict | None:
    """Text-only sibling of analyze_image above — a single forced tool call,
    no image. Used by app/pregen_extractor.py. Returns the tool call's args
    dict, or None on any failure (no GEMINI_API_KEY, the call raised, or no
    matching function call came back)."""
    if not GEMINI_API_KEY:
        return None
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        function_declaration = types.FunctionDeclaration(
            name=tool["name"], description=tool["description"], parameters_json_schema=tool["input_schema"]
        )
        config = types.GenerateContentConfig(
            tools=[types.Tool(function_declarations=[function_declaration])],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=cast(Any, "ANY"), allowed_function_names=[tool["name"]]
                )
            ),
        )
        contents: Any = [types.Content(role="user", parts=[types.Part(text=f"{prompt_text}\n\n{text}")])]
        with observability.span("llm.request", provider="gemini", model=GEMINI_MODEL, api_operation="generate_content"):
            response = client.models.generate_content(model=GEMINI_MODEL, contents=contents, config=config)
        for fc in response.function_calls or []:
            if fc.name == tool["name"]:
                return dict(fc.args or {})
        return None
    except Exception:  # noqa: BLE001 - provider response shapes vary across SDK versions.
        return None
