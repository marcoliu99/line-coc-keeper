"""When a retrieved scenario chunk visibly continues into its neighbour, bring the neighbour along.

A chunk is a ~400-character slice of a page, so an action and its scripted consequence can land in
two chunks ("the reception desk has a bell" / "If the bell is rung, the clerk comes out"). Retrieval
scores chunks one at a time, and the second may not share a word with the player's action. This
module decides, from the text alone, whether a hit needs its neighbour. It adds at most the
configured number of chunks per side, never across more than a page boundary, and never because a
neighbour merely exists: a self-contained hit stays as it is.
"""
from __future__ import annotations

import re
from collections.abc import Collection

# A sentence that opens with a condition or a trigger describes what happens when the action is taken.
_CONSEQUENCE = re.compile(
    r"^\s*(?:[（(「『\"']\s*)?(?:若是|若|如果|假如|倘若|如|當|一旦|只要|每當|凡是|"
    r"if\b|when\b|whenever\b|once\b|should\b|upon\b|after\b|as soon as\b)",
    re.IGNORECASE,
)
# The chunker repeats at most this many characters of one chunk at the start of the next (scenario_rag).
_MAX_OVERLAP = 120
# A chunk that does not end a sentence was cut in the middle of one.
_SENTENCE_END = tuple("。！？!?.」』）)\"”’…;；:：")
# A chunk that opens with these continues the sentence before it.
_CONTINUATION = re.compile(r"^\s*(?:[，、；,;]|[a-z]|then\b|and\b|but\b|否則|則|並|而且|但|然後|此時)")


def new_text(previous: str, following: str) -> str:
    """``following`` without the tail of ``previous`` that the chunker repeats at its start.

    The repeated tail can itself contain a blank line, so it is found as the longest suffix of ``previous`` that
    ``following`` begins with and that is followed by the chunker's own paragraph break.
    """
    for size in range(min(len(previous), len(following), _MAX_OVERLAP), 0, -1):
        if following[size:size + 2] == "\n\n" and previous.endswith(following[:size]):
            return following[size + 2:].strip()
    return following.strip()


def ends_open(text: str) -> bool:
    stripped = text.rstrip()
    return bool(stripped) and not stripped.endswith(_SENTENCE_END)


def starts_with_consequence(text: str) -> bool:
    return bool(_CONSEQUENCE.match(text))


def starts_as_continuation(text: str) -> bool:
    return bool(_CONTINUATION.match(text))


def reason_for_next(chunk: str, following: str, shared_terms: Collection[str], before: str = "") -> str:
    """Why ``following`` belongs with a hit on ``chunk``, or "" when it does not.

    ``before`` is the chunk ahead of the hit. A hit that itself opens with a condition is a rule on
    its own, so the next condition is a different rule and does not come along.
    """
    if not shared_terms:
        return ""
    if ends_open(chunk):
        return "hit_ends_mid_sentence"
    if starts_with_consequence(new_text(before, chunk)):
        return ""
    if starts_with_consequence(new_text(chunk, following)):
        return "next_opens_with_consequence"
    return ""


def reason_for_previous(previous: str, chunk: str, shared_terms: Collection[str]) -> str:
    """Why ``previous`` belongs with a hit on ``chunk``, or "" when it does not."""
    if not shared_terms:
        return ""
    own = new_text(previous, chunk)
    if starts_as_continuation(own):
        return "hit_continues_previous"
    if starts_with_consequence(own):
        return "hit_opens_with_consequence"
    return ""
