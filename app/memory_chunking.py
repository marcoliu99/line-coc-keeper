"""Bound a memory chunk to what one embedding input can hold, without losing or reordering any of it.

A trim of the conversation log used to become one memory chunk however long it was, and one embedding request:
a chunk of ~19,000 tokens is rejected by an embedding model that accepts ~8,000, so the chunk was stored without a
vector and retrieval quietly stayed lexical. Here the dropped messages are packed, in order, into parts that each
fit a configured token budget (measured, not guessed from characters); a message too long for one part is split at
sentence boundaries, then by length. Every part keeps the provenance of the messages it holds.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

Measure = Callable[[str], int]
_SENTENCE_END = re.compile(r"(?<=[。！？!?；;])|(?<=[.])\s+|(?<=\n)")


@dataclass(frozen=True)
class MemoryPart:
    text: str
    source_messages: tuple[dict[str, str], ...] = field(default_factory=tuple)


def split_text(text: str, max_tokens: int, measure: Measure) -> list[str]:
    """Pieces of ``text`` that each measure at most ``max_tokens`` and concatenate back to ``text``."""
    if max_tokens < 1:
        raise ValueError("max_tokens must be positive")
    if measure(text) <= max_tokens:
        return [text]
    pieces = [piece for piece in _SENTENCE_END.split(text) if piece]
    if len(pieces) <= 1:
        # No sentence boundary to use: cut by length, sized from the measured density of the text itself.
        window = max(1, int(len(text) * max_tokens / max(1, measure(text))))
        pieces = [text[i:i + window] for i in range(0, len(text), window)]
    parts: list[str] = []
    current = ""
    for piece in pieces:
        if measure(piece) > max_tokens:
            if current:
                parts.append(current)
                current = ""
            half = max(1, len(piece) // 2)
            parts.extend(split_text(piece[:half], max_tokens, measure))
            parts.extend(split_text(piece[half:], max_tokens, measure))
        elif current and measure(current + piece) > max_tokens:
            parts.append(current)
            current = piece
        else:
            current += piece
    if current:
        parts.append(current)
    return parts


def pack(
    messages: Sequence[dict[str, str]], source_messages: Sequence[dict[str, str]],
    *, max_tokens: int, measure: Measure,
) -> list[MemoryPart]:
    """Pack ``role: content`` lines, in order, into parts of at most ``max_tokens``.

    ``source_messages[i]`` describes ``messages[i]``. Joined with newlines the parts read back as the same text,
    except that a message split across parts is cut where a sentence ends.
    """
    lines = [f"{m['role']}: {m['content']}" for m in messages]
    parts: list[MemoryPart] = []
    current: list[str] = []
    current_sources: list[dict[str, str]] = []

    def flush() -> None:
        if current:
            parts.append(MemoryPart("\n".join(current), tuple(current_sources)))
            current.clear()
            current_sources.clear()

    for index, line in enumerate(lines):
        source = source_messages[index] if index < len(source_messages) else {}
        if measure(line) > max_tokens:
            flush()
            parts.extend(MemoryPart(piece, (source,) if source else ()) for piece in split_text(line, max_tokens, measure))
        elif current and measure("\n".join([*current, line])) > max_tokens:
            flush()
            current.append(line)
            if source:
                current_sources.append(source)
        else:
            current.append(line)
            if source:
                current_sources.append(source)
    flush()
    return parts
