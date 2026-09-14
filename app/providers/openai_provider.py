"""OpenAI provider adapter — uses the **Responses API**
(`client.responses.create(...)`), not the older Chat Completions API.

This project originally used Chat Completions (matching markitdown-ocr's
hard-coded shape — see app/markitdown_shim.py, which still targets that on
purpose). But OpenAI's own current Python library docs
(https://developers.openai.com/api/docs/libraries?language=python — checked
directly, not assumed from memory) show `client.responses.create(model=...,
input=...)` as the recommended call for new integrations, so the Keeper path
was rewritten to match. The exact field names below (FunctionToolParam's
flat {type, name, description, parameters} shape, ResponseFunctionToolCall's
{call_id, name, arguments}, FunctionCallOutput's {type: "function_call_output",
call_id, output}) were confirmed against this project's installed `openai`
SDK's actual type stubs (openai/types/responses/*.py), not guessed from the
docs page alone.
"""
from __future__ import annotations

import json
from typing import Callable

from app.config import KEEPER_REASONING_EFFORT, KEEPER_TEMPERATURE, OPENAI_API_KEY, OPENAI_MODEL

# Populated per-process the first time the API rejects one of these — see
# _create_response.
_unsupported_params: set[str] = set()


def _create_response(client, **kwargs):
    """Some model tiers (reasoning-focused releases in particular) reject
    certain optional parameters outright with a 400 instead of silently
    ignoring them — confirmed for `temperature`; `reasoning` is the same
    class of per-model-support risk. This project stays model-agnostic on
    purpose (OPENAI_MODEL is whatever's configured in .env, not hardcoded
    here — see that setting's own caveat), so rather than hardcoding which
    models support what, detect a rejection once per process and stop
    sending that specific parameter for the rest of this run. `while True`
    terminates naturally: each pass either returns, raises (the failure
    wasn't one of the two tracked params), or removes one of at most two
    trackable params from kwargs — so within 3 attempts it's either
    succeeded or is raising for an unrelated reason."""
    for param in _unsupported_params:
        kwargs.pop(param, None)
    while True:
        try:
            return client.responses.create(**kwargs)
        except Exception as exc:
            exc_text = str(exc).lower()
            offending = next((p for p in ("temperature", "reasoning") if p in kwargs and p in exc_text), None)
            if offending is None:
                raise
            _unsupported_params.add(offending)
            kwargs.pop(offending, None)


def run_conversation(
    static_system: str,
    dynamic_system: str,
    tools: list[dict],
    history: list[dict],
    new_message: str,
    execute_tool: Callable[[str, dict], dict],
    max_iterations: int,
) -> str:
    if not OPENAI_API_KEY:
        return "（尚未設定 OPENAI_API_KEY，守密人無法回應，請管理員檢查 .env 設定）"

    import openai

    client = openai.OpenAI(api_key=OPENAI_API_KEY)

    # Responses API tools are flat (no nested "function" wrapper, unlike Chat
    # Completions) — see FunctionToolParam in the SDK's type stubs.
    openai_tools = [
        {
            "type": "function",
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        }
        for t in tools
    ]

    # instructions is the Responses API's dedicated system-prompt field —
    # unlike the Chat Completions provider, this doesn't need a "system" role
    # message mixed into the input list.
    instructions = f"{static_system}\n\n{dynamic_system}"

    input_items: list[dict] = [{"role": entry["role"], "content": entry["content"]} for entry in history]
    input_items.append({"role": "user", "content": new_message})

    # Omitted entirely (not sent as an empty/None value) when
    # KEEPER_REASONING_EFFORT="" — that's the escape hatch back to the old
    # "don't touch this parameter at all" behavior for anyone who wants it.
    reasoning_kwargs = {"reasoning": {"effort": KEEPER_REASONING_EFFORT}} if KEEPER_REASONING_EFFORT else {}

    final_text = "（守密人一時語塞，請再說一次剛才的行動）"
    for _ in range(max_iterations):
        response = _create_response(
            client,
            model=OPENAI_MODEL,
            instructions=instructions,
            input=input_items,
            tools=openai_tools,
            temperature=KEEPER_TEMPERATURE,
            **reasoning_kwargs,
        )

        function_calls = [item for item in response.output if item.type == "function_call"]

        # Echo the model's own output back into the next call's input — explicit
        # per-item-type conversion (rather than passing the raw response.output
        # pydantic objects straight through) so this stays plain-dict JSON and
        # easy to test against a fake response object.
        for item in response.output:
            if item.type == "function_call":
                input_items.append({
                    "type": "function_call",
                    "call_id": item.call_id,
                    "name": item.name,
                    "arguments": item.arguments,
                })
            elif item.type == "message":
                text = "".join(c.text for c in item.content if getattr(c, "type", None) == "output_text")
                if text:
                    input_items.append({"role": "assistant", "content": text})
            # Other item types (reasoning, etc.) are intentionally dropped —
            # not needed for this project's tool-calling loop.

        if not function_calls:
            final_text = (response.output_text or "").strip() or final_text
            break

        for fc in function_calls:
            args = json.loads(fc.arguments or "{}")
            result = execute_tool(fc.name, args)
            input_items.append({
                "type": "function_call_output",
                "call_id": fc.call_id,
                "output": json.dumps(result, ensure_ascii=False),
            })

    return final_text


def analyze_image(png_bytes: bytes, tool: dict, prompt_text: str) -> dict | None:
    """Vision + a single forced tool call via the Responses API — used by
    app/scene_map.py's analyze_page_image, not the Keeper conversation loop
    above. Image input uses the {"type": "input_image", "image_url": <data
    URL>} shape (ResponseInputImageContentParam) and tool_choice forces the
    one tool by name (ToolChoiceFunctionParam) — both confirmed against this
    project's installed `openai` SDK type stubs. Returns the tool call's
    parsed arguments dict, or None on any failure (no OPENAI_API_KEY, the
    call raised, or no matching function_call came back)."""
    if not OPENAI_API_KEY:
        return None
    try:
        import base64

        import openai

        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        image_b64 = base64.standard_b64encode(png_bytes).decode("utf-8")
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt_text},
                    {"type": "input_image", "image_url": f"data:image/png;base64,{image_b64}"},
                ],
            }],
            tools=[{
                "type": "function",
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            }],
            tool_choice={"type": "function", "name": tool["name"]},
        )
        for item in response.output:
            if item.type == "function_call" and item.name == tool["name"]:
                return json.loads(item.arguments or "{}")
        return None
    except Exception:
        return None


def analyze_text(text: str, tool: dict, prompt_text: str) -> dict | None:
    """Text-only sibling of analyze_image above — a single forced tool call,
    no image. Used by app/pregen_extractor.py. Returns the tool call's parsed
    arguments dict, or None on any failure (no OPENAI_API_KEY, the call
    raised, or no matching function_call came back)."""
    if not OPENAI_API_KEY:
        return None
    try:
        import openai

        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=[{"role": "user", "content": f"{prompt_text}\n\n{text}"}],
            tools=[{
                "type": "function",
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            }],
            tool_choice={"type": "function", "name": tool["name"]},
        )
        for item in response.output:
            if item.type == "function_call" and item.name == tool["name"]:
                return json.loads(item.arguments or "{}")
        return None
    except Exception:
        return None
