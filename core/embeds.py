"""Shared embed helpers so every module keeps the project's MORIARTY footer."""

import discord

FOOTER = "MORIARTY"


def make(title: str = "", description: str = "", color: int | discord.Color = 0x5865F2) -> discord.Embed:
    e = discord.Embed(title=title or None, description=description or None, color=color)
    e.set_footer(text=FOOTER)
    e.timestamp = discord.utils.utcnow()
    return e


def clip(text: str | None, limit: int = 1000) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"
