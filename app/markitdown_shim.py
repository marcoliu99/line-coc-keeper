"""Wires Microsoft's MarkItDown (+ the markitdown-ocr plugin) into the PDF
pipeline using this project's existing Anthropic key, instead of adding a new
OpenAI dependency/API key.

MarkItDown's own LLM-vision hooks — both the core library's image captioning
(markitdown/converters/_llm_caption.py) and the markitdown-ocr plugin's
LLMVisionOCRService (markitdown_ocr/_ocr_service.py) — are hard-coded to call
`client.chat.completions.create(model=..., messages=[...])`, i.e. the OpenAI
SDK's Chat Completions shape (confirmed by reading both source files directly,
not just the docs). This project has stayed Anthropic-only throughout (prompt
caching, vision, scene-map extraction all use ANTHROPIC_API_KEY) and has no
reason to add `openai` as a dependency just to satisfy that interface — so
`_AnthropicChatCompletions` below is a small translation shim: it accepts the
same call shape MarkItDown expects, converts the OpenAI-style `image_url`
data-URI content block into Anthropic's base64 image block, and reshapes
Anthropic's response into an object with the `.choices[0].message.content`
attribute path MarkItDown reads. It does not attempt to support any other
OpenAI Chat Completions feature (tools, streaming, etc.) — MarkItDown's own
callers only ever use this one text+image-in, text-out shape.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL

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
    overridden by markitdown-ocr's OCR-enhanced ones, using our Claude vision
    prompt for embedded-image description), or None if unavailable — no
    ANTHROPIC_API_KEY, or markitdown/markitdown-ocr aren't installed. Callers
    should treat None the same as a failed conversion: fall back to the
    existing PyMuPDF-only pipeline (see app/pdf_loader.py)."""
    if not ANTHROPIC_API_KEY:
        return None
    try:
        from markitdown import MarkItDown
    except ImportError:
        return None

    llm_client = _build_anthropic_openai_shim(ANTHROPIC_MODEL)
    return MarkItDown(
        enable_plugins=True,
        llm_client=llm_client,
        llm_model=ANTHROPIC_MODEL,
        llm_prompt=vision_prompt,
    )
