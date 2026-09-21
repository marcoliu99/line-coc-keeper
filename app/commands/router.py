from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from app import help_service, locks, observability
from app.agents import supervisor
from app.commands import sudo as sudo_policy
from app.commands.handlers import character as character_handler
from app.commands.handlers import combat as combat_handler
from app.commands.handlers import map_handler
from app.commands.handlers import system as system_handler
from app.legacy_commands import (
    FormatMention,
    GetDisplayName,
    Reply,
    SendDM,
    SendDMImage,
    SendImage,
    _resolve_map_action_transaction,
    _run_post_turn_maintenance_after_output,
    _set_character_away_state,
    _skill_names_match,
    handle_check_command,
    handle_luck_decision,
    handle_pregen_luck_roll,
    handle_roll_command,
)
from app.repositories.group_state import load_state

_logger = logging.getLogger(__name__)

_CHARACTER_COMMANDS = {"pc", "sheet", "setskill", "setconnection", "create", "alloc", "pregens", "pregen", "usepregen", "switch", "characters", "retire"}
_SYSTEM_COMMANDS = {"newgame", "pdf", "kp", "scenario", "status", "end", "setpersona", "era", "index", "away", "back", "start", "checkpoint", "checkpoints", "rollback", "digest", "digests"}
_MAP_COMMANDS = {"showpage", "where", "enter", "leavemap"}


