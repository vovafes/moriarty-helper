"""Member Messages -- welcome, farewell and boost announcements (text or embed, optional DM)."""

import discord
from discord.ext import commands

from core import embeds, modules
from modules.automations import render

MODULE = modules.register(modules.Module(
    key="welcome", title="Приветствия", icon="👋", category="community",
    description="Сообщения о входе, выходе и бусте сервера. Можно написать новичку в ЛС.",
    fields=[
        {"key": "welcome_channel", "label": "Канал приветствий", "type": "channel", "kind": "text", "default": None, "group": "Приветствие"},
        {"key": "welcome_message", "label": "Текст приветствия", "type": "longtext", "group": "Приветствие",
         "default": "Добро пожаловать, {user}! Нас уже {count}.", "help": "{user} {name} {server} {count}"},
        {"key": "welcome_embed", "label": "Оформить красиво (embed)", "type": "bool", "default": True, "group": "Приветствие"},
        {"key": "dm_message", "label": "Личное сообщение новичку", "type": "longtext", "default": "", "group": "Приветствие",
         "help": "Пусто — не отправлять"},
        {"key": "farewell_channel", "label": "Канал прощаний", "type": "channel", "kind": "text", "default": None, "group": "Прощание"},
        {"key": "farewell_message", "label": "Текст прощания", "type": "longtext", "default": "{name} покинул сервер.", "group": "Прощание"},
        {"key": "boost_channel", "label": "Канал для бустов", "type": "channel", "kind": "text", "default": None, "group": "Бусты"},
        {"key": "boost_message", "label": "Текст о бусте", "type": "longtext", "default": "💎 {user} забустил сервер! Спасибо!", "group": "Бусты"},
    ],
))


class Welcome(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _post(self, guild, channel_id, text, member, *, as_embed=False, color=0x5865F2):
        ch = guild.get_channel(channel_id) if channel_id else None
        if ch is None or not (text or "").strip():
            return
        body = render(text, member=member, guild=guild, channel=ch)
        try:
            if as_embed:
                e = embeds.make(description=body, color=color)
                e.set_thumbnail(url=member.display_avatar.url)
                await ch.send(embed=e, allowed_mentions=discord.AllowedMentions(users=[member]))
            else:
                await ch.send(body, allowed_mentions=discord.AllowedMentions(users=[member]))
        except (discord.Forbidden, discord.HTTPException):
            pass

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        cfg = modules.get_config(member.guild.id, "welcome")
        if not cfg["enabled"] or member.bot:
            return
        await self._post(member.guild, cfg["welcome_channel"], cfg["welcome_message"], member,
                         as_embed=cfg["welcome_embed"], color=0x3BA55D)
        if (cfg["dm_message"] or "").strip():
            try:
                await member.send(render(cfg["dm_message"], member=member, guild=member.guild))
            except (discord.Forbidden, discord.HTTPException):
                pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        cfg = modules.get_config(member.guild.id, "welcome")
        if cfg["enabled"] and not member.bot:
            await self._post(member.guild, cfg["farewell_channel"], cfg["farewell_message"], member, color=0xED4245)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.type != discord.MessageType.premium_guild_subscription:
            return
        cfg = modules.get_config(message.guild.id, "welcome")
        if cfg["enabled"]:
            await self._post(message.guild, cfg["boost_channel"], cfg["boost_message"], message.author, color=0xF47FFF)


async def _test(ctx, p):
    cfg = modules.get_config(ctx.guild.id, "welcome")
    member = ctx.guild.get_member(ctx.user_id) or ctx.guild.me
    key = {"welcome": ("welcome_channel", "welcome_message"), "farewell": ("farewell_channel", "farewell_message"),
           "boost": ("boost_channel", "boost_message")}[p["kind"]]
    ch = ctx.guild.get_channel(cfg[key[0]]) if cfg[key[0]] else None
    if ch is None:
        raise modules.ValidationError("Для этого сообщения не выбран канал (выберите и сохраните)")
    await Welcome(ctx.bot)._post(ctx.guild, cfg[key[0]], cfg[key[1]], member,
                                 as_embed=cfg["welcome_embed"] and p["kind"] == "welcome")
    return f"Пробное сообщение отправлено в #{ch.name}"


MODULE.actions.append(modules.Action("test", "Отправить пробное сообщение", _test, params=[
    {"key": "kind", "label": "Какое", "type": "select", "default": "welcome",
     "options": [{"value": "welcome", "label": "Приветствие"}, {"value": "farewell", "label": "Прощание"}, {"value": "boost", "label": "Буст"}]}],
    description="Покажет, как будет выглядеть сообщение, от вашего имени"))


async def setup(bot):
    await bot.add_cog(Welcome(bot))
