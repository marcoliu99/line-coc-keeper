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

from app.config import OPENAI_API_KEY, OPENAI_MODEL


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

    final_text = "（守密人一時語塞，請再說一次剛才的行動）"
    for _ in range(max_iterations):
        response = client.responses.create(
            model=OPENAI_MODEL,
            instructions=instructions,
            input=input_items,
            tools=openai_tools,
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
