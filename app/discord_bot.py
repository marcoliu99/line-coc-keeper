"""Discord adapter — the discord.py-based front-end.

Game/command logic lives behind app/commands/router.py; this module is the entry point and the event
handlers (message, ready, the backup loop) and only translates Discord events into calls against that
shared layer. Everything it sends or builds lives in app/discord_transport/. Run it as its own process
(`python -m app.discord_bot`); discord.py owns a persistent gateway connection, so there is no webhook
URL or ngrok tunnel.
"""
from __future__ import annotations

import asyncio
import logging
import time
import unicodedata
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import discord

from app import (
    async_utils,
    config,
    db,
    help_service,
    logging_config,
    observability,
    providers,
    scenario_rag,
)
from app.commands import router as command_router
from app.commands import sudo as sudo_policy
from app.commands.handlers.uploads import Upload
from app.config import (
    BACKUP_INTERVAL_MINUTES,
    DISCORD_BOT_TOKEN,
    LOG_SLOW_REQUEST_MS,
)
from app.discord_transport import controls, delivery, gateway, help_ui, interactions
from app.providers import anthropic_provider, gemini_provider, openai_provider
from app.repositories.group_state import StateRevisionConflict
from app.repositories.group_state import load_state as load_group_state
from app.services import pending_buttons

_logger = logging.getLogger(__name__)
_backup_task: asyncio.Task | None = None


def _is_ooc_message(text: str) -> bool:
    ooc_text = unicodedata.normalize("NFKC", text or "").lstrip()
    return ooc_text.startswith(("@", "<@"))


gateway.client.add_dynamic_items(
    controls.CheckButton, controls.LuckSpendButton, controls.PdfUploadChoiceButton,
    help_ui.HelpButton, help_ui.HelpExecuteButton,
)


@gateway.client.event
async def on_ready() -> None:
    global _backup_task
    provider_module = {
        "openai": openai_provider,
        "anthropic": anthropic_provider,
        "gemini": gemini_provider,
    }.get(config.LLM_PROVIDER)
    provider_key = {
        "openai": config.OPENAI_API_KEY,
        "anthropic": config.ANTHROPIC_API_KEY,
        "gemini": config.GEMINI_API_KEY,
    }.get(config.LLM_PROVIDER, "")
    if provider_module is not None and provider_key:
        try:
            await provider_module.get_async_client()
        except Exception:
            _logger.exception("failed to prewarm %s async provider client", config.LLM_PROVIDER)
    if _backup_task is None or _backup_task.done():
        _backup_task = asyncio.create_task(_backup_loop())
    print(f"Discord bot 已上線：{gateway.client.user}")


async def _backup_loop() -> None:
    while True:
        try:
            await asyncio.sleep(BACKUP_INTERVAL_MINUTES * 60)
            await asyncio.to_thread(db.backup_now, "scheduled")
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.exception("scheduled backup failed; will retry next interval")


@asynccontextmanager
async def _best_effort_typing(channel: discord.abc.Messageable) -> AsyncIterator[None]:
    """Wraps `channel.typing()` so a Discord-side failure entering or
    exiting it (rate limit, transient network hiccup — `Typing.__aenter__`
    itself makes a real API call, see discord.py's `Typing` class) can
    never block or fail actual message processing. The indicator is a
    nice-to-have UX signal, not a prerequisite — review finding: the naive
    `async with message.channel.typing():` wrapper made every message's
    processing depend on that one API call succeeding first, a brand new
    single point of failure that didn't exist before this indicator was
    added."""
    try:
        typing_cm = channel.typing()
        await typing_cm.__aenter__()
    except Exception:  # noqa: BLE001 - the indicator failing must never block the message it's decorating
        observability.event("discord.typing.failed", level=logging.WARNING)
        typing_cm = None
    try:
        yield
    finally:
        if typing_cm is not None:
            try:
                await typing_cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001 - same reasoning as entering it
                observability.event("discord.typing.exit_failed", level=logging.WARNING)


