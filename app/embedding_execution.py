"""Complete ordered embedding calls shared by scenario and memory retrieval."""
from __future__ import annotations

import logging
from typing import cast

from app import observability


def embed_texts(
    texts: list[str], *, rag_kind: str, api_key: str, model: str,
    timeout: float, batch_size: int = 100,
) -> list[list[float]] | None:
    if not texts:
        return None
    if not api_key:
        _fallback(rag_kind, model, 'missing_api_key')
        return None
    client = None
    try:
        import openai

        client = openai.OpenAI(api_key=api_key, timeout=timeout, max_retries=0)
        ordered: list[list[float] | None] = [None] * len(texts)
        batch_count = (len(texts) + batch_size - 1) // batch_size
        for batch_index, start in enumerate(range(0, len(texts), batch_size)):
            batch = texts[start:start + batch_size]
            with observability.span('embedding.batch', embedding_model=model,
                                    batch_size=len(batch), batch_index=batch_index,
                                    batch_count=batch_count):
                response = client.embeddings.create(model=model, input=batch)
            seen: set[int] = set()
            for item in response.data:
                index = item.index
                if not isinstance(index, int) or not 0 <= index < len(batch) or index in seen:
                    _fallback(rag_kind, model, 'incomplete_embedding_response')
                    return None
                seen.add(index)
                ordered[start + index] = item.embedding
            if len(seen) != len(batch):
                _fallback(rag_kind, model, 'incomplete_embedding_response')
                return None
        if any(vector is None for vector in ordered):
            _fallback(rag_kind, model, 'incomplete_embedding_response')
            return None
        return cast(list[list[float]], ordered)
    except Exception:  # noqa: BLE001 - lexical search remains available.
        _fallback(rag_kind, model, 'embedding_error')
        return None
    finally:
        if client is not None:
            close = getattr(client, 'close', None)
            if callable(close):
                try:
                    close()
                except Exception as exc:  # noqa: BLE001 - cleanup cannot mask fallback.
                    observability.event('rag.embedding_client_close_failed',
                                        level=logging.WARNING, rag_kind=rag_kind,
                                        error_type=type(exc).__name__)


def _fallback(rag_kind: str, model: str, reason: str) -> None:
    observability.event('rag.embedding_fallback', level=logging.WARNING, rag_kind=rag_kind,
                        embedding_model=model, fallback='bm25', error_type=reason)
