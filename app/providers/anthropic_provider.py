"""Claude (Anthropic Messages API) provider adapter."""
from __future__ import annotations

import json
from collections.abc import Callable

from app import observability
from app.config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    KEEPER_TEMPERATURE,
    LOG_INCLUDE_USAGE,
    LOG_SLOW_OPERATION_MS,
)
from app.providers import retry


def run_conversation(
    static_system: str,
    dynamic_system: str,
    tools: list[dict],
    history: list[dict],
    new_message: str,
    execute_tool: Callable[[str, dict], dict],
    max_iterations: int,
) -> str:
    if not ANTHROPIC_API_KEY:
        return "（尚未設定 ANTHROPIC_API_KEY，守密人無法回應，請管理員檢查 .env 設定）"

    import anthropic

    # max_retries=0: the SDK itself defaults to retrying twice on connection
    # errors/retryable status codes — stacked on top of retry.call_with_retry
    # below, a persistent outage would make up to 3x the intended number of
    # HTTP attempts (each with its own SDK-internal backoff) instead of the
    # single LLM_MAX_RETRIES-bounded budget documented in app/config.py. Our
    # retry layer is the single source of truth for this client; the SDK's
    # own retry logic is disabled, not layered.
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, max_retries=0)

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
        with observability.span(
            "llm.request",
            provider="anthropic",
            model=ANTHROPIC_MODEL,
            iteration=iteration,
            tool_count=len(anthropic_tools),
            slow_threshold_ms=LOG_SLOW_OPERATION_MS,
            metrics=request_metrics,
        ):
            response = retry.call_with_retry(
                lambda: client.messages.create(
                    model=ANTHROPIC_MODEL,
                    max_tokens=1024,
                    temperature=KEEPER_TEMPERATURE,
                    system=system_blocks,
                    tools=anthropic_tools,
                    messages=messages,
                ),
                provider="anthropic",
                operation="messages.create",
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
            result = execute_tool(tu.name, tu.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": json.dumps(result, ensure_ascii=False),
            })
        messages.append({"role": "user", "content": tool_results})

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
            response = client.messages.create(
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
            response = client.messages.create(
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
