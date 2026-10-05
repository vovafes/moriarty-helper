"""app -- split out of main.py."""

from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from dotenv import load_dotenv

load_dotenv()


MSK = timezone(timedelta(hours=3))


def now_msk() -> datetime:
    """Текущее время по МСК."""
    return datetime.now(MSK).replace(tzinfo=None)


intents = discord.Intents.default()


intents.message_content = True


intents.members = True


intents.presences = True


intents.reactions = True


bot = commands.Bot(command_prefix="!", intents=intents)


tree = bot.tree
