"""
Guards -- three small protections in one cog:

* Honeypot: a channel nobody should write in; whoever does is punished (and the
  message removed) -- catches spam bots and compromised accounts.
* Account age: kick brand-new accounts on join (raid / alt protection).
* Anti ghost ping: when someone mentions people and deletes the message, the bot
  says so.
Members with Administrator / Manage Messages are never touched by honeypot.
"""

import datetime
import time

import discord
from discord.ext import commands

from core import embeds, modules

HONEYPOT = modules.register(modules.Module(
    key="honeypot", title="Ловушка (honeypot)", icon="🍯", category="moderation", default_enabled=False,
    description="Канал-приманка: кто в него напишет, того бот накажет. Ловит спам-ботов и взломанные аккаунты.",
    fields=[
        {"key": "channel", "label": "Канал-ловушка", "type": "channel", "kind": "text", "default": None,
         "help": "Лучше скрыть его от обычных участников «в лоб» нельзя — иначе ловушка не сработает. Назовите его заманчиво и подпишите, что писать нельзя"},
        {"key": "action", "label": "Наказание", "type": "select", "default": "timeout",
         "options": [{"value": "timeout", "label": "Таймаут на сутки"}, {"value": "kick", "label": "Кик"}, {"value": "ban", "label": "Бан"}]},
        {"key": "purge_hours", "label": "Удалить сообщения нарушителя за N часов", "type": "number", "default": 1, "min": 0, "max": 168,
         "help": "При бане — Discord удаляет сообщения сам (до 7 дней); при кике и таймауте — чистится канал-ловушка"},
        {"key": "log_channel", "label": "Канал логов", "type": "channel", "kind": "text", "default": None},
    ],
))
ACCOUNT_AGE = modules.register(modules.Module(
    key="account_age", title="Возраст аккаунта", icon="🪪", category="moderation", default_enabled=False,
    description="Кикает слишком новые аккаунты при входе на сервер (защита от рейдов и мультиаккаунтов).",
    fields=[
        {"key": "min_days", "label": "Минимальный возраст, дней", "type": "number", "default": 7, "min": 1, "max": 365},
        {"key": "dm_text", "label": "Сообщение перед киком", "type": "text",
         "default": "Ваш аккаунт слишком новый для этого сервера. Вернитесь, когда ему исполнится {days} дн."},
        {"key": "log_channel", "label": "Канал логов", "type": "channel", "kind": "text", "default": None},
    ],
))
GHOST = modules.register(modules.Module(
    key="ghost_ping", title="Анти-призрачные пинги", icon="👻", category="moderation", default_enabled=False,
    description="Если кого-то упомянули и сразу удалили сообщение, бот сообщит об этом в канале.",
    fields=[{"key": "max_age", "label": "Сообщение «свежее», если младше, сек", "type": "number", "default": 120, "min": 5, "max": 3600,
             "help": "Старые сообщения с упоминаниями не считаются призрачным пингом"}],
))


def is_account_too_new(created_at: datetime.datetime, min_days: int, now: datetime.datetime | None = None) -> bool:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return (now - created_at) < datetime.timedelta(days=min_days)


def ghost_targets(message) -> list[str]:
    """Mentions worth reporting (self-mentions and bots don't count)."""
    out = [m.mention for m in message.mentions if not m.bot and m.id != message.author.id]
    out += [r.mention for r in message.role_mentions]
    return out


class Guards(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _log(self, guild, channel_id, embed):
        ch = guild.get_channel(channel_id) if channel_id else None
        if ch:
            try:
                await ch.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException):
                pass

    # ── honeypot ────────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or not hasattr(message.author, "guild_permissions"):   # webhooks / departed users
            return
        cfg = modules.get_config(message.guild.id, "honeypot")
        if not cfg["enabled"] or not cfg["channel"] or message.channel.id != cfg["channel"]:
            return
        perms = message.author.guild_permissions
        if perms.administrator or perms.manage_messages:
            return
        guild, member = message.guild, message.author
        result = "сообщение удалено"
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass
        reason = "Honeypot: написал в канал-ловушку"
        try:
            if cfg["action"] == "ban":
                await guild.ban(member, reason=reason, delete_message_seconds=min(cfg["purge_hours"], 168) * 3600)
                result = "бан"
            elif cfg["action"] == "kick":
                await guild.kick(member, reason=reason)
                result = "кик"
            else:
                await member.timeout(datetime.timedelta(days=1), reason=reason)
                result = "таймаут на сутки"
            if cfg["action"] != "ban" and cfg["purge_hours"]:
                since = discord.utils.utcnow() - datetime.timedelta(hours=cfg["purge_hours"])
                await message.channel.purge(limit=200, after=since, check=lambda m: m.author.id == member.id, reason=reason)
        except (discord.Forbidden, discord.HTTPException):
            result += " (наказание не применено: нет прав или роль выше)"
        e = embeds.make("🍯 Honeypot", color=0xE67E22)
        e.add_field(name="Кто", value=f"{member.mention} (`{member.id}`)"); e.add_field(name="Итог", value=result, inline=False)
        e.add_field(name="Сообщение", value=embeds.clip(message.content, 500) or "*без текста*", inline=False)
        await self._log(guild, cfg["log_channel"], e)

    # ── account age ─────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        cfg = modules.get_config(member.guild.id, "account_age")
        if not cfg["enabled"] or member.bot or not is_account_too_new(member.created_at, cfg["min_days"]):
            return
        try:
            await member.send((cfg["dm_text"] or "").replace("{days}", str(cfg["min_days"])))
        except (discord.Forbidden, discord.HTTPException):
            pass
        result = "кикнут"
        try:
            await member.kick(reason=f"Аккаунт младше {cfg['min_days']} дн.")
        except (discord.Forbidden, discord.HTTPException):
            result = "кик не удался (нет прав)"
        e = embeds.make("🪪 Новый аккаунт", f"{member.mention} (`{member.id}`) — {result}", 0xE67E22)
        e.add_field(name="Создан", value=discord.utils.format_dt(member.created_at, "R"))
        await self._log(member.guild, cfg["log_channel"], e)

    # ── ghost pings ─────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        cfg = modules.get_config(message.guild.id, "ghost_ping")
        if not cfg["enabled"]:
            return
        age = (discord.utils.utcnow() - message.created_at).total_seconds()
        targets = ghost_targets(message)
        if not targets or age > cfg["max_age"]:
            return
        try:
            await message.channel.send(
                embed=embeds.make("👻 Призрачный пинг", f"{message.author.mention} упомянул {', '.join(targets)} и удалил сообщение.", 0x99AAB5),
                allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            pass


async def setup(bot):
    await bot.add_cog(Guards(bot))
