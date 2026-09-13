"""Discord adapter — the discord.py-based front-end.

Game/command logic lives in app/commands.py; this module only translates
Discord events into calls against that shared layer. Run it as its own process
(`python -m app.discord_bot`), separate from the LINE FastAPI server — discord.py
owns a persistent gateway connection rather than serving HTTP requests, so
there's no webhook URL or ngrok tunnel needed for this adapter at all.
"""
from __future__ import annotations

import io

import discord

from app import commands
from app.config import DISCORD_BOT_TOKEN

MAX_DISCORD_MESSAGE_CHARS = 1900  # Discord's hard limit is 2000; leave a margin
MAX_REPLY_MESSAGES = 10

intents = discord.Intents.default()
intents.message_content = True  # privileged intent — must also be switched on
# for this bot under Developer Portal > your app > Bot > Privileged Gateway Intents,
# or on_message will only ever see empty message content.
client = discord.Client(intents=intents)


def _chunk_text(text: str) -> list[str]:
    text = text.strip() or "（沒有內容）"
    chunks = [text[i : i + MAX_DISCORD_MESSAGE_CHARS] for i in range(0, len(text), MAX_DISCORD_MESSAGE_CHARS)]
    return chunks[:MAX_REPLY_MESSAGES]


def _make_reply(channel: discord.abc.Messageable) -> commands.Reply:
    async def reply(text: str) -> None:
        for chunk in _chunk_text(text):
            await channel.send(chunk)

    return reply


def _conversation_id(channel_id: int) -> str:
    return f"discord-channel-{channel_id}"


async def _send_dm(owner_id: str, text: str) -> None:
    # owner_id is str(discord.Member.id), as stored on Character.owner_id. Raises
    # if the user has DMs from server members disabled; commands.py swallows
    # that (see its docstring on why it doesn't fall back to posting publicly).
    user = client.get_user(int(owner_id)) or await client.fetch_user(int(owner_id))
    for chunk in _chunk_text(text):
        await user.send(chunk)


def _make_send_image(channel: discord.abc.Messageable) -> commands.SendImage:
    async def send_image(png_bytes: bytes, conversation_id: str, page_number: int) -> None:
        # conversation_id/page_number are part of the shared SendImage signature
        # (LINE's adapter needs them to build a URL) but unused here — Discord
        # just attaches the bytes directly.
        await channel.send(file=discord.File(io.BytesIO(png_bytes), filename=f"page_{page_number}.png"))

    return send_image


async def _send_dm_image(owner_id: str, png_bytes: bytes, conversation_id: str, page_number: int) -> None:
    user = client.get_user(int(owner_id)) or await client.fetch_user(int(owner_id))
    await user.send(file=discord.File(io.BytesIO(png_bytes), filename=f"page_{page_number}.png"))


@client.event
async def on_ready() -> None:
    print(f"Discord bot 已上線：{client.user}")


@client.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return  # ignore other bots (and echoes of our own messages)

    conversation_id = _conversation_id(message.channel.id)
    user_id = str(message.author.id)
    reply = _make_reply(message.channel)
    send_image = _make_send_image(message.channel)

    async def get_display_name() -> str:
        return message.author.display_name

    try:
        pdf_attachments = [a for a in message.attachments if a.filename.lower().endswith(".pdf")]
        if pdf_attachments:
            attachment = pdf_attachments[0]
            content = await attachment.read()
            # No reply-token/time-window constraint here, so the same callback
            # serves as both the immediate ack and the final result.
            await commands.handle_pdf_upload(conversation_id, reply, reply, content, attachment.filename)
            return

        text = (message.content or "").strip()
        if not text:
            if message.attachments:
                await commands.handle_unsupported_message(conversation_id, reply, "附件")
            return

        await commands.handle_text_message(
            conversation_id, user_id, get_display_name, reply, _send_dm, send_image, _send_dm_image, text
        )
    except Exception as exc:  # noqa: BLE001 - keep the bot alive, surface the error to the channel
        try:
            await reply(f"發生錯誤了：{exc}")
        except Exception:
            pass


def main() -> None:
    if not DISCORD_BOT_TOKEN:
        raise SystemExit("尚未設定 DISCORD_BOT_TOKEN，請檢查 .env")
    client.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
