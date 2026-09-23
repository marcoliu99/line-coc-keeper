"""Claude (Anthropic Messages API) provider adapter."""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

from app import observability
from app.config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    KEEPER_TEMPERATURE,
    LLM_REQUEST_TIMEOUT_SECONDS,
    LOG_INCLUDE_USAGE,
    LOG_SLOW_OPERATION_MS,
    PROVIDER_SHUTDOWN_GRACE_SECONDS,
)
from app.providers import retry
from app.providers.client_lifecycle import AsyncClientLifecycle

_client_lifecycle = AsyncClientLifecycle("anthropic", PROVIDER_SHUTDOWN_GRACE_SECONDS)


async def _close_client(client) -> None:
    close = getattr(client, "aclose", None) or getattr(client, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


def _create_client():
    import anthropic

    return anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY, max_retries=0)


async def _close_lifecycle_client(client, _owner) -> None:
    await _close_client(client)


async def get_async_client():
    """Return an event-loop-scoped, lazily initialized Anthropic client."""
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
    if not ANTHROPIC_API_KEY:
        return "（尚未設定 ANTHROPIC_API_KEY，守密人無法回應，請管理員檢查 .env 設定）"

    # Cache the large/stable static system block and the (fully static) tool
    # definitions; the small per-turn dynamic block is left uncached on purpose —
    # see app/keeper.py for why the split exists.
    anthropic_tools = [dict(t) for t in tools]
    if anthropic_tools:
        anthropic_tools[-1] = {**anthropic_tools[-1], "cache_control": {"type": "ephemeral"}}

    system_blocks = [
        {"type": "text", "text": static_system, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": dynamic_system},
    ]

    # `history` is the persisted log tail (see app/keeper.py) — a stable prefix
    # between consecutive turns as long as app/state.py hasn't trimmed it, so
    # caching through its last entry lets a long conversation reuse almost all of
    # it on every turn instead of re-billing the whole thing each time. Only the
    # newest 1-2 entries (this call's new_message, and whatever wasn't cached
    # yet) are ever paid for in full.
    messages = []
    for i, entry in enumerate(history):
        content = entry["content"]
        if i == len(history) - 1:
            content = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
        messages.append({"role": entry["role"], "content": content})
    messages.append({"role": "user", "content": new_message})

    final_text = "（守密人一時語塞，請再說一次剛才的行動）"
    for iteration in range(max_iterations):
        observability.increment_metric("iteration_count")
        request_metrics: dict[str, int | None] = {}
        logical_request_id = observability.new_id("llm")
        with observability.context(provider_request_id=logical_request_id), observability.span(
            "llm.request",
            provider="anthropic",
            model=ANTHROPIC_MODEL,
            logical_request_id=logical_request_id,
            iteration=iteration,
            timeout_ms=LLM_REQUEST_TIMEOUT_SECONDS * 1000,
            tool_count=len(anthropic_tools),
            slow_threshold_ms=LOG_SLOW_OPERATION_MS,
            metrics=request_metrics,
        ):
            async with _request_scope() as client:
                async def request_once():
                    async with asyncio.timeout(LLM_REQUEST_TIMEOUT_SECONDS):
                        return await client.messages.create(
                            model=ANTHROPIC_MODEL,
                            max_tokens=1024,
                            temperature=KEEPER_TEMPERATURE,
                            system=system_blocks,
                            tools=anthropic_tools,
                            messages=messages,
                        )

                response = await retry.async_call_with_retry(
                    request_once, provider="anthropic", operation="messages.create",
                    request_id=logical_request_id,
                )
            usage = getattr(response, "usage", None)
            if LOG_INCLUDE_USAGE:
                request_metrics.update(
                    input_tokens=getattr(usage, "input_tokens", None),
                    cached_input_tokens=getattr(usage, "cache_read_input_tokens", None),
                    output_tokens=getattr(usage, "output_tokens", None),
                )
        if LOG_INCLUDE_USAGE:
            observability.event(
                "llm.usage",
                provider="anthropic",
                model=ANTHROPIC_MODEL,
                input_tokens=getattr(usage, "input_tokens", None),
                cached_input_tokens=getattr(usage, "cache_read_input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
            )
        messages.append({"role": "assistant", "content": response.content})

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            final_text = "".join(b.text for b in response.content if b.type == "text").strip() or final_text
            break

        tool_results = []
        for tu in tool_uses:
            result = await execute_tool(tu.name, tu.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": json.dumps(result, ensure_ascii=False),
            })
        messages.append({"role": "user", "content": tool_results})
    else:
        # Every iteration up to max_iterations returned tool calls — the
        # tool calls themselves (start_combat, add_npc_to_combat, HP
        # changes, ...) already executed and saved for real, but the Keeper
        # never got a turn to narrate any of it. Spend one more request with
        # no tools offered to force a plain-text wrap-up instead of silently
        # returning the placeholder while state and narration diverge.
        wrapup_system = system_blocks + [{
            "type": "text",
            "text": (
                "（系統提示：本回合的工具呼叫額度已用完，接下來不能再呼叫任何工具。"
                "請根據上面剛執行的工具結果，直接用一段文字向玩家說明剛才發生的事，"
                "不要再嘗試呼叫工具。）"
            ),
        }]
        try:
            logical_request_id = observability.new_id("llm")
            with observability.context(provider_request_id=logical_request_id), observability.span(
                "llm.request",
                provider="anthropic",
                model=ANTHROPIC_MODEL,
                logical_request_id=logical_request_id,
                iteration=max_iterations,
                timeout_ms=LLM_REQUEST_TIMEOUT_SECONDS * 1000,
                tool_count=0,
                slow_threshold_ms=LOG_SLOW_OPERATION_MS,
            ):
                async with _request_scope() as client:
                    async def wrapup_once(wrapup_system=wrapup_system):
                        async with asyncio.timeout(LLM_REQUEST_TIMEOUT_SECONDS):
                            return await client.messages.create(
                                model=ANTHROPIC_MODEL,
                                max_tokens=1024,
                                temperature=KEEPER_TEMPERATURE,
                                system=wrapup_system,
                                messages=messages,
                            )

                    wrapup_response = await retry.async_call_with_retry(
                        wrapup_once, provider="anthropic", operation="messages.create",
                        request_id=logical_request_id,
                    )
        except Exception:  # noqa: BLE001 - fall back to placeholder text rather than fail the turn
            observability.event("llm.turn.wrapup_failed", level=logging.WARNING, provider="anthropic")
        else:
            wrapup_text = "".join(b.text for b in wrapup_response.content if b.type == "text").strip()
            if wrapup_text:
                final_text = wrapup_text

    return final_text


def analyze_image(png_bytes: bytes, tool: dict, prompt_text: str) -> dict | None:
    """Vision + a single forced tool call — used by app/scene_map.py's
    analyze_page_image, not the Keeper conversation loop above. Returns the
    tool's input dict, or None on any failure (no ANTHROPIC_API_KEY, the call
    raised, or no matching tool_use came back)."""
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import base64

        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        image_b64 = base64.standard_b64encode(png_bytes).decode("utf-8")
        with observability.span("llm.request", provider="anthropic", model=ANTHROPIC_MODEL, api_operation="messages.create"):
            response = cast(Any, client.messages).create(
                model=ANTHROPIC_MODEL, max_tokens=4096, tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
                    {"type": "text", "text": prompt_text},
                ]}],
            )
        for block in response.content:
            if block.type == "tool_use" and block.name == tool["name"]:
                return block.input
        return None
    except Exception:  # noqa: BLE001 - provider response shapes vary across SDK versions.
        return None


def analyze_text(text: str, tool: dict, prompt_text: str) -> dict | None:
    """Text-only sibling of analyze_image above — a single forced tool call,
    no image. Used by app/pregen_extractor.py. Returns the tool's input dict,
    or None on any failure (no ANTHROPIC_API_KEY, the call raised, or no
    matching tool_use came back)."""
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        with observability.span("llm.request", provider="anthropic", model=ANTHROPIC_MODEL, api_operation="messages.create"):
            response = cast(Any, client.messages).create(
                model=ANTHROPIC_MODEL, max_tokens=4096, tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
                messages=[{"role": "user", "content": f"{prompt_text}\n\n{text}"}],
            )
        for block in response.content:
            if block.type == "tool_use" and block.name == tool["name"]:
                return block.input
        return None
    except Exception:  # noqa: BLE001 - provider response shapes vary across SDK versions.
        return None
