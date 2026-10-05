"""
Logging -- server event log split across channels (messages / members /
moderation / voice / server). Each event type can be switched off on its own;
bots and chosen channels can be ignored. Works next to the legacy per-feature
logs in main.py (tickets, shop, warns, ...) -- those are unchanged.
"""

import datetime

import discord
from discord.ext import commands

from core import embeds, modules

GREEN, RED, YELLOW, BLUE, GREY = 0x3BA55D, 0xED4245, 0xFEE75C, 0x5865F2, 0x99AAB5

CHANNEL_FIELDS = [
    {"key": "messages_channel", "label": "Канал: сообщения", "type": "channel", "kind": "text", "default": None,
     "help": "Удаление и редактирование сообщений"},
    {"key": "members_channel", "label": "Канал: участники", "type": "channel", "kind": "text", "default": None,
     "help": "Вход, выход, смена ника и ролей"},
    {"key": "moderation_channel", "label": "Канал: модерация", "type": "channel", "kind": "text", "default": None,
     "help": "Баны, разбаны, таймауты"},
    {"key": "voice_channel", "label": "Канал: голосовые", "type": "channel", "kind": "text", "default": None},
    {"key": "server_channel", "label": "Канал: сервер", "type": "channel", "kind": "text", "default": None,
     "help": "Создание и удаление каналов и ролей"},
]
EVENT_FIELDS = [
    ("log_message_delete", "Удаление сообщений"), ("log_message_edit", "Редактирование сообщений"),
    ("log_member_join", "Вход участников"), ("log_member_leave", "Выход участников"),
    ("log_member_nick", "Смена ника"), ("log_member_roles", "Изменение ролей участника"),
    ("log_ban", "Баны и разбаны"), ("log_timeout", "Таймауты"),
    ("log_voice", "Вход/выход/перемещение в голосовых"),
    ("log_channels", "Создание/удаление каналов"), ("log_roles", "Создание/удаление ролей"),
]

modules.register(modules.Module(
    key="logging", title="Логи", icon="📜", category="moderation",
    description="Журнал событий сервера по отдельным каналам: сообщения, участники, модерация, голосовые.",
    fields=CHANNEL_FIELDS
    + [{"key": k, "label": label, "type": "bool", "default": True} for k, label in EVENT_FIELDS]
    + [{"key": "ignore_bots", "label": "Игнорировать ботов", "type": "bool", "default": True},
       {"key": "ignore_channels", "label": "Игнорировать каналы", "type": "channels", "default": [],
        "help": "Сообщения из этих каналов не логируются"}],
))

CATEGORY_OF = {
    "log_message_delete": "messages_channel", "log_message_edit": "messages_channel",
    "log_member_join": "members_channel", "log_member_leave": "members_channel",
    "log_member_nick": "members_channel", "log_member_roles": "members_channel",
    "log_ban": "moderation_channel", "log_timeout": "moderation_channel",
    "log_voice": "voice_channel", "log_channels": "server_channel", "log_roles": "server_channel",
}


def _fmt_user(u) -> str:
    return f"{u.mention} (`{u}`, `{u.id}`)"


