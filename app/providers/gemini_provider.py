"""Google Gemini provider adapter (manual function-calling loop via google-genai).

Built from Google's published `google-genai` SDK docs (client.models.generate_content,
FunctionDeclaration/Tool, manual function-calling loop). Not exercised against a
live Gemini API key during development — the Gen AI SDK surface has moved fast, so
if this misbehaves, checking the exact attribute names against the current
`google-genai` docs/changelog before assuming the game logic is at fault is the
first debugging step.
"""
from __future__ import annotations

from typing import Callable

from app.config import GEMINI_API_KEY, GEMINI_MODEL


def run_conversation(
    static_system: str,
    dynamic_system: str,
    tools: list[dict],
    history: list[dict],
    new_message: str,
    execute_tool: Callable[[str, dict], dict],
    max_iterations: int,
) -> str:
    if not GEMINI_API_KEY:
        return "（尚未設定 GEMINI_API_KEY，守密人無法回應，請管理員檢查 .env 設定）"

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)

    function_declarations = [
        types.FunctionDeclaration(
            name=t["name"],
            description=t["description"],
            parameters_json_schema=t["input_schema"],
        )
        for t in tools
    ]
    gemini_tools = [types.Tool(function_declarations=function_declarations)]
    config = types.GenerateContentConfig(
        system_instruction=f"{static_system}\n\n{dynamic_system}",
        tools=gemini_tools,
    )

    contents: list = []
    for entry in history:
        role = "model" if entry["role"] == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=entry["content"])]))
    contents.append(types.Content(role="user", parts=[types.Part(text=new_message)]))

    final_text = "（守密人一時語塞，請再說一次剛才的行動）"
    for _ in range(max_iterations):
        response = client.models.generate_content(model=GEMINI_MODEL, contents=contents, config=config)
        candidate = response.candidates[0]
        contents.append(candidate.content)

        function_calls = response.function_calls or []
        if not function_calls:
            final_text = (response.text or "").strip() or final_text
            break

        response_parts = []
        for fc in function_calls:
            result = execute_tool(fc.name, dict(fc.args or {}))
            response_parts.append(types.Part.from_function_response(name=fc.name, response=result))
        contents.append(types.Content(role="user", parts=response_parts))

    return final_text
