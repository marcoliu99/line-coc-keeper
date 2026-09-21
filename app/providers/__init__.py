"""LLM provider adapters.

Each module here exposes one async function:

    await run_conversation(static_system, dynamic_system, tools, history,
                           new_message, execute_tool, max_iterations) -> str

so app/keeper.py (system prompt building, tool execution against GroupState, the
COC7e game logic) never needs to know which SDK is actually talking to the model.
`tools` is the common {"name", "description", "input_schema"} shape; each adapter
translates that into whatever its own SDK expects.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeAlias

from app import observability

ToolExecutor: TypeAlias = Callable[[str, dict], Awaitable[dict]]


async def shutdown_async_clients() -> None:
    """Close every provider client; safe to call more than once."""
    from app.providers import anthropic_provider, gemini_provider, openai_provider

    providers = (
        ("openai", openai_provider.shutdown_async_client),
        ("anthropic", anthropic_provider.shutdown_async_client),
        ("gemini", gemini_provider.shutdown_async_client),
    )
    results = await asyncio.gather(
        *(shutdown() for _, shutdown in providers),
        return_exceptions=True,
    )
    errors: list[BaseException] = []
    for (provider, _), result in zip(providers, results, strict=True):
        if not isinstance(result, BaseException):
            continue
        observability.event(
            "provider.shutdown.failed",
            level=logging.ERROR,
            provider=provider,
            status="cancelled" if isinstance(result, asyncio.CancelledError) else "error",
            error_type=type(result).__name__,
        )
        errors.append(result)
    if errors:
        raise errors[0]
