"""Callback and value types the command layer passes around.

The adapters (Discord) supply plain callbacks; handlers and services take them
as arguments and never import the transport.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal

Reply = Callable[[str], Awaitable[None]]
GetDisplayName = Callable[[], Awaitable[str]]
# owner_id -> a Discord mention rendered by the adapter.
FormatMention = Callable[[str], str]
SendDM = Callable[[str, str], Awaitable[None]]  # (owner_id, text) -> None
# (png_bytes, conversation_id, page_number) -> None, posts publicly. The
# conversation and page metadata are retained for state-aware image sends.
SendImage = Callable[[bytes, str, int], Awaitable[None]]
SendDMImage = Callable[[str, bytes, str, int], Awaitable[None]]  # (owner_id, png_bytes, conversation_id, page_number)
# "new": the upload starts a fresh scenario; "fix": it corrects the current one.
PdfChoice = Literal["new", "fix"]
