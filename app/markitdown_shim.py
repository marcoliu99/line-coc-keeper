"""Wires Microsoft's MarkItDown (+ the markitdown-ocr plugin) into the PDF
pipeline.

MarkItDown's own LLM-vision hooks — both the core library's image captioning
(markitdown/converters/_llm_caption.py) and the markitdown-ocr plugin's
LLMVisionOCRService (markitdown_ocr/_ocr_service.py) — are hard-coded to call
`client.chat.completions.create(model=..., messages=[...])`, i.e. the OpenAI
SDK's Chat Completions shape (confirmed by reading both source files directly,
not just the docs). This is exactly `openai.OpenAI()`'s native interface, so
when OPENAI_API_KEY is set (see app/providers/openai_provider.py — the
project is moving toward OpenAI as the Keeper's LLM), build_markitdown uses
a real OpenAI client directly, no translation needed. When it isn't set, this
falls back to `_AnthropicChatCompletions` — a small shim presenting that same
call shape backed by anthropic.Anthropic() instead, so markitdown-ocr still
works on ANTHROPIC_API_KEY alone without requiring an OpenAI account. It does
not attempt to support any other OpenAI Chat Completions feature (tools,
streaming, etc.) — MarkItDown's own callers only ever use this one
text+image-in, text-out shape.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, OPENAI_API_KEY, OPENAI_MODEL

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


def build_markitdown(vision_prompt: str):
    """Returns a configured MarkItDown instance (core PDF/office converters
    overridden by markitdown-ocr's OCR-enhanced ones, using our vision prompt
    for embedded-image description), or None if unavailable — no usable API
    key, or markitdown/markitdown-ocr aren't installed. Callers should treat
    None the same as a failed conversion: fall back to the existing
    PyMuPDF-only pipeline (see app/pdf_loader.py).

    Prefers real OpenAI (native interface, no translation) when
    OPENAI_API_KEY is set; falls back to the Anthropic shim otherwise."""
    try:
        from markitdown import MarkItDown
    except ImportError:
        return None

    if OPENAI_API_KEY:
        import openai

        llm_client = openai.OpenAI(api_key=OPENAI_API_KEY)
        llm_model = OPENAI_MODEL
    elif ANTHROPIC_API_KEY:
        llm_client = _build_anthropic_openai_shim(ANTHROPIC_MODEL)
        llm_model = ANTHROPIC_MODEL
    else:
        return None

    return MarkItDown(
        enable_plugins=True,
        llm_client=llm_client,
        llm_model=llm_model,
        llm_prompt=vision_prompt,
    )
