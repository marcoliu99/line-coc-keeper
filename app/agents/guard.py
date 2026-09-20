from __future__ import annotations

import asyncio
import logging

from app import observability
from app.domain.models import AgentMessage
from app.config import LLM_PROVIDER
from app.providers import anthropic_provider, gemini_provider, openai_provider
from app.services import prompt_config

_logger = logging.getLogger(__name__)
_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}


async def run_repair(message: AgentMessage, original_text: str, error_reason: str) -> str:
    """
    Runs the LLM loop for the Guard Agent to repair invalid narrative text.
    Falls back to the original text (rather than losing the reply entirely)
    if the repair call itself fails.
    """
    provider = _PROVIDERS[LLM_PROVIDER]

    dynamic_system = prompt_config.build_guard_dynamic_prompt(original_text, error_reason)
    new_message = "請修復並重新輸出這段敘述："

    def _no_tools(_name: str, _tool_input: dict) -> dict:
        return {"ok": False, "error": "Guard agent has no tools"}

    metrics: dict[str, int] = {}
    model = getattr(provider, f"{LLM_PROVIDER.upper()}_MODEL", None)
    try:
        with observability.metrics_context(metrics):
            with observability.span(
                "llm.turn", provider=LLM_PROVIDER, model=model, agent="guard",
                reasoning_effort=observability.llm_reasoning_effort(LLM_PROVIDER),
                metrics=metrics,
            ):
                repaired_text = await asyncio.to_thread(
                    provider.run_conversation, prompt_config.GUARD_SYSTEM_PROMPT,
                    dynamic_system, [], [], new_message, _no_tools, 1,
                )
    except Exception:
        observability.event("llm.failed", level=logging.ERROR, agent="guard", status="error")
        _logger.exception("Guard LLM call failed — keeping the original (unrepaired) narrative")
        return original_text

    return repaired_text.strip() or original_text
