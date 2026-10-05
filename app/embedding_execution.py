"""Complete ordered embedding calls shared by scenario and memory retrieval.

A failed call returns ``None`` so retrieval stays lexical, and leaves a diagnosis of why (``take_failure``)
that never contains a credential, a request body or the provider's message text.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import cast

from app import observability

PROVIDER = 'openai'
OPERATION = 'embeddings.create'
_local = threading.local()


@dataclass(frozen=True)
class EmbeddingFailure:
    """Why the last embedding call on this thread failed; safe to log and to store."""
    reason: str
    status_class: str
    retryable: bool
    status_code: int | None = None
    error_code: str = ''
    error_type: str = ''


def take_failure() -> EmbeddingFailure | None:
    """The failure of the most recent ``embed_texts`` call on this thread, once; None after a success."""
    failure = getattr(_local, 'failure', None)
    _local.failure = None
    return cast('EmbeddingFailure | None', failure)


def classify(exc: BaseException) -> EmbeddingFailure:
    """Diagnose a provider exception from its class and structured fields, never its message."""
    name = type(exc).__name__
    status = getattr(exc, 'status_code', None)
    status = status if isinstance(status, int) else None
    code = getattr(exc, 'code', '')
    kind = getattr(exc, 'type', '')
    code = code if isinstance(code, str) else ''
    kind = kind if isinstance(kind, str) else ''
    if status is not None:
        status_class = f'{status // 100}xx'
        # 408/409/429 and every 5xx can succeed later; any other 4xx will be refused again for the same input.
        retryable = status in {408, 409, 429} or status >= 500
        if status == 429 and code == 'insufficient_quota':
            retryable = False
    elif 'Timeout' in name:
        status_class, retryable = 'timeout', True
    elif 'Connection' in name or isinstance(exc, (OSError, TimeoutError)):
        status_class, retryable = 'connection', True
    else:
        status_class, retryable = 'unknown', False
    return EmbeddingFailure('embedding_error', status_class, retryable, status, code[:64], (kind or name)[:64])


def _measure(texts: list[str]) -> tuple[int, int]:
    """Total and largest input of the request in UTF-8 bytes: an upper bound on its tokens, free to compute."""
    sizes = [len(text.encode('utf-8')) for text in texts]
    return sum(sizes), max(sizes, default=0)


def embed_texts(
    texts: list[str], *, rag_kind: str, api_key: str, model: str,
    timeout: float, batch_size: int = 100,
) -> list[list[float]] | None:
    _local.failure = None
    if not texts:
        return None
    if not api_key:
        _fallback(rag_kind, model, EmbeddingFailure('missing_api_key', 'none', False), texts)
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
                    _fallback(rag_kind, model, EmbeddingFailure('incomplete_embedding_response', 'none', True), texts)
                    return None
                seen.add(index)
                ordered[start + index] = item.embedding
            if len(seen) != len(batch):
                _fallback(rag_kind, model, EmbeddingFailure('incomplete_embedding_response', 'none', True), texts)
                return None
        if any(vector is None for vector in ordered):
            _fallback(rag_kind, model, EmbeddingFailure('incomplete_embedding_response', 'none', True), texts)
            return None
        return cast(list[list[float]], ordered)
    except Exception as exc:  # noqa: BLE001 - lexical search remains available.
        _fallback(rag_kind, model, classify(exc), texts)
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


def _fallback(rag_kind: str, model: str, failure: EmbeddingFailure, texts: list[str]) -> None:
    _local.failure = failure
    total, largest = _measure(texts)
    observability.event(
        'rag.embedding_fallback', level=logging.WARNING, rag_kind=rag_kind, embedding_model=model,
        provider=PROVIDER, operation=OPERATION, fallback='bm25', error_type=failure.reason,
        input_count=len(texts), input_bytes=total, largest_input_bytes=largest,
        status_class=failure.status_class, status_code=failure.status_code,
        provider_error_code=failure.error_code or None, provider_error_type=failure.error_type or None,
        retryable=failure.retryable,
    )
