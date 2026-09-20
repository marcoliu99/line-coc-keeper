from __future__ import annotations

import asyncio
import logging

from app import keeper
from app import observability
from app.domain.models import AgentMessage, MechanicResult
from app.config import LLM_PROVIDER
from app.providers import anthropic_provider, gemini_provider, openai_provider
from app.services import prompt_config

_logger = logging.getLogger(__name__)
_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}


async def run_narrator(message: AgentMessage) -> tuple[str, list[tuple[str, str]], list[tuple[str | None, int]]]:
    """
    Runs the LLM loop for the Narrator Agent.
    This Agent has NO access to tools. It only generates narrative text
    based on the state and facts provided to it.

    Returns:
        (reply_text, private_messages, image_requests)
    """
    provider = _PROVIDERS[LLM_PROVIDER]

    state = message.payload["state"]
    user_id = message.payload.get("user_id", "")
    text = message.payload.get("text", "")
    display_name = message.payload.get("display_name", "玩家")
    speaker_role = message.payload.get("speaker_role", "player")
    resolved_location = message.payload.get("resolved_location")
    intent = message.payload.get("intent", "PURE_ROLEPLAY")
    mechanic_result: MechanicResult | None = message.payload.get("mechanic_result")
    rag_context = message.payload.get("rag_context", "")
    memory_context = message.payload.get("memory_context", "")

    static_system = prompt_config.build_narrator_static_prompt(keeper._build_static_prompt(state))
    dynamic_system = prompt_config.build_dynamic_prompt_with_context(
        keeper._build_dynamic_prompt(state, user_id, resolved_location, speaker_role), rag_context, memory_context
    )

    if intent == "GAMEPLAY_ACTION" and mechanic_result:
        dynamic_system += "\n\n" + prompt_config.build_mechanic_facts_block(mechanic_result)
    else:
        dynamic_system += "\n\n" + prompt_config.PURE_ROLEPLAY_BLOCK

    new_message = f"{display_name}：{text}"
    history = state.log

    def _no_tools(_name: str, _tool_input: dict) -> dict:
        # Narrator has no tools per the design spec — this is never actually
        # invoked (tools=[] below means the model has nothing to call), it's
        # only here because run_conversation's signature requires a callback.
        return {"ok": False, "error": "Narrator agent has no tools"}

    try:
        # run_conversation is synchronous (see every app/providers/*.py) —
        # dispatched via asyncio.to_thread like every other call site in
        # this codebase, not awaited directly.
        with observability.span("llm.turn", provider=LLM_PROVIDER, agent="narrator"):
            reply_text = await asyncio.to_thread(
                provider.run_conversation,
                static_system,
                dynamic_system,
                [],
                history,
                new_message,
                _no_tools,
                1,
            )
    except Exception:
        _logger.exception("Narrator LLM call failed")
        reply_text = "（守密人一時語塞，請再說一次剛才的行動）"

    private_messages = message.payload.get("private_messages", [])
    image_requests = message.payload.get("image_requests", [])
    return reply_text, private_messages, image_requests
