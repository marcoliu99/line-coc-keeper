from __future__ import annotations

import logging

from app import config, observability, spoiler_policy
from app.agents import rule_validator
from app.config import LLM_PROVIDER
from app.domain.models import AgentMessage
from app.providers import anthropic_provider, gemini_provider, openai_provider
from app.services import prompt_config

_logger = logging.getLogger(__name__)
_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}

MAX_REPAIR_ATTEMPTS = 2


async def run_repair(message: AgentMessage, original_text: str, error_reason: str) -> str:
    """
    Runs the LLM loop for the Guard Agent to repair invalid narrative text.
    Falls back to the original text (rather than losing the reply entirely)
    if the repair call itself fails.
    """
    provider = _PROVIDERS[LLM_PROVIDER]

    dynamic_system = prompt_config.build_guard_dynamic_prompt(original_text, error_reason)
    new_message = "請修復並重新輸出這段敘述："

    async def _no_tools(_name: str, _tool_input: dict) -> dict:
        return {"ok": False, "error": "Guard agent has no tools"}

    metrics: dict[str, int] = {}
    model = getattr(provider, f"{LLM_PROVIDER.upper()}_MODEL", None)
    try:
        with observability.metrics_context(metrics), observability.span(
            "llm.turn", provider=LLM_PROVIDER, model=model, agent="guard",
            reasoning_effort=observability.llm_reasoning_effort(LLM_PROVIDER),
            metrics=metrics,
        ):
            repaired_text = await provider.run_conversation(
                prompt_config.GUARD_SYSTEM_PROMPT, dynamic_system, [], [],
                new_message, _no_tools, 1,
            )
    except Exception:
        observability.event("llm.failed", level=logging.ERROR, agent="guard", status="error")
        _logger.exception("Guard LLM call failed — keeping the original (unrepaired) narrative")
        return original_text

    return repaired_text.strip() or original_text


async def enforce_narrative_safety(message: AgentMessage, reply_text: str) -> str:
    """Validates `reply_text` against rule_validator.validate_narrative();
    if it's already valid, returns it unchanged with no repair attempt.

    If invalid and GUARD_ENABLED is false, returns the text as-is (logged as
    a warning) — the check still ran, but repair is disabled, so the
    unrepaired text goes through rather than triggering an LLM call.

    If invalid and GUARD_ENABLED is true, retries via run_repair() up to
    MAX_REPAIR_ATTEMPTS times, re-validating after every attempt. If still
    invalid once attempts are exhausted, fails closed to
    spoiler_policy.NEUTRAL_FALLBACK_TEXT (shared with the spoiler-leak
    fallback — see its definition) rather than returning the last (still
    invalid) repair attempt — see docs/specs/enhancement-guard-agent.md §1
    for the bug this replaced: the previous loop exited without a final
    check and could silently send content still flagged as a system leak or
    broken Markdown."""
    is_valid, error_reason = rule_validator.validate_narrative(reply_text)
    if is_valid:
        return reply_text

    if not config.GUARD_ENABLED:
        _logger.warning(
            f"Narrative validation failed: {error_reason}. "
            "Guard Agent disabled (GUARD_ENABLED=false) — sending unrepaired text."
        )
        observability.event("guard.disabled", level=logging.WARNING, reason=error_reason)
        return reply_text

    attempts = 0
    while attempts < MAX_REPAIR_ATTEMPTS and not is_valid:
        _logger.warning(
            f"Narrative validation failed: {error_reason}. Triggering Guard Agent (Attempt {attempts + 1})."
        )
        reply_text = await run_repair(message, reply_text, error_reason)
        attempts += 1
        is_valid, error_reason = rule_validator.validate_narrative(reply_text)

    if not is_valid:
        _logger.error(
            f"Guard Agent repair loop exhausted after {MAX_REPAIR_ATTEMPTS} attempts; "
            f"still invalid: {error_reason}. Falling back to neutral text."
        )
        observability.event("guard.repair_exhausted", level=logging.ERROR, reason=error_reason)
        return spoiler_policy.NEUTRAL_FALLBACK_TEXT

    return reply_text