@gateway.client.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot or _is_ooc_message(message.content):
        return

    conversation_id = interactions.channel_conversation_id(message.channel.id)
    with observability.request_context(conversation_id=conversation_id):
        observed = config.LOG_ENABLED
        if observed:
            parts = (message.content or "").strip().split()
            command_name = parts[1].casefold() if len(parts) > 1 and parts[0].casefold() == "/coc" else None
            message_kind = "attachment" if message.attachments else "plain_text"
            observability.event(
                "request.started",
                platform="discord",
                message_kind=message_kind,
                command_name=command_name,
                attachment_count=len(message.attachments),
            )
        started = time.perf_counter() if observed else 0.0
        try:
            # Entered before anything else (including the conversation
            # lock) so Discord's typing indicator appears immediately on
            # receipt, not only once processing actually starts — a queued
            # message behind a long-running Keeper turn would otherwise
            # look identical to the bot being dead for tens of seconds.
            # `typing()` is an async context manager that keeps re-sending
            # Discord's ~10s typing signal for as long as the block is open.
            # _best_effort_typing wraps it so a Discord-side failure on the
            # typing indicator itself can never block _handle_message.
            async with _best_effort_typing(message.channel):
                await _handle_message(message)
        except Exception as exc:
            if observed:
                observability.event(
                    "request.failed",
                    level=logging.ERROR,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    error_type=type(exc).__name__,
                    status="error",
                    **delivery.request_metrics(),
                )
            raise
        else:
            if observed:
                duration_ms = (time.perf_counter() - started) * 1000
                level = logging.WARNING if duration_ms >= LOG_SLOW_REQUEST_MS else logging.INFO
                observability.event(
                    "request.completed",
                    level=level,
                    duration_ms=duration_ms,
                    slow_threshold_ms=LOG_SLOW_REQUEST_MS,
                    status=observability.current_context().get("request_status", "success"),
                    **delivery.request_metrics(),
                )


def _error_reference() -> str:
    """A short code the KP can search for in the log: the tail of the request id every log line of this request carries.

    Empty when logging is entirely off, because then there is no id and nothing to find.
    """
    request_id = observability.current_context().get("request_id", "")
    return f"（代碼 {request_id[-6:]}）" if request_id else ""


