"""FastAPI webhook server — the LINE-specific adapter.

Game/command logic lives in app/commands.py; this module only translates LINE's
webhook payloads and SDK objects into calls against that shared layer. See
app/discord_bot.py for the Discord equivalent.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request, Response

from linebot.v3 import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    AsyncMessagingApiBlob,
    Configuration,
    ImageMessage,
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import (
    AudioMessageContent,
    FileMessageContent,
    GroupSource,
    ImageMessageContent,
    LocationMessageContent,
    MessageEvent,
    RoomSource,
    StickerMessageContent,
    TextMessageContent,
    UserSource,
    VideoMessageContent,
)

from app import locks
from app.commands import router as command_router
from app.config import LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, PUBLIC_BASE_URL
from app.legacy_commands import (
    Reply,
    SendDM,
    SendDMImage,
    SendImage,
    GetDisplayName,
    handle_map_upload,
    handle_pdf_upload,
    handle_role_sheet_upload,
    handle_scenario_compare_upload,
    handle_unsupported_message,
)
from app.repositories.group_state import load_page_image

_logger = logging.getLogger(__name__)

app = FastAPI(title="LINE COC7e Keeper Bot")

_configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
parser = WebhookParser(LINE_CHANNEL_SECRET)

# aiohttp's connector needs a running event loop, so the async LINE clients are
# created lazily on first use inside a request handler rather than at import time.
_async_api_client: AsyncApiClient | None = None
line_bot_api: AsyncMessagingApi | None = None
line_bot_blob_api: AsyncMessagingApiBlob | None = None


def _ensure_line_clients() -> None:
    global _async_api_client, line_bot_api, line_bot_blob_api
    if _async_api_client is None:
        _async_api_client = AsyncApiClient(_configuration)
        line_bot_api = AsyncMessagingApi(_async_api_client)
        line_bot_blob_api = AsyncMessagingApiBlob(_async_api_client)


MAX_LINE_MESSAGE_CHARS = 4800
MAX_REPLY_MESSAGES = 5


def _chunk_text(text: str) -> list[str]:
    text = text.strip() or "（沒有內容）"
    chunks = [text[i : i + MAX_LINE_MESSAGE_CHARS] for i in range(0, len(text), MAX_LINE_MESSAGE_CHARS)]
    return chunks[:MAX_REPLY_MESSAGES]


def _make_reply(reply_token: str) -> Reply:
    async def reply(text: str) -> None:
        messages = [TextMessage(text=c) for c in _chunk_text(text)]
        await line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(reply_token=reply_token, messages=messages)
        )

    return reply


def _make_push(to_id: str) -> Reply:
    # For results that can't make LINE's 60-second, single-use reply token
    # window — see the docstring on commands.handle_pdf_upload. Push messages
    # count against LINE's paid quota (unlike replies, which are free and
    # unlimited), but this only fires once per PDF upload, not per turn.
    async def push(text: str) -> None:
        messages = [TextMessage(text=c) for c in _chunk_text(text)]
        await line_bot_api.push_message_with_http_info(
            PushMessageRequest(to=to_id, messages=messages)
        )

    return push


async def _send_dm(owner_id: str, text: str) -> None:
    # owner_id is the raw LINE user id stored on Character.owner_id — push_message's
    # `to` field accepts that directly, so this just reuses _make_push. Will raise
    # if that user hasn't added the bot as a friend; commands.py swallows the
    # exception (see its docstring on why it doesn't fall back to posting publicly).
    await _make_push(owner_id)(text)


@app.get("/images/{conversation_id}/{page_number}.png")
async def get_page_image(conversation_id: str, page_number: int):
    png_bytes = load_page_image(conversation_id, page_number)
    if png_bytes is None:
        raise HTTPException(status_code=404, detail="Image not found")
    return Response(content=png_bytes, media_type="image/png")


def _image_url(conversation_id: str, page_number: int) -> str:
    if not PUBLIC_BASE_URL:
        raise RuntimeError("PUBLIC_BASE_URL 尚未設定，LINE 無法用圖片訊息（見 .env.example 的說明）")
    return f"{PUBLIC_BASE_URL}/images/{conversation_id}/{page_number}.png"


async def _send_image_to(to_id: str, png_bytes: bytes, conversation_id: str, page_number: int) -> None:
    # png_bytes is unused here — LINE's image message can't carry raw bytes, it
    # needs a URL, which is why the get_page_image route above exists: LINE's own
    # servers fetch that URL when actually rendering the image to the user.
    url = _image_url(conversation_id, page_number)
    await line_bot_api.push_message_with_http_info(
        PushMessageRequest(to=to_id, messages=[ImageMessage(original_content_url=url, preview_image_url=url)])
    )


async def _send_image(png_bytes: bytes, conversation_id: str, page_number: int) -> None:
    to_id = _push_target_id_from_conversation(conversation_id)
    await _send_image_to(to_id, png_bytes, conversation_id, page_number)


async def _send_dm_image(owner_id: str, png_bytes: bytes, conversation_id: str, page_number: int) -> None:
    await _send_image_to(owner_id, png_bytes, conversation_id, page_number)


def _push_target_id_from_conversation(conversation_id: str) -> str:
    # Reverses _conversation_id's namespacing to recover the raw LINE id
    # push_message needs, since /coc showpage only has conversation_id on hand
    # (unlike the file-upload path, which still has the original event.source).
    for prefix in ("line-group-", "line-room-", "line-user-"):
        if conversation_id.startswith(prefix):
            return conversation_id[len(prefix):]
    return conversation_id


def _conversation_id(source) -> str:
    if isinstance(source, GroupSource):
        return f"line-group-{source.group_id}"
    if isinstance(source, RoomSource):
        return f"line-room-{source.room_id}"
    if isinstance(source, UserSource):
        return f"line-user-{source.user_id}"
    return "line-unknown"


def _push_target_id(source) -> str:
    if isinstance(source, GroupSource):
        return source.group_id
    if isinstance(source, RoomSource):
        return source.room_id
    if isinstance(source, UserSource):
        return source.user_id
    return ""


async def _display_name(source, user_id: str) -> str:
    try:
        if isinstance(source, GroupSource):
            profile = await line_bot_api.get_group_member_profile(source.group_id, user_id)
        elif isinstance(source, RoomSource):
            profile = await line_bot_api.get_room_member_profile(source.room_id, user_id)
        else:
            profile = await line_bot_api.get_profile(user_id)
        return profile.display_name
    except Exception:
        return user_id[:8] if user_id else "玩家"


@app.post("/callback")
async def callback(request: Request):
    _ensure_line_clients()
    signature = request.headers.get("X-Line-Signature", "")
    body = (await request.body()).decode("utf-8")
    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        if not isinstance(event, MessageEvent):
            continue
        try:
            await _handle_message_event(event)
        except Exception as exc:  # noqa: BLE001 - keep the webhook alive, surface the error to the group
            _logger.exception("_handle_message_event failed")
            try:
                await _make_reply(event.reply_token)(f"發生錯誤了：{exc}")
            except Exception:
                _logger.exception("also failed to report the above error back via reply_token")
    return "OK"


_UNSUPPORTED_MESSAGE_LABELS: list[tuple[type, str]] = [
    (StickerMessageContent, "貼圖"),
    (ImageMessageContent, "圖片"),
    (VideoMessageContent, "影片"),
    (AudioMessageContent, "語音"),
    (LocationMessageContent, "位置"),
]


async def _handle_message_event(event: MessageEvent) -> None:
    conversation_id = _conversation_id(event.source)
    user_id = getattr(event.source, "user_id", "") or ""
    reply = _make_reply(event.reply_token)

    if isinstance(event.message, FileMessageContent):
        file_name = getattr(event.message, "file_name", "") or ""
        content = await line_bot_blob_api.get_message_content(event.message.id)
        push = _make_push(_push_target_id(event.source))
        await handle_pdf_upload(conversation_id, reply, push, content, file_name)
        return

    if not isinstance(event.message, TextMessageContent):
        for msg_type, label in _UNSUPPORTED_MESSAGE_LABELS:
            if isinstance(event.message, msg_type):
                await handle_unsupported_message(conversation_id, reply, label)
                return
        return  # unrecognized message type (e.g. flex/template echoes): stay silent

    async def get_display_name() -> str:
        return await _display_name(event.source, user_id)

    await command_router.handle_text_message(
        conversation_id, user_id, get_display_name, reply, _send_dm, _send_image, _send_dm_image, event.message.text
    )
