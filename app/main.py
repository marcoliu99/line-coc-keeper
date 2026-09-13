"""FastAPI webhook server — the LINE-specific adapter.

Game/command logic lives in app/commands.py; this module only translates LINE's
webhook payloads and SDK objects into calls against that shared layer. See
app/discord_bot.py for the Discord equivalent.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request

from linebot.v3 import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    AsyncMessagingApiBlob,
    Configuration,
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

from app import commands
from app.config import LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET

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


def _make_reply(reply_token: str) -> commands.Reply:
    async def reply(text: str) -> None:
        messages = [TextMessage(text=c) for c in _chunk_text(text)]
        await line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(reply_token=reply_token, messages=messages)
        )

    return reply


def _conversation_id(source) -> str:
    if isinstance(source, GroupSource):
        return f"line-group-{source.group_id}"
    if isinstance(source, RoomSource):
        return f"line-room-{source.room_id}"
    if isinstance(source, UserSource):
        return f"line-user-{source.user_id}"
    return "line-unknown"


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
            try:
                await _make_reply(event.reply_token)(f"發生錯誤了：{exc}")
            except Exception:
                pass
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
        await commands.handle_pdf_upload(conversation_id, reply, content, file_name)
        return

    if not isinstance(event.message, TextMessageContent):
        for msg_type, label in _UNSUPPORTED_MESSAGE_LABELS:
            if isinstance(event.message, msg_type):
                await commands.handle_unsupported_message(conversation_id, reply, label)
                return
        return  # unrecognized message type (e.g. flex/template echoes): stay silent

    async def get_display_name() -> str:
        return await _display_name(event.source, user_id)

    await commands.handle_text_message(conversation_id, user_id, get_display_name, reply, event.message.text)
