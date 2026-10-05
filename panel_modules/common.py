"""Helpers shared by the legacy-feature panel modules.

These modules expose the old JSON-backed state (legacy/state.py) in the web
panel: `loader` reads the live dicts, `saver` writes them back and calls
save_data(). Everything runs on the bot's event loop, same as the Discord
handlers, so there is no extra locking.
"""

import asyncio
from datetime import timezone, timedelta

import discord

from core import modules
from legacy.app import bot
from legacy.state import save_data

MSK = timezone(timedelta(hours=3))
ImageText = "text"


def put(store: dict, gid: int, value) -> None:
    """Set store[gid]; None / empty list removes it (same as 'not configured')."""
    if value in (None, [], "", {}):
        store.pop(gid, None)
    else:
        store[gid] = value


def spawn(coro) -> None:
    asyncio.get_running_loop().create_task(coro)


def name_of(guild: discord.Guild, uid) -> str:
    try:
        uid = int(uid)
    except (TypeError, ValueError):
        return str(uid)
    m = guild.get_member(uid)
    return f"{m.display_name} ({uid})" if m else f"[{uid}]"


def channel_name(guild: discord.Guild, cid) -> str:
    ch = guild.get_channel(cid) if cid else None
    return f"#{ch.name}" if ch else "—"


def role_name(guild: discord.Guild, rid) -> str:
    r = guild.get_role(rid) if rid else None
    return f"@{r.name}" if r else f"[{rid}]"


def to_epoch(dt) -> int | None:
    """Legacy stores naive MSK datetimes."""
    try:
        return int(dt.replace(tzinfo=MSK).timestamp())
    except Exception:
        return None


def parse_ids(text: str | None) -> list[int]:
    out = []
    for part in (text or "").replace(",", " ").split():
        part = part.strip("<@&#!> ")
        if part.isdigit():
            out.append(int(part))
    return out


# the usual "text + picture" pair every legacy panel has
PANEL_FIELDS = [
    {"key": "text", "label": "Текст панели", "type": "longtext", "default": "", "group": "Оформление панели",
     "help": "Пусто — стандартный текст"},
    {"key": "image_url", "label": "Картинка (ссылка)", "type": "text", "default": "", "group": "Оформление панели"},
]


async def publish_panel(guild: discord.Guild, channel_id: int, store: dict, builder, view, *,
                        channel_key="channel_id", message_key="message_id") -> str:
    """Delete the previous panel message (if any), post a fresh one, remember it.
    Keeps the panel's custom text/image like the slash commands do."""
    channel = guild.get_channel(channel_id)
    if channel is None or not hasattr(channel, "send"):
        raise modules.ValidationError("Нужен текстовый канал")
    prev = store.get(guild.id) or {}
    if prev.get(message_key):
        try:
            old_ch = guild.get_channel(prev[channel_key])
            await (await old_ch.fetch_message(prev[message_key])).delete()
        except Exception:
            pass
    try:
        msg = await channel.send(embed=builder(guild.id), view=view)
    except discord.Forbidden:
        raise modules.ValidationError("У бота нет прав писать в этот канал")
    store[guild.id] = {**prev, channel_key: channel.id, message_key: msg.id}
    save_data()
    return f"Панель опубликована в #{channel.name}"
