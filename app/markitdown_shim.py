"""Wires Microsoft's MarkItDown (+ the markitdown-ocr plugin) into the PDF
pipeline.

MarkItDown's own LLM-vision hooks — both the core library's image captioning
(markitdown/converters/_llm_caption.py) and the markitdown-ocr plugin's
LLMVisionOCRService (markitdown_ocr/_ocr_service.py) — are hard-coded to call
`client.chat.completions.create(model=..., messages=[...])`, i.e. the OpenAI
SDK's Chat Completions shape (confirmed by reading both source files directly,
not just the docs). This is exactly `openai.OpenAI()`'s native interface, so
when LLM_PROVIDER=openai this uses a real OpenAI client directly, no
translation needed. For the other two providers, a small shim presents that
same call shape backed by anthropic.Anthropic() or google-genai instead, so
this still works without an OpenAI account. Neither shim attempts to support
any other OpenAI Chat Completions feature (tools, streaming, etc.) —
MarkItDown's own callers only ever use this one text+image-in, text-out
shape.

Which provider/key gets used here now follows LLM_PROVIDER directly — same
dispatch this project's other vision fallback (app/scene_map.py's
analyze_page_image, via each app/providers/*.py's analyze_image) already
uses — instead of an independent "prefer OpenAI, fall back to Anthropic"
priority order that ignored LLM_PROVIDER entirely. That older order meant a
group running LLM_PROVIDER=anthropic but with an OPENAI_API_KEY also present
(e.g. for embeddings — see app/scenario_rag.py) would silently have its
embedded-image OCR calls billed to OpenAI while the rest of the game ran on
Claude, two different providers active for no visible reason. Now: no key
configured for whichever provider is actually selected means this returns
None (same as before — callers fall back to the existing PyMuPDF-only
pipeline), rather than reaching for a different provider's key just because
it happens to be present.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    LLM_PROVIDER,
    OPENAI_API_KEY,
    OPENAI_MODEL,
)

_VISION_MAX_TOKENS = 4096  # matches app/pdf_loader.py's own vision calls — see
# that module's comment on why 1024 was once observed silently swallowed
# whole by extended thinking on claude-sonnet-5, returning no text at all.


class _AnthropicChatCompletions:
    def __init__(self, client: Any, model_default: str) -> None:
        self._client = client
        self._model_default = model_default

    def create(self, model: str | None = None, messages: list[dict] | None = None, **_ignored: Any) -> Any:
        content: list[dict] = []
        for part in (messages or [{}])[0].get("content", []):
            if part.get("type") == "text":
                content.append({"type": "text", "text": part["text"]})
            elif part.get("type") == "image_url":
                data_uri = part["image_url"]["url"]
                header, b64data = data_uri.split(",", 1)
                media_type = header.split(";")[0].split(":", 1)[1]
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": b64data},
                })

        response = self._client.messages.create(
            model=model or self._model_default,
            max_tokens=_VISION_MAX_TOKENS,
            messages=[{"role": "user", "content": content}],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        message = SimpleNamespace(content=text)
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice])


def _build_anthropic_openai_shim(model_default: str) -> Any:
    """An object shaped like `openai.OpenAI()` just enough for MarkItDown's
    `client.chat.completions.create(...)` calls to work, backed by
    anthropic.Anthropic() instead."""
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    completions = _AnthropicChatCompletions(client, model_default)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


class _GeminiChatCompletions:
    def __init__(self, client: Any, model_default: str) -> None:
        self._client = client
        self._model_default = model_default

    def create(self, model: str | None = None, messages: list[dict] | None = None, **_ignored: Any) -> Any:
        import base64

        from google.genai import types

        parts = []
        for part in (messages or [{}])[0].get("content", []):
            if part.get("type") == "text":
                parts.append(types.Part(text=part["text"]))
            elif part.get("type") == "image_url":
                data_uri = part["image_url"]["url"]
                header, b64data = data_uri.split(",", 1)
                media_type = header.split(";")[0].split(":", 1)[1]
                parts.append(types.Part.from_bytes(data=base64.b64decode(b64data), mime_type=media_type))

        response = self._client.models.generate_content(
            model=model or self._model_default,
            contents=[types.Content(role="user", parts=parts)],
        )
        text = response.text or ""
        message = SimpleNamespace(content=text)
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice])


def _build_gemini_openai_shim(model_default: str) -> Any:
    """Same idea as _build_anthropic_openai_shim above, backed by
    google-genai (see app/providers/gemini_provider.py — same SDK, same
    "not exercised against a live key" caveat applies to this shim too)."""
    from google import genai

    client = genai.Client(api_key=GEMINI_API_KEY)
    completions = _GeminiChatCompletions(client, model_default)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def build_markitdown(vision_prompt: str):
    """Returns a configured MarkItDown instance (core PDF/office converters
    overridden by markitdown-ocr's OCR-enhanced ones, using our vision prompt
    for embedded-image description), or None if unavailable — LLM_PROVIDER's
    key isn't set, or markitdown/markitdown-ocr aren't installed. Callers
    should treat None the same as a failed conversion: fall back to the
    existing PyMuPDF-only pipeline (see app/pdf_loader.py).

    Follows LLM_PROVIDER exactly (see this module's docstring for why) —
    OpenAI gets a real native client, Anthropic/Gemini get a small shim
    presenting the same chat.completions.create(...) shape MarkItDown's
    vision hooks are hard-coded to call."""
    try:
        from markitdown import MarkItDown
    except ImportError:
        return None

    if LLM_PROVIDER == "openai" and OPENAI_API_KEY:
        import openai

        llm_client = openai.OpenAI(api_key=OPENAI_API_KEY)
        llm_model = OPENAI_MODEL
    elif LLM_PROVIDER == "anthropic" and ANTHROPIC_API_KEY:
        llm_client = _build_anthropic_openai_shim(ANTHROPIC_MODEL)
        llm_model = ANTHROPIC_MODEL
    elif LLM_PROVIDER == "gemini" and GEMINI_API_KEY:
        llm_client = _build_gemini_openai_shim(GEMINI_MODEL)
        llm_model = GEMINI_MODEL
    else:
        return None

    return MarkItDown(
        enable_plugins=True,
        llm_client=llm_client,
        llm_model=llm_model,
        llm_prompt=vision_prompt,
    )
