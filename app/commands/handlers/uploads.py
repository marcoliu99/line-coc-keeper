"""Attachment uploads: which kind of file this is, and who handles it.

Moved out of app/discord_bot.py so the transport only turns a Discord message
into Upload values (docs/specs/refactor/discord_events_through_router_design_spec.md).
Scenario-file upload deliberately has no KP/Host check, even with
SCENARIO_LIFECYCLE_KP_ONLY: any player may upload, and the first upload of a
conversation applies at once. Only the choice buttons and /coc scenario are
KP/Host-only.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app import locks, scenario_library
from app.commands.types import PdfChoice, Reply
from app.legacy_commands import (
    handle_map_upload,
    handle_pdf_upload,
    handle_role_sheet_upload,
    handle_scenario_compare_upload,
    handle_scenario_markdown_upload,
    resolve_pdf_upload_choice,
)
from app.repositories import state_transaction
from app.services import mutation_admission

_PART_NAME = re.compile(r"(?:^|[_ .-])part(?:[_ .-]?\d+)(?:$|[_ .-])", re.IGNORECASE)


@dataclass(frozen=True)
class Upload:
    """One attached file; `read` fetches its bytes only when it is used."""

    filename: str
    read: Callable[[], Awaitable[bytes]]


async def handle_uploads(
    conversation_id: str,
    uploads: list[Upload],
    reply: Reply,
    *,
    post_pdf_buttons: Callable[[], Awaitable[None]],
) -> bool:
    """Handle a message's attachments; True when one of them was handled."""
    pdfs = sorted((u for u in uploads if u.filename.lower().endswith(".pdf")), key=lambda u: u.filename.lower())
    if pdfs:
        if len(pdfs) > 1 or any(_PART_NAME.search(Path(u.filename).stem) for u in pdfs):
            await _stage_pdf_parts(conversation_id, pdfs, reply)
            return True
        # No reply-token/time-window constraint here, so the same callback
        # serves as both the immediate ack and the final result.
        await handle_pdf_upload(conversation_id, reply, reply, await pdfs[0].read(), pdfs[0].filename)
        await post_pdf_buttons()
        return True

    scenario_markdowns = [
        u for u in uploads
        if u.filename.lower().startswith("scenario") and u.filename.lower().endswith(".md")
    ]
    if scenario_markdowns:
        if len(scenario_markdowns) > 1:
            await reply("一次請只上傳一份 scenario 開頭的 Markdown 劇本。")
            return True
        upload = scenario_markdowns[0]
        await handle_scenario_markdown_upload(
            conversation_id, reply, reply, await upload.read(), upload.filename
        )
        await post_pdf_buttons()
        return True

    # Requires the map_ prefix (see docs/character_and_dictionary_system_
    # spec.md's Module 1) — a bare .yaml/.yml attachment is no longer
    # assumed to be a map on extension alone.
    maps = [u for u in uploads if u.filename.lower().startswith("map_") and u.filename.lower().endswith((".yaml", ".yml"))]
    if maps:
        await handle_map_upload(conversation_id, reply, reply, await maps[0].read(), maps[0].filename)
        return True

    # A .yaml/.yml file that's missing the map_ prefix isn't silently
    # dropped (it wouldn't match anything else below either) — tell the
    # GM exactly what to rename it to, rather than leaving them wondering
    # why nothing happened.
    unprefixed = [u for u in uploads if u.filename.lower().endswith((".yaml", ".yml"))]
    if unprefixed:
        await reply(
            f"「{unprefixed[0].filename}」看起來是地圖資料，"
            "但檔名需要以 map_ 開頭（例如 map_lighthouse.yaml）才會被辨識，請改檔名後重新上傳。"
        )
        return True

    roles = [u for u in uploads if u.filename.lower().startswith("role_") and u.filename.lower().endswith((".txt", ".md"))]
    if roles:
        # A GM handing out the whole party's cards often drags every
        # role_*.txt into one message, so every card is handled, not just
        # the first.
        for upload in roles:
            content = await upload.read()
            await handle_role_sheet_upload(conversation_id, reply, content.decode("utf-8", errors="replace"), upload.filename)
        return True

    compares = [u for u in uploads if u.filename.lower().endswith((".txt", ".md"))]
    if compares:
        content = await compares[0].read()
        await handle_scenario_compare_upload(
            conversation_id, reply, reply, content.decode("utf-8", errors="replace"), compares[0].filename
        )
        return True
    return False


async def _stage_pdf_parts(conversation_id: str, pdfs: list[Upload], reply: Reply) -> None:
    """Stage a multi-part scenario PDF for a later /coc scenario merge.

    The admission hold is checked before anything is written, so a held
    group gets the hold notice rather than a generic internal error, and no
    staged file is left behind.
    """
    if mutation_admission.is_held(conversation_id):
        await reply(mutation_admission.NOTICE)
        return
    staged = []
    for upload in pdfs:
        key = await asyncio.to_thread(scenario_library.stage_upload, await upload.read())
        staged.append({"key": key, "file_name": upload.filename})
    def stage(ctx: state_transaction.TxContext) -> None:
        ctx.state.staged_pdf_parts.extend(staged)

    async with locks.get_conversation_lock(conversation_id):
        try:
            await state_transaction.amutate(conversation_id, stage, reason="pdf_stage")
        except mutation_admission.MutationHeld:
            # A content-addressed key may already be referenced by another
            # conversation or a pending similarity decision. Keep the bytes.
            await reply(mutation_admission.NOTICE)
            return
    await reply(
        "已暫存 PDF part，尚未合併或解析：\n"
        + "\n".join(f"・{item['key'][:12]} {item['file_name']}" for item in staged)
        + "\n請由 KP 輸入 `/coc scenario merge 暫存ID1 暫存ID2 ...`。"
    )


async def handle_pdf_choice(conversation_id: str, choice: PdfChoice, user_id: str, reply: Reply) -> None:
    """The GM's answer to the new-scenario-vs-correction choice a PDF
    re-upload posted (discord_bot.PdfUploadChoiceButton)."""
    await resolve_pdf_upload_choice(conversation_id, choice, reply, user_id=user_id)
