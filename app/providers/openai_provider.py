"""OpenAI (Chat Completions API) provider adapter.

Built from the `openai` Python SDK's standard Chat Completions tool-calling
loop (`client.chat.completions.create(..., tools=[{"type": "function", ...}])`,
reading `message.tool_calls` back off the response) — the same interface
`app/markitdown_shim.py` already targets for markitdown-ocr, just used here
directly instead of through a shim, since this *is* real OpenAI. Not
exercised against a live API key during development (same caveat as
app/providers/gemini_provider.py): if this misbehaves, check the exact
OPENAI_MODEL string and the current `openai` SDK's tool-calling shape before
assuming the game logic is at fault.
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

    openai_tools = [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]

    messages: list[dict] = [{"role": "system", "content": f"{static_system}\n\n{dynamic_system}"}]
    for entry in history:
        messages.append({"role": entry["role"], "content": entry["content"]})
    messages.append({"role": "user", "content": new_message})

    final_text = "（守密人一時語塞，請再說一次剛才的行動）"
    for _ in range(max_iterations):
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            tools=openai_tools,
        )
        message = response.choices[0].message

        assistant_entry: dict = {"role": "assistant", "content": message.content}
        if message.tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in message.tool_calls
            ]
        messages.append(assistant_entry)

        if not message.tool_calls:
            final_text = (message.content or "").strip() or final_text
            break

        for tc in message.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            result = execute_tool(tc.function.name, args)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False),
            })

    return final_text