class _SudoDenied(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _sudo_check_matches_pending(pending: dict | None, args: tuple[str, ...]) -> bool:
    """Return whether a sudo check can resolve the subject's pending request.

    ``handle_check_command`` intentionally supports a player-initiated check
    when the supplied skill does not match the pending request. That behavior
    is correct for normal players, but unsafe for sudo: KP delegation must
    only consume the check the Keeper registered for this subject.
    """
    if not isinstance(pending, dict) or len(args) > 1:
        return False
    skill_arg = args[0] if args else None
    pending_type = pending.get("type")
    if pending_type == "sanity":
        return skill_arg is None
    if pending_type == "skill":
        return skill_arg is None or _skill_names_match(str(pending.get("skill", "")), skill_arg)
    if pending_type == "choice":
        if skill_arg is None:
            return True
        options = pending.get("options")
        if not isinstance(options, list):
            return False
        return any(
            _skill_names_match(str(option.get("label", "")), skill_arg)
            or _skill_names_match(str(option.get("skill", "")), skill_arg)
            for option in options
            if isinstance(option, dict)
        )
    return False


def sudo_public_marker(state, parsed: sudo_policy.ParsedSudoCommand) -> str:
    """Return the public marker for one sudo command.

    ``switch`` uses its destination only after that character is active in the
    supplied state. This keeps a rejected switch from claiming it operated on
    a character it never reached; callers that reply after mutation pass the
    latest state so a successful switch still gets the destination marker.
    """
    character = None
    if parsed.command == "switch":
        requested_name = " ".join(parsed.args).strip()
        matches = [
            candidate
            for candidate in state.characters_for_owner(parsed.subject_user_id)
            if candidate.name == requested_name
        ]
        if len(matches) == 1:
            active = state.get_active_character(parsed.subject_user_id)
            if active is not None and active.character_id == matches[0].character_id:
                character = active
    if character is None:
        character = state.get_active_character(parsed.subject_user_id)
    character_name = character.name if character is not None else "玩家"
    return f"【KP Assistant 代操作：{character_name}】"


def _sudo_reply(reply: Reply, marker: str | Callable[[], str]) -> Reply:
    async def wrapped(message: str) -> None:
        resolved_marker = marker() if callable(marker) else marker
        await reply(f"{resolved_marker}\n{message}")

    return wrapped


def _sudo_image(reply: Reply, marker: str, send_image: SendImage) -> SendImage:
    async def wrapped(png_bytes: bytes, conversation_id: str, page_number: int) -> None:
        await reply(marker)
        await send_image(png_bytes, conversation_id, page_number)

    return wrapped


def _record_sudo_event(
    event_name: str,
    parsed: sudo_policy.ParsedSudoCommand | None,
    actor_user_id: str,
    *,
    level: int = logging.INFO,
    duration_ms: float | None = None,
    status: str | None = None,
    deny_reason: str | None = None,
    error_type: str | None = None,
) -> None:
    observability.event(
        event_name,
        level=level,
        command=parsed.audit_command if parsed is not None else "parse",
        actor_user_id_hash=observability.safe_identifier(actor_user_id),
        subject_user_id_hash=(
            observability.safe_identifier(parsed.subject_user_id) if parsed is not None else None
        ),
        duration_ms=duration_ms,
        status=status,
        deny_reason=deny_reason,
        error_type=error_type,
    )


async def _run_sudo_act_locked(
    conversation_id: str,
    acting_context: sudo_policy.ActingContext,
    parsed: sudo_policy.ParsedSudoCommand,
    state,
    reply: Reply,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
) -> str:
    subject_user_id = acting_context.subject_user_id
    character = state.get_active_character(subject_user_id)
    if character is None:
        raise _SudoDenied("target_requires_character")
    if not state.active or not state.game_started:
        raise _SudoDenied("game_not_started")

    action_text = " ".join(parsed.args).strip()
    resolved_location = await asyncio.to_thread(
        _resolve_map_action_transaction, conversation_id, subject_user_id, action_text
    )
    canonical_text = f"[KP Assistant 代操作 {character.name}] {action_text}"
    async with locks.get_keeper_turn_lock(conversation_id):
        with observability.context(turn_id=observability.new_id("turn")):
            reply_text, private_messages, image_requests = await supervisor.run_turn(
                state=state,
                user_id=subject_user_id,
                display_name=character.name,
                text=canonical_text,
                resolved_location=resolved_location,
                speaker_role="player",
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
            run_maintenance=True,
        )
    return "success"


async def _dispatch_sudo_locked(
    conversation_id: str,
    actor_user_id: str,
    is_keeper: bool,
    parsed: sudo_policy.ParsedSudoCommand,
    reply: Reply,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
    format_mention: FormatMention,
) -> str:
    state = load_state(conversation_id)
    if state.kp_assistant_user_id != actor_user_id and not is_keeper:
        raise _SudoDenied("not_authorized")
    if parsed.subject_user_id == actor_user_id:
        raise _SudoDenied("self_target")
    if state.kp_assistant_user_id == parsed.subject_user_id:
        raise _SudoDenied("kp_target")
    if state.get_active_character(actor_user_id) is not None or actor_user_id in state.creation_sessions:
        raise _SudoDenied("actor_role_conflict")
    if parsed.command == "act" and (not state.active or not state.game_started):
        raise _SudoDenied("game_not_started")
    if parsed.command in {
        "act", "away", "back", "check", "enter", "leavemap", "luck", "retire",
        "setconnection", "setskill", "sheet", "showpage", "where",
    } and state.get_active_character(parsed.subject_user_id) is None:
        raise _SudoDenied("target_requires_character")

    acting_context = sudo_policy.ActingContext(
        actor_user_id=actor_user_id,
        subject_user_id=parsed.subject_user_id,
        mode="kp_sudo",
        command=parsed.command,
    )
    marker = sudo_public_marker(state, parsed)
    if parsed.command == "switch":
        marker_reply = _sudo_reply(
            reply,
            lambda: sudo_public_marker(load_state(conversation_id), parsed),
        )
    else:
        marker_reply = _sudo_reply(reply, marker)
    marker_image = _sudo_image(reply, marker, send_image)

    with observability.context(acting_mode=acting_context.mode, acting_command=acting_context.command):
        if parsed.command == "act":
            return await _run_sudo_act_locked(
                conversation_id, acting_context, parsed, state,
                marker_reply, send_dm, marker_image, send_dm_image,
            )

        if parsed.command == "check":
            pending_check = state.pending_checks.get(acting_context.subject_user_id)
            if not _sudo_check_matches_pending(pending_check, parsed.args):
                await marker_reply(sudo_policy.denial_message("pending_check_mismatch"))
                return "rejected"
            if not locks.try_acquire_check(conversation_id, acting_context.subject_user_id):
                await marker_reply("target 上一次的檢定還在處理中，請稍等結果出來，不要重複送出。")
                return "rejected"
            try:
                result = await handle_check_command(
                    conversation_id,
                    acting_context.subject_user_id,
                    marker_reply,
                    send_dm,
                    marker_image,
                    send_dm_image,
                    " ".join(parsed.player_parts),
                )
            finally:
                locks.release_check(conversation_id, acting_context.subject_user_id)
            return "success" if result else "rejected"

        if parsed.command == "luck":
            if not locks.try_acquire_check(conversation_id, acting_context.subject_user_id):
                await marker_reply("target 上一次的檢定還在處理中，請稍等結果出來，不要重複送出。")
                return "rejected"
            try:
                result = await handle_luck_decision(
                    conversation_id,
                    acting_context.subject_user_id,
                    parsed.args[0],
                    marker_reply,
                    send_dm,
                    marker_image,
                    send_dm_image,
                )
            finally:
                locks.release_check(conversation_id, acting_context.subject_user_id)
            return "success" if result else "rejected"

        player_parts = parsed.player_parts
        if parsed.command in {"away", "back"}:
            away_result = await asyncio.to_thread(
                _set_character_away_state,
                conversation_id,
                acting_context.subject_user_id,
                parsed.command == "away",
            )
            if away_result.error_text:
                await marker_reply(away_result.error_text)
                return "rejected"
            message = (
                f"{away_result.character_name} 已標記為暫離，戰鬥中會自動跳過他的回合，直到輸入「/coc back」回來。"
                if parsed.command == "away"
                else f"{away_result.character_name} 回來了，恢復正常參與。"
            )
            await marker_reply(message)
            return "success"
        if parsed.command in {"sheet", "characters", "pregen", "pregens", "retire", "setskill", "setconnection", "switch"}:
            result = await character_handler.handle_character_command(
                conversation_id, acting_context.subject_user_id, marker_reply, send_dm, player_parts
            )
            return "success" if result else "rejected"
        if parsed.command in {"showpage", "where", "enter", "leavemap"}:
            result = await map_handler.handle_map_command(
                conversation_id, acting_context.subject_user_id, marker_reply, marker_image, player_parts
            )
            return "success" if result else "rejected"
        raise _SudoDenied("forbidden_command")


async def _handle_sudo_command(
    conversation_id: str,
    actor_user_id: str,
    reply: Reply,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
    parts: list[str],
    format_mention: FormatMention,
    is_keeper: bool,
    allow_opaque_target: bool,
) -> None:
    parsed, parse_error = sudo_policy.parse_sudo_command(
        parts, allow_opaque_target=allow_opaque_target
    )
    if parse_error is not None:
        _record_sudo_event("sudo.denied", parsed, actor_user_id, level=logging.WARNING, deny_reason=parse_error)
        await reply(sudo_policy.denial_message(parse_error))
        return
    assert parsed is not None
    started = asyncio.get_running_loop().time()
    _record_sudo_event("sudo.started", parsed, actor_user_id)
    try:
        dispatch_status = "rejected"
        async with locks.get_keeper_priority_gate(conversation_id, is_kp=True), locks.get_conversation_lock(conversation_id):
            dispatch_status = await _dispatch_sudo_locked(
                conversation_id,
                actor_user_id,
                is_keeper,
                parsed,
                reply,
                send_dm,
                send_image,
                send_dm_image,
                format_mention,
            )
    except _SudoDenied as exc:
        _record_sudo_event(
            "sudo.denied",
            parsed,
            actor_user_id,
            level=logging.WARNING,
            duration_ms=(asyncio.get_running_loop().time() - started) * 1000,
            deny_reason=exc.reason,
        )
        await reply(sudo_policy.denial_message(exc.reason))
    except Exception as exc:
        _record_sudo_event(
            "sudo.failed",
            parsed,
            actor_user_id,
            level=logging.ERROR,
            duration_ms=(asyncio.get_running_loop().time() - started) * 1000,
            error_type=type(exc).__name__,
        )
        _logger.exception("sudo command failed command=%s", parsed.audit_command)
        await reply("sudo 操作失敗，請稍後再試。")
    else:
        _record_sudo_event(
            "sudo.completed",
            parsed,
            actor_user_id,
            duration_ms=(asyncio.get_running_loop().time() - started) * 1000,
            status=dispatch_status,
        )


def is_known_coc_command(subcommand: str) -> bool:
    """Return whether Discord should route this `/coc` subcommand to a handler."""
    normalized = subcommand.casefold()
    return normalized in _CHARACTER_COMMANDS | _SYSTEM_COMMANDS | _MAP_COMMANDS | {"combat", "check", "luck", "sudo"}


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
    is_keeper: bool = False,
    allow_opaque_sudo_target: bool = False,
) -> None:
    with observability.span("router", command_name=text.split()[1] if len(text.split()) > 1 else "text"):
        await _handle_text_message_impl(
            conversation_id, user_id, get_display_name, reply, send_dm, send_image,
            send_dm_image, text, format_mention, is_keeper, allow_opaque_sudo_target,
        )


