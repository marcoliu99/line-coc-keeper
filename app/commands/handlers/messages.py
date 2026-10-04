"""Plain-message commands that are not ``/coc``: ``/roll`` and the reply to an unsupported message type.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app import (
    dice,
    locks,
)
from app.repositories.group_state import load_state

if TYPE_CHECKING:
    from app.commands.types import Reply


async def handle_unsupported_message(conversation_id: str, reply: Reply, label: str) -> None:
    """Called by an adapter when it receives a message type it can't hand text
    or a PDF from (sticker, image, voice, etc.) — only speaks up once a game is
    actually active, to avoid being noisy in unrelated chat."""
    async with locks.get_conversation_lock(conversation_id):
        state = load_state(conversation_id)
        active = state.active
    if active:
        await reply(
            f"（守密人目前只讀得懂文字訊息、PDF 劇本和 scenario 開頭的 Markdown 劇本，收到的{label}不會被處理；"
            "如果裡面有重要內容，麻煩用文字描述一下發生了什麼事。）"
        )


async def handle_roll_command(reply: Reply, text: str) -> None:
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await reply("用法：/roll 1d100 或 /roll 3d6+2")
        return
    try:
        result = dice.roll_expression(parts[1].strip())
    except ValueError as exc:
        await reply(str(exc))
        return
    await reply(f"🎲 {result.describe()}")
