from __future__ import annotations

import logging
from typing import Awaitable, Callable

from app.legacy_commands import (
    Reply,
    GetDisplayName,
    SendDM,
    SendImage,
    SendDMImage,
    FormatMention,
    handle_roll_command,
    handle_check_command,
    handle_luck_decision,
    handle_unsupported_message,
    _handle_coc_command,
)
from app import locks
from app.repositories.group_state import load_state
from app.commands.handlers import combat as combat_handler
from app.commands.handlers import character as character_handler
from app.commands.handlers import system as system_handler
from app.commands.handlers import map_handler

_logger = logging.getLogger(__name__)

async def handle_text_message(
    conversation_id: str,
    user_id: str,
    get_display_name: GetDisplayName,
    reply: Reply,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
    text: str,
    format_mention: FormatMention = lambda owner_id: owner_id,
) -> None:
    text = text.strip()

    if text.startswith("/roll"):
        await handle_roll_command(reply, text)
        return

    if text.startswith("/coc check"):
        if not locks.try_acquire_check(conversation_id, user_id):
            await reply("上一次的檢定還在處理中，請稍等結果出來，不要重複送出。")
            return
        try:
            async with locks.get_conversation_lock(conversation_id):
                await handle_check_command(conversation_id, user_id, reply, send_dm, send_image, send_dm_image, text)
        finally:
            locks.release_check(conversation_id, user_id)
        return

    if text.startswith("/coc luck"):
        parts = text.split()
        choice = parts[2] if len(parts) > 2 else "skip"
        if not locks.try_acquire_check(conversation_id, user_id):
            await reply("上一次的檢定還在處理中，請稍等結果出來，不要重複送出。")
            return
        try:
            async with locks.get_conversation_lock(conversation_id):
                await handle_luck_decision(conversation_id, user_id, choice, reply, send_dm, send_image, send_dm_image)
        finally:
            locks.release_check(conversation_id, user_id)
        return

    if text.startswith("/coc"):
        parts = text.split()
        sub = parts[1] if len(parts) > 1 else "help"
        
        if sub == "combat":
            async with locks.get_conversation_lock(conversation_id):
                await combat_handler.handle_combat_command(conversation_id, reply, parts)
            return

        if sub in ("pc", "sheet", "setskill", "setconnection", "create", "alloc", "pregens", "pregen", "usepregen"):
            async with locks.get_conversation_lock(conversation_id):
                await character_handler.handle_character_command(conversation_id, user_id, reply, send_dm, parts)
            return

        if sub in ("newgame", "pdf", "kp", "status", "end", "setpersona", "era", "index", "away", "back", "start"):
            async with locks.get_conversation_lock(conversation_id):
                await system_handler.handle_system_command(
                    conversation_id, user_id, reply, send_dm, send_image, send_dm_image, parts, format_mention
                )
            return

        if sub in ("showpage", "where", "enter", "leavemap"):
            async with locks.get_conversation_lock(conversation_id):
                await map_handler.handle_map_command(conversation_id, user_id, reply, send_image, parts)
            return

        async with locks.get_conversation_lock(conversation_id):
            from app.legacy_commands import HELP_TEXT
            await reply(HELP_TEXT)
        return

    # Non-command text -> goes to Keeper Supervisor
    async with locks.get_conversation_lock(conversation_id):
        state = load_state(conversation_id)
        if not state.active or not state.game_started:
            return

        is_kp_assistant = state.kp_assistant_user_id == user_id
        if is_kp_assistant:
            display_name = await get_display_name()
            speaker_role = "kp_assistant"
            resolved_location = None
        elif user_id not in state.characters:
            display_name = await get_display_name()
            await reply(f"{display_name}，你還沒有調查員角色，先輸入「/coc pc 角色名 職業」建立角色吧！")
            return
        else:
            display_name = state.characters[user_id].name
            speaker_role = "player"
            from app.legacy_commands import _resolve_map_action_transaction
            import asyncio
            resolved_location = await asyncio.to_thread(_resolve_map_action_transaction, conversation_id, user_id, text)

        # Route to KeeperSupervisor here instead of old keeper.py
        import asyncio
        from app.agents import supervisor
        from app.legacy_commands import _run_post_turn_maintenance_after_output
        async with locks.get_keeper_turn_lock(conversation_id):
            reply_text, private_messages, image_requests = await supervisor.run_turn(
                state=state,
                user_id=user_id,
                display_name=display_name,
                text=text,
                resolved_location=resolved_location,
                speaker_role=speaker_role,
                conversation_id=conversation_id,
            )
            await _run_post_turn_maintenance_after_output(
                conversation_id,
                reply,
                reply_text,
                send_dm,
                send_image,
                send_dm_image,
                private_messages,
                image_requests,
                run_maintenance=not is_kp_assistant,
            )