async def _handle_text_message_impl(
    conversation_id: str,
    user_id: str,
    get_display_name: GetDisplayName,
    reply: Reply,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
    text: str,
    format_mention: FormatMention = lambda owner_id: owner_id,
    is_keeper: bool = False,
    allow_opaque_sudo_target: bool = False,
) -> None:
    text = text.strip()

    if text.startswith("/roll"):
        await handle_roll_command(reply, text)
        return

    command_parts = text.split()
    is_coc_command = bool(command_parts) and command_parts[0].casefold() == "/coc"
    coc_subcommand = command_parts[1].casefold() if len(command_parts) > 1 and is_coc_command else ""

    if coc_subcommand == "sudo":
        await _handle_sudo_command(
            conversation_id,
            user_id,
            reply,
            send_dm,
            send_image,
            send_dm_image,
            command_parts,
            format_mention,
            is_keeper,
            allow_opaque_sudo_target,
        )
        return

    if coc_subcommand == "check":
        if not locks.try_acquire_check(conversation_id, user_id):
            await reply("上一次的檢定還在處理中，請稍等結果出來，不要重複送出。")
            return
        try:
            async with locks.get_conversation_lock(conversation_id):
                await handle_check_command(conversation_id, user_id, reply, send_dm, send_image, send_dm_image, text)
        finally:
            locks.release_check(conversation_id, user_id)
        return

    if coc_subcommand == "luck":
        choice = command_parts[2] if len(command_parts) > 2 else "skip"
        if not locks.try_acquire_check(conversation_id, user_id):
            await reply("上一次的檢定還在處理中，請稍等結果出來，不要重複送出。")
            return
        try:
            async with locks.get_conversation_lock(conversation_id):
                if choice.casefold() == "roll":
                    await handle_pregen_luck_roll(conversation_id, user_id, reply)
                else:
                    await handle_luck_decision(conversation_id, user_id, choice, reply, send_dm, send_image, send_dm_image)
        finally:
            locks.release_check(conversation_id, user_id)
        return

    if is_coc_command:
        parts = command_parts[:]
        parts[0] = "/coc"
        if len(parts) > 1:
            parts[1] = parts[1].casefold()
        sub = parts[1] if len(parts) > 1 else "help"

        if sub == "combat":
            async with locks.get_conversation_lock(conversation_id):
                await combat_handler.handle_combat_command(conversation_id, reply, parts)
            return

        if sub in _CHARACTER_COMMANDS:
            async with locks.get_conversation_lock(conversation_id):
                await character_handler.handle_character_command(conversation_id, user_id, reply, send_dm, parts)
            return

        if sub in _SYSTEM_COMMANDS:
            # PDF import/merge/reparse perform long extraction and
            # handle_pdf_upload acquires the conversation lock around each
            # state commit. Keep these top-level operations outside the lock;
            # their helpers lock only around the short read-modify-write
            # sections. asyncio.Lock is not re-entrant.
            scenario_action = parts[2].casefold() if sub == "scenario" and len(parts) > 2 else ""
            is_long_scenario_operation = sub == "scenario" and scenario_action in {
                "import", "merge", "reparse",
            }
            if is_long_scenario_operation:
                await system_handler.handle_system_command(
                    conversation_id, user_id, reply, send_dm, send_image, send_dm_image, parts, format_mention,
                    is_keeper,
                )
            else:
                async with locks.get_conversation_lock(conversation_id):
                    await system_handler.handle_system_command(
                        conversation_id, user_id, reply, send_dm, send_image, send_dm_image, parts, format_mention,
                        is_keeper,
                    )
            return

        if sub in _MAP_COMMANDS:
            async with locks.get_conversation_lock(conversation_id):
                await map_handler.handle_map_command(conversation_id, user_id, reply, send_image, parts)
            return

        async with locks.get_conversation_lock(conversation_id):
            state = load_state(conversation_id)
            await reply(help_service.get_page(state, user_id).text)
        return

    # Non-command text -> goes to the Keeper Supervisor. KP Assistant is
    # optional: only when this conversation currently has one registered do
    # ordinary Keeper turns go through the priority gate (queued KP messages
    # jump ahead of queued player messages); otherwise this intentionally
    # bypasses the gate and keeps the plain conversation-lock-only path —
    # see app/locks.py's get_keeper_priority_gate docstring.
    scheduling_state = load_state(conversation_id)
    if not scheduling_state.kp_assistant_user_id:
        async with locks.get_conversation_lock(conversation_id):
            await _handle_ordinary_text_message_locked(
                conversation_id, user_id, get_display_name, reply, send_dm, send_image, send_dm_image, text
            )
        return

    is_kp_priority = scheduling_state.kp_assistant_user_id == user_id
    async with locks.get_keeper_priority_gate(conversation_id, is_kp=is_kp_priority), locks.get_conversation_lock(conversation_id):
        await _handle_ordinary_text_message_locked(
            conversation_id, user_id, get_display_name, reply, send_dm, send_image, send_dm_image, text
        )


