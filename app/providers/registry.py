"""Shared conversation registry; document analysis remains a separate capability."""
from collections.abc import Awaitable, Callable
from typing import Protocol

from app.providers import (
    anthropic_provider,
    codex_provider,
    gemini_provider,
    openai_provider,
)


class ConversationProvider(Protocol):
    async def run_conversation(
        self, static_system: str, dynamic_system: str, tools: list[dict], history: list[dict],
        new_message: str, execute_tool: Callable[[str, dict], Awaitable[dict]],
        max_iterations: int, enable_wrapup: bool = True,
    ) -> str: ...


CONVERSATION_PROVIDERS = {
    'openai': openai_provider, 'anthropic': anthropic_provider,
    'gemini': gemini_provider, 'codex': codex_provider,
}
ANALYSIS_PROVIDERS = {key: value for key, value in CONVERSATION_PROVIDERS.items() if key != 'codex'}


def supports_dynamic_tools(provider) -> bool:
    return bool(getattr(provider, 'SUPPORTS_DYNAMIC_TOOLS', False))


def supports_response_stage(provider) -> bool:
    return bool(getattr(provider, 'SUPPORTS_RESPONSE_STAGE', False))
