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

from app import observability
from app.config import GEMINI_API_KEY, GEMINI_MODEL, KEEPER_TEMPERATURE, LOG_SLOW_OPERATION_MS


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
        temperature=KEEPER_TEMPERATURE,
    )

    contents: list = []
    for entry in history:
        role = "model" if entry["role"] == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=entry["content"])]))
    contents.append(types.Content(role="user", parts=[types.Part(text=new_message)]))

    final_text = "（守密人一時語塞，請再說一次剛才的行動）"
    for iteration in range(max_iterations):
        with observability.span(
            "llm.request",
            provider="gemini",
            model=GEMINI_MODEL,
            iteration=iteration,
            tool_count=len(function_declarations),
            slow_threshold_ms=LOG_SLOW_OPERATION_MS,
        ):
            response = client.models.generate_content(model=GEMINI_MODEL, contents=contents, config=config)
        usage = getattr(response, "usage_metadata", None)
        observability.event(
            "llm.usage",
            provider="gemini",
            model=GEMINI_MODEL,
            input_tokens=getattr(usage, "prompt_token_count", None),
            cached_input_tokens=getattr(usage, "cached_content_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
            reasoning_tokens=getattr(usage, "thoughts_token_count", None),
        )
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


def analyze_image(png_bytes: bytes, tool: dict, prompt_text: str) -> dict | None:
    """Vision + a single forced tool call — used by app/scene_map.py's
    analyze_page_image, not the Keeper conversation loop above. Forces the
    one tool via ToolConfig(function_calling_config=FunctionCallingConfig(
    mode="ANY", allowed_function_names=[...])) — same "not exercised against
    a live key" caveat as the rest of this module applies here. Returns the
    tool call's args dict, or None on any failure (no GEMINI_API_KEY, the
    call raised, or no matching function call came back)."""
    if not GEMINI_API_KEY:
        return None
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        function_declaration = types.FunctionDeclaration(
            name=tool["name"], description=tool["description"], parameters_json_schema=tool["input_schema"]
        )
        config = types.GenerateContentConfig(
            tools=[types.Tool(function_declarations=[function_declaration])],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="ANY", allowed_function_names=[tool["name"]])
            ),
        )
        contents = [types.Content(role="user", parts=[
            types.Part.from_bytes(data=png_bytes, mime_type="image/png"),
            types.Part(text=prompt_text),
        ])]
        response = client.models.generate_content(model=GEMINI_MODEL, contents=contents, config=config)
        for fc in response.function_calls or []:
            if fc.name == tool["name"]:
                return dict(fc.args or {})
        return None
    except Exception:
        return None


def analyze_text(text: str, tool: dict, prompt_text: str) -> dict | None:
    """Text-only sibling of analyze_image above — a single forced tool call,
    no image. Used by app/pregen_extractor.py. Returns the tool call's args
    dict, or None on any failure (no GEMINI_API_KEY, the call raised, or no
    matching function call came back)."""
    if not GEMINI_API_KEY:
        return None
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        function_declaration = types.FunctionDeclaration(
            name=tool["name"], description=tool["description"], parameters_json_schema=tool["input_schema"]
        )
        config = types.GenerateContentConfig(
            tools=[types.Tool(function_declarations=[function_declaration])],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="ANY", allowed_function_names=[tool["name"]])
            ),
        )
        contents = [types.Content(role="user", parts=[types.Part(text=f"{prompt_text}\n\n{text}")])]
        response = client.models.generate_content(model=GEMINI_MODEL, contents=contents, config=config)
        for fc in response.function_calls or []:
            if fc.name == tool["name"]:
                return dict(fc.args or {})
        return None
    except Exception:
        return None
