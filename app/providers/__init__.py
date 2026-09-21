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

from collections.abc import Awaitable, Callable
from typing import TypeAlias

ToolExecutor: TypeAlias = Callable[[str, dict], Awaitable[dict]]


async def shutdown_async_clients() -> None:
    """Close every provider client; safe to call more than once."""
    from app.providers import anthropic_provider, gemini_provider, openai_provider

    await openai_provider.shutdown_async_client()
    await anthropic_provider.shutdown_async_client()
    await gemini_provider.shutdown_async_client()