async def _handle_message(message: discord.Message) -> None:
    conversation_id = interactions.channel_conversation_id(message.channel.id)
    user_id = str(message.author.id)
    reply = delivery.make_reply(message.channel)
    send_image = delivery.make_send_image(message.channel)

    async def get_display_name() -> str:
        return message.author.display_name

    def format_mention(owner_id: str) -> str:
        # owner_id is str(message.author.id) — a Discord snowflake — so this
        # needs no API call, unlike get_display_name; Discord resolves
        # <@id> to a clickable name client-side.
        return f"<@{owner_id}>"

    try:
        uploads = [Upload(a.filename, a.read) for a in message.attachments]
        if await command_router.handle_uploads(
            conversation_id, uploads, reply,
            post_pdf_buttons=lambda: controls.post_pdf_upload_buttons(message.channel, conversation_id),
        ):
            return

        text = (message.content or "").strip()
        if not text:
            if message.attachments:
                await command_router.handle_unsupported_attachment(conversation_id, reply)
            return

        command_parts = text.split()
        if command_parts[0].casefold() == "/coc" and (
            len(command_parts) == 1
            or command_parts[1].casefold() == "help"
            or not command_router.is_known_coc_command(command_parts[1])
        ):
            state = await asyncio.to_thread(load_group_state, conversation_id)
            path = help_service.resolve_text_path(
                state,
                user_id,
                command_parts[2:] if len(command_parts) > 1 and command_parts[1].casefold() == "help" else [],
            )
            await help_ui.post_help_page(message.channel, conversation_id, user_id, path)
            return

        state_before = await asyncio.to_thread(load_group_state, conversation_id)
        sudo_command: sudo_policy.ParsedSudoCommand | None = None
        if command_parts[0].casefold() == "/coc" and len(command_parts) > 1 and command_parts[1].casefold() == "sudo":
            sudo_command, _ = sudo_policy.parse_sudo_command(command_parts, allow_opaque_target=False)
        completion = pending_buttons.ControlCompletion(
            conversation_id, dict(state_before.pending_checks),
            dict(state_before.pending_luck_decisions), sudo_command,
        )

        try:
            interactions.note_ignored_keeper_role(message.author, state_before, interactions.formerly_role_gated(command_parts))
            await command_router.handle_text_message(
                conversation_id, user_id, get_display_name, reply, delivery.send_dm, send_image, delivery.send_dm_image, text,
                format_mention, post_turn_hook=completion.claim_locked,
                server=interactions.server_facts(message.author, getattr(message, "mentions", ())),
                referenced_message_id=(
                    str(message.reference.message_id)
                    if command_parts[0].casefold() == "/coc"
                    and len(command_parts) > 1
                    and command_parts[1].casefold() == "correct"
                    and message.reference is not None
                    and message.reference.message_id is not None
                    else None
                ),
            )
        finally:
            # Always attempt this, even if handle_text_message raised partway
            # through a turn — a check can already be registered and saved
            # (e.g. skill_check's tool call) before a *later* tool call in the
            # same turn blows up, and that would otherwise silently strand a
            # pending check with no button ever posted for it.
            await controls.publish_control_completion(completion, message.channel)
    except StateRevisionConflict:
        observability.mark_request_error()
        _logger.warning(
            "state revision conflict for conversation_id=%s; asking the user to retry",
            conversation_id,
        )
        try:
            await reply("遊戲狀態剛被另一個操作更新，這次指令沒有套用，請再試一次。")
        except Exception:
            _logger.exception("failed to report state revision conflict for conversation_id=%s", conversation_id)
    except Exception:
        observability.mark_request_error()
        _logger.exception("on_message failed for conversation_id=%s", conversation_id)
        try:
            await reply(f"發生內部錯誤了，請稍後再試；詳細資訊已記錄到 Bot log{_error_reference()}。")
        except Exception:
            _logger.exception("also failed to report the above error back to conversation_id=%s", conversation_id)


async def _run_bot() -> None:
    try:
        await gateway.client.start(DISCORD_BOT_TOKEN)
    finally:
        try:
            if not gateway.client.is_closed():
                await gateway.client.close()
        finally:
            try:
                await scenario_rag.shutdown_prewarm()
            finally:
                try:
                    await async_utils.wait_for_background_tasks(
                        config.PROVIDER_SHUTDOWN_GRACE_SECONDS
                    )
                finally:
                    await providers.shutdown_async_clients()


def warn_if_privacy_isolation_disabled() -> None:
    """spec §12 item #6: doesn't block startup (a KP may have a legitimate
    local/dev reason to flip this), but a silent per-call WARNING deep in
    spoiler_policy is easy to miss — surface it once, loudly, at boot."""
    if config.PRIVACY_ISOLATION_ENABLED:
        return
    _logger.warning(
        "PRIVACY_ISOLATION_ENABLED=false — 玩家私訊、秘密目標、戰鬥隱藏資訊等隱私保護"
        "已全部停用，不建議用於正式營運環境。"
    )
    observability.event(
        "privacy.isolation.disabled", level=logging.WARNING, reason="startup_config"
    )


def main() -> None:
    if not DISCORD_BOT_TOKEN:
        raise SystemExit("尚未設定 DISCORD_BOT_TOKEN，請檢查 .env")
    logging_config.configure_logging()
    warn_if_privacy_isolation_disabled()
    asyncio.run(_run_bot())


if __name__ == "__main__":
    main()
