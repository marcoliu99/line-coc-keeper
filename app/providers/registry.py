"""Shared conversation registry; document analysis remains a separate capability."""
from collections.abc import Awaitable, Callable
from typing import Protocol

from app import config
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
# Document/image analysis has its own setting (ANALYSIS_PROVIDER) because
# codex_provider has no analyze_text/analyze_image adapter yet (the Codex CLI
# itself takes images and an output schema); once it does, only this table
# changes.
ANALYSIS_PROVIDERS = {key: value for key, value in CONVERSATION_PROVIDERS.items() if key != 'codex'}


def conversation_provider():
    """The provider that runs Keeper turns (LLM_PROVIDER), or None if unsupported.

    Reads app.config at call time, so the setting is patched in one place.
    """
    return CONVERSATION_PROVIDERS.get(config.LLM_PROVIDER)


def require_conversation_provider():
    """conversation_provider(), raising a setup error when LLM_PROVIDER is unsupported."""
    provider = conversation_provider()
    if provider is None:
        raise ValueError(
            f'LLM_PROVIDER="{config.LLM_PROVIDER}" 不是支援的供應商，請在 .env 設成 '
            f'{"、".join(CONVERSATION_PROVIDERS)} 其中之一'
        )
    return provider


def analysis_provider():
    """The provider for document/image analysis (ANALYSIS_PROVIDER), or None."""
    return ANALYSIS_PROVIDERS.get(config.ANALYSIS_PROVIDER)


def supports_dynamic_tools(provider) -> bool:
    return bool(getattr(provider, 'SUPPORTS_DYNAMIC_TOOLS', False))


def supports_response_stage(provider) -> bool:
    return bool(getattr(provider, 'SUPPORTS_RESPONSE_STAGE', False))
