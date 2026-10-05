"""The one Discord client object and its intents."""
from __future__ import annotations

import discord

intents = discord.Intents.default()
intents.message_content = True  # privileged intent — must also be switched on
# for this bot under Developer Portal > your app > Bot > Privileged Gateway Intents,
# or on_message will only ever see empty message content.
client = discord.Client(intents=intents)