class ServerLogging(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── plumbing ────────────────────────────────────────────────────────────
    def _target(self, guild: discord.Guild, event: str):
        cfg = modules.get_config(guild.id, "logging")
        if not cfg["enabled"] or not cfg.get(event):
            return None, cfg
        ch_id = cfg.get(CATEGORY_OF[event])
        ch = guild.get_channel(ch_id) if ch_id else None
        return ch, cfg

    async def _send(self, guild, event: str, embed: discord.Embed) -> None:
        ch, _ = self._target(guild, event)
        if ch is None:
            return
        try:
            await ch.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _audit_actor(self, guild, action: discord.AuditLogAction, target_id: int):
        """Best effort: who did it, per the audit log (needs View Audit Log)."""
        try:
            async for entry in guild.audit_logs(limit=6, action=action,
                                                after=discord.utils.utcnow() - datetime.timedelta(seconds=15)):
                if getattr(entry.target, "id", None) == target_id:
                    return entry.user, entry.reason
        except (discord.Forbidden, discord.HTTPException):
            pass
        return None, None

    def _skip_message(self, message: discord.Message, cfg: dict) -> bool:
        if message.guild is None:
            return True
        if cfg["ignore_bots"] and message.author.bot:
            return True
        return message.channel.id in (cfg["ignore_channels"] or [])

    # ── messages ────────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.guild is None:
            return
        ch, cfg = self._target(message.guild, "log_message_delete")
        if ch is None or self._skip_message(message, cfg):
            return
        e = embeds.make("🗑 Сообщение удалено", color=RED)
        e.add_field(name="Автор", value=_fmt_user(message.author), inline=False)
        e.add_field(name="Канал", value=message.channel.mention)
        e.add_field(name="Текст", value=embeds.clip(message.content) or "*пусто*", inline=False)
        if message.attachments:
            e.add_field(name="Вложения", value="\n".join(a.filename for a in message.attachments)[:1000], inline=False)
        await self._send(message.guild, "log_message_delete", e)

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]):
        if not messages or messages[0].guild is None:
            return
        e = embeds.make("🧹 Массовое удаление", f"Удалено сообщений: **{len(messages)}** в {messages[0].channel.mention}", RED)
        await self._send(messages[0].guild, "log_message_delete", e)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.guild is None or before.content == after.content:
            return
        ch, cfg = self._target(after.guild, "log_message_edit")
        if ch is None or self._skip_message(after, cfg):
            return
        e = embeds.make("✏️ Сообщение изменено", f"[Перейти к сообщению]({after.jump_url})", YELLOW)
        e.add_field(name="Автор", value=_fmt_user(after.author), inline=False)
        e.add_field(name="Было", value=embeds.clip(before.content) or "*пусто*", inline=False)
        e.add_field(name="Стало", value=embeds.clip(after.content) or "*пусто*", inline=False)
        await self._send(after.guild, "log_message_edit", e)

    # ── members ─────────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        e = embeds.make("📥 Участник зашёл", _fmt_user(member), GREEN)
        e.add_field(name="Аккаунт создан", value=discord.utils.format_dt(member.created_at, "R"))
        e.set_thumbnail(url=member.display_avatar.url)
        await self._send(member.guild, "log_member_join", e)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        e = embeds.make("📤 Участник вышел", _fmt_user(member), RED)
        roles = [r.mention for r in member.roles if not r.is_default()]
        if roles:
            e.add_field(name="Роли", value=embeds.clip(" ".join(roles)), inline=False)
        e.set_thumbnail(url=member.display_avatar.url)
        await self._send(member.guild, "log_member_leave", e)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.nick != after.nick:
            e = embeds.make("🏷 Ник изменён", _fmt_user(after), BLUE)
            e.add_field(name="Было", value=before.nick or "*нет*")
            e.add_field(name="Стало", value=after.nick or "*нет*")
            await self._send(after.guild, "log_member_nick", e)
        if before.roles != after.roles:
            added = [r for r in after.roles if r not in before.roles]
            removed = [r for r in before.roles if r not in after.roles]
            if added or removed:
                e = embeds.make("🎭 Роли изменены", _fmt_user(after), BLUE)
                if added:
                    e.add_field(name="Добавлены", value=" ".join(r.mention for r in added), inline=False)
                if removed:
                    e.add_field(name="Сняты", value=" ".join(r.mention for r in removed), inline=False)
                await self._send(after.guild, "log_member_roles", e)
        if before.timed_out_until != after.timed_out_until:
            if after.timed_out_until:
                e = embeds.make("🔇 Таймаут выдан", _fmt_user(after), YELLOW)
                e.add_field(name="До", value=discord.utils.format_dt(after.timed_out_until, "f"))
            else:
                e = embeds.make("🔊 Таймаут снят", _fmt_user(after), GREEN)
            await self._send(after.guild, "log_timeout", e)

    # ── moderation ──────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        actor, reason = await self._audit_actor(guild, discord.AuditLogAction.ban, user.id)
        e = embeds.make("🔨 Бан", _fmt_user(user), RED)
        if actor:
            e.add_field(name="Модератор", value=_fmt_user(actor))
        if reason:
            e.add_field(name="Причина", value=embeds.clip(reason, 500), inline=False)
        await self._send(guild, "log_ban", e)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        actor, _ = await self._audit_actor(guild, discord.AuditLogAction.unban, user.id)
        e = embeds.make("♻️ Разбан", _fmt_user(user), GREEN)
        if actor:
            e.add_field(name="Модератор", value=_fmt_user(actor))
        await self._send(guild, "log_ban", e)

    # ── voice ───────────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if before.channel == after.channel:
            return
        if modules.get_config(member.guild.id, "logging")["ignore_bots"] and member.bot:
            return
        if before.channel is None:
            e = embeds.make("🎙 Зашёл в голосовой", f"{_fmt_user(member)}\n→ {after.channel.mention}", GREEN)
        elif after.channel is None:
            e = embeds.make("🔌 Вышел из голосового", f"{_fmt_user(member)}\n← {before.channel.mention}", RED)
        else:
            e = embeds.make("🔀 Перешёл между каналами", f"{_fmt_user(member)}\n{before.channel.mention} → {after.channel.mention}", BLUE)
        await self._send(member.guild, "log_voice", e)

    # ── server structure ────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        await self._send(channel.guild, "log_channels", embeds.make("📁 Канал создан", f"{channel.mention} (`{channel.name}`)", GREEN))

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        await self._send(channel.guild, "log_channels", embeds.make("🗑 Канал удалён", f"`{channel.name}`", RED))

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        await self._send(role.guild, "log_roles", embeds.make("🎭 Роль создана", f"{role.mention} (`{role.name}`)", GREEN))

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        await self._send(role.guild, "log_roles", embeds.make("🗑 Роль удалена", f"`{role.name}`", RED))


async def setup(bot):
    await bot.add_cog(ServerLogging(bot))