async def _handle_ordinary_text_message_locked(
    conversation_id: str,
    user_id: str,
    get_display_name: GetDisplayName,
    reply: Reply,
    send_dm: SendDM,
    send_image: SendImage,
    send_dm_image: SendDMImage,
    text: str,
) -> None:
    """Handle an ordinary non-command text message via the Keeper Supervisor.

    The caller must already hold get_conversation_lock(conversation_id). This
    function always reloads state itself; any pre-gate scheduling snapshot is
    only a priority hint and never authoritative game state.
    """
    state = load_state(conversation_id)
    if not state.active or not state.game_started:
        # Two separate conditions on purpose: a scenario must be loaded
        # (state.active) AND /coc start must have actually run for it
        # (state.game_started) before ordinary free text becomes in-character
        # play.
        return

    is_kp_assistant = state.kp_assistant_user_id == user_id
    if is_kp_assistant:
        display_name = await get_display_name()
        speaker_role = "kp_assistant"
        resolved_location = None
    elif state.get_active_character(user_id) is None:
        display_name = await get_display_name()
        await reply(f"{display_name}，你還沒有調查員角色，先輸入「/coc pc 角色名 職業」建立角色吧！")
        return
    else:
        active_character = state.get_active_character(user_id)
        if active_character is None:
            return
        display_name = active_character.name
        speaker_role = "player"
        resolved_location = await asyncio.to_thread(_resolve_map_action_transaction, conversation_id, user_id, text)

    async with locks.get_keeper_turn_lock(conversation_id):
        with observability.context(turn_id=observability.new_id("turn")):
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
