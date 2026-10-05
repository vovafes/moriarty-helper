"""
Starboard -- messages that collect enough ⭐ reactions are copied to a "best of"
channel; the copy's counter follows the reactions and the copy disappears if
the count falls back below the threshold. Self-stars and bots don't count
(self-stars optionally).
"""

import discord
from discord.ext import commands

from core import db, embeds, modules

db.register_schema("""
CREATE TABLE IF NOT EXISTS starboard (
    guild_id        INTEGER NOT NULL,
    message_id      INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    author_id       INTEGER NOT NULL,
    star_message_id INTEGER,
    stars           INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, message_id)
);
""")

MODULE = modules.register(modules.Module(
    key="starboard", title="Starboard", icon="🌟", category="community",
    description="Лучшие сообщения (набравшие нужное число звёзд) попадают в отдельный канал.",
    fields=[
        {"key": "channel", "label": "Канал Starboard", "type": "channel", "kind": "text", "default": None},
        {"key": "emoji", "label": "Эмодзи", "type": "text", "default": "⭐"},
        {"key": "threshold", "label": "Сколько нужно реакций", "type": "number", "default": 3, "min": 1, "max": 100},
        {"key": "allow_self", "label": "Учитывать реакцию автора", "type": "bool", "default": False},
        {"key": "ignored_channels", "label": "Каналы-исключения", "type": "channels", "default": []},
    ],
    tables=[{"id": "top", "title": "Лучшие сообщения", "columns": [
        {"key": "stars", "label": "⭐"}, {"key": "author", "label": "Автор"}, {"key": "channel", "label": "Канал"}, {"key": "link", "label": "Сообщение", "format": "link"}]}],
))


def should_post(stars: int, threshold: int) -> bool:
    return stars >= threshold


def get(guild_id, message_id) -> dict | None:
    rows = db.query("SELECT * FROM starboard WHERE guild_id=? AND message_id=?", (guild_id, message_id))
    return dict(rows[0]) if rows else None


def upsert(guild_id, message_id, channel_id, author_id, star_message_id, stars) -> None:
    db.execute("INSERT INTO starboard (guild_id, message_id, channel_id, author_id, star_message_id, stars) VALUES (?,?,?,?,?,?) "
               "ON CONFLICT(guild_id, message_id) DO UPDATE SET star_message_id=excluded.star_message_id, stars=excluded.stars",
               (guild_id, message_id, channel_id, author_id, star_message_id, stars))


def drop(guild_id, message_id) -> None:
    db.execute("DELETE FROM starboard WHERE guild_id=? AND message_id=?", (guild_id, message_id))


def build_embed(message, stars: int) -> discord.Embed:
    e = embeds.make(description=embeds.clip(message.content, 1900), color=0xF1C40F)
    e.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
    e.add_field(name="Источник", value=f"[Перейти к сообщению]({message.jump_url})")
    img = next((a.url for a in message.attachments if (a.content_type or "").startswith("image/")), None)
    if img:
        e.set_image(url=img)
    return e


async def count_stars(message: discord.Message, emoji: str, allow_self: bool) -> int:
    reaction = next((r for r in message.reactions if str(r.emoji) == emoji), None)
    if reaction is None:
        return 0
    users = [u async for u in reaction.users(limit=200)]
    return len({u.id for u in users if not u.bot and (allow_self or u.id != message.author.id)})


class Starboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def refresh(self, guild: discord.Guild, channel, message_id: int) -> None:
        cfg = modules.get_config(guild.id, "starboard")
        board = guild.get_channel(cfg["channel"]) if cfg["channel"] else None
        if not cfg["enabled"] or board is None or channel.id in (cfg["ignored_channels"] or []) or channel.id == board.id:
            return
        if getattr(channel, "nsfw", False) and not getattr(board, "nsfw", False):
            return
        try:
            message = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
        stars = await count_stars(message, cfg["emoji"], cfg["allow_self"])
        existing = await db.run(get, guild.id, message_id)
        label = f"{cfg['emoji']} **{stars}** · {channel.mention}"
        if should_post(stars, cfg["threshold"]):
            if existing and existing["star_message_id"]:
                try:
                    await (await board.fetch_message(existing["star_message_id"])).edit(content=label)
                    await db.run(upsert, guild.id, message_id, channel.id, message.author.id, existing["star_message_id"], stars)
                    return
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass                                    # the copy was deleted by hand -> post a fresh one
            try:
                copy = await board.send(label, embed=build_embed(message, stars), allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException):
                return
            await db.run(upsert, guild.id, message_id, channel.id, message.author.id, copy.id, stars)
        elif existing:
            if existing["star_message_id"]:
                try:
                    await (await board.fetch_message(existing["star_message_id"])).delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
            await db.run(drop, guild.id, message_id)

    async def _on_reaction(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.user_id == self.bot.user.id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None or str(payload.emoji) != modules.get_config(guild.id, "starboard")["emoji"]:
            return
        channel = guild.get_channel(payload.channel_id)
        if channel is not None:
            await self.refresh(guild, channel, payload.message_id)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        await self._on_reaction(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        await self._on_reaction(payload)


def _table(guild):
    out = []
    for r in db.query("SELECT * FROM starboard WHERE guild_id=? ORDER BY stars DESC LIMIT 100", (guild.id,)):
        m, ch = guild.get_member(r["author_id"]), guild.get_channel(r["channel_id"])
        out.append({"stars": r["stars"], "author": m.display_name if m else f"[{r['author_id']}]",
                    "channel": f"#{ch.name}" if ch else "—",
                    "link": f"https://discord.com/channels/{guild.id}/{r['channel_id']}/{r['message_id']}"})
    return out


modules.register_table("starboard", "top", _table)


async def setup(bot):
    await bot.add_cog(Starboard(bot))
