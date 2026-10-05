"""
Sticky messages -- a message that keeps itself at the bottom of a channel.
After the channel has been quiet for a few seconds the bot deletes its old
copy and posts it again, so busy channels aren't spammed.
"""

import asyncio

import discord
from discord.ext import commands

from core import db, embeds, modules

db.register_schema("""
CREATE TABLE IF NOT EXISTS sticky (
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    text        TEXT NOT NULL,
    last_msg_id INTEGER,
    PRIMARY KEY (guild_id, channel_id)
);
""")

MODULE = modules.register(modules.Module(
    key="sticky", title="Закреплённые сообщения", icon="📌", category="community",
    description="Сообщение, которое всегда остаётся внизу канала (например, правила или напоминание).",
    fields=[{"key": "delay", "label": "Пауза перед повтором, сек", "type": "number", "default": 4, "min": 1, "max": 120,
             "help": "Бот обновляет сообщение, когда в канале затихло на столько секунд"}],
    tables=[{"id": "list", "title": "Закреплённые", "columns": [{"key": "channel", "label": "Канал"}, {"key": "text", "label": "Текст"}]}],
))


def get_for(guild_id, channel_id) -> dict | None:
    rows = db.query("SELECT * FROM sticky WHERE guild_id=? AND channel_id=?", (guild_id, channel_id))
    return dict(rows[0]) if rows else None


def set_last(guild_id, channel_id, msg_id) -> None:
    db.execute("UPDATE sticky SET last_msg_id=? WHERE guild_id=? AND channel_id=?", (msg_id, guild_id, channel_id))


class Sticky(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._pending: dict[int, asyncio.Task] = {}

    async def repost(self, guild: discord.Guild, channel) -> None:
        row = await db.run(get_for, guild.id, channel.id)
        if row is None:
            return
        if row["last_msg_id"]:
            try:
                await (await channel.fetch_message(row["last_msg_id"])).delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        try:
            msg = await channel.send(embed=embeds.make("📌 Закреплено", row["text"], 0xF1C40F))
        except (discord.Forbidden, discord.HTTPException):
            return
        await db.run(set_last, guild.id, channel.id, msg.id)

    async def _later(self, guild, channel, delay: float):
        try:
            await asyncio.sleep(delay)
            await self.repost(guild, channel)
        except asyncio.CancelledError:
            raise
        finally:
            if self._pending.get(channel.id) is asyncio.current_task():
                self._pending.pop(channel.id, None)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.id == self.bot.user.id:
            return
        cfg = modules.get_config(message.guild.id, "sticky")
        if not cfg["enabled"] or await db.run(get_for, message.guild.id, message.channel.id) is None:
            return
        task = self._pending.get(message.channel.id)
        if task:
            task.cancel()                                   # still chatty -> wait for silence
        self._pending[message.channel.id] = asyncio.create_task(self._later(message.guild, message.channel, cfg["delay"]))


def _table(guild):
    return [{"channel": f"#{guild.get_channel(r['channel_id']).name}" if guild.get_channel(r["channel_id"]) else f"[{r['channel_id']}]",
             "text": embeds.clip(r["text"], 120)} for r in db.query("SELECT * FROM sticky WHERE guild_id=?", (guild.id,))]


modules.register_table("sticky", "list", _table)


async def _act_set(ctx, p):
    ch = ctx.guild.get_channel(p["channel"])
    if ch is None or not hasattr(ch, "send"):
        raise modules.ValidationError("Выберите текстовый канал")
    await db.run(db.execute, "INSERT INTO sticky (guild_id, channel_id, text) VALUES (?,?,?) "
                 "ON CONFLICT(guild_id, channel_id) DO UPDATE SET text=excluded.text", (ctx.guild.id, ch.id, p["text"]))
    await Sticky(ctx.bot).repost(ctx.guild, ch)
    return f"Сообщение закреплено в #{ch.name}"


async def _act_remove(ctx, p):
    row = await db.run(get_for, ctx.guild.id, p["channel"])
    if row is None:
        raise modules.ValidationError("В этом канале нет закреплённого сообщения")
    ch = ctx.guild.get_channel(p["channel"])
    if ch and row["last_msg_id"]:
        try:
            await (await ch.fetch_message(row["last_msg_id"])).delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
    await db.run(db.execute, "DELETE FROM sticky WHERE guild_id=? AND channel_id=?", (ctx.guild.id, p["channel"]))
    return "Закреплённое сообщение убрано"


MODULE.actions.extend([
    modules.Action("set", "Закрепить сообщение", _act_set, params=[
        {"key": "channel", "label": "Канал", "type": "channel", "kind": "text"}, {"key": "text", "label": "Текст", "type": "longtext"}]),
    modules.Action("remove", "Убрать закреп", _act_remove, params=[{"key": "channel", "label": "Канал", "type": "channel", "kind": "text"}], danger=True),
])


async def setup(bot):
    await bot.add_cog(Sticky(bot))
