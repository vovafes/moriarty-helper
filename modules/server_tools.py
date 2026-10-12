"""
Server tools (ported from the AlphaCloud panel):

* Auto Slowmode    -- raises the slowmode while chat is busy, lowers it when calm.
* Temp voice       -- join a "creator" channel and get your own voice channel.
* Role DMs         -- DM a member when they get / lose a role.
* Auto publish     -- crosspost messages in announcement channels.
* Auto ping        -- ghost-ping new members in chosen channels so they show as unread.
* Auto responder   -- reply when a message contains a trigger phrase.
* Custom commands  -- /cmd <name>: canned replies managed from the panel.
"""

import asyncio
import collections
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import db, embeds, modules
from core.tools_group import install as install_tools, tool, uninstall as uninstall_tools
from modules.automations import render

db.register_schema("""
CREATE TABLE IF NOT EXISTS temp_vc (
    channel_id INTEGER PRIMARY KEY,
    guild_id   INTEGER NOT NULL,
    owner_id   INTEGER NOT NULL,
    created_ts INTEGER NOT NULL
);
""")

SLOW_STEPS = [5, 10, 15, 30, 60, 120, 300, 600]
SLOW_OPTIONS = [{"value": s, "label": (f"{s} с" if s < 60 else f"{s // 60} мин")} for s in SLOW_STEPS]

AUTO_SLOWMODE = modules.register(modules.Module(
    key="auto_slowmode", title="Auto Slowmode", icon="🐌", category="moderation", default_enabled=False,
    description="Поднимает slowmode, пока в канале много пишут, и снижает его, когда всё стихает.",
    fields=[
        {"key": "channels", "label": "Следить за каналами (до 10)", "type": "channels", "kind": "text", "default": [], "group": "Каналы"},
        {"key": "busy_from", "label": "«Горячо» от (сообщений в минуту)", "type": "number", "default": 20, "min": 3, "max": 500, "group": "Поведение"},
        {"key": "max_slowmode", "label": "Максимальный slowmode, сек", "type": "select", "default": 30, "options": SLOW_OPTIONS, "group": "Поведение"},
        {"key": "announce", "label": "Сообщать в канале", "type": "bool", "default": True, "group": "Поведение",
         "help": "Короткое «slowmode теперь 30 секунд»; прошлое уведомление удаляется"},
    ],
))

TEMP_VOICE = modules.register(modules.Module(
    key="temp_voice", title="Временные голосовые", icon="🔊", category="community", default_enabled=False,
    description="Участник заходит в канал-создатель — бот делает ему личный голосовой канал и удаляет, когда он пустеет.",
    fields=[
        {"key": "creators", "label": "Каналы-создатели (до 5)", "type": "channels", "kind": "voice", "default": [], "group": "Каналы"},
        {"key": "name_template", "label": "Название нового канала", "type": "text", "default": "Канал {name}", "group": "Каналы",
         "help": "{name} — ник владельца"},
        {"key": "user_limit", "label": "Лимит участников (0 — без лимита)", "type": "number", "default": 0, "min": 0, "max": 99, "group": "Каналы"},
        {"key": "log_channel", "label": "Канал логов", "type": "channel", "kind": "text", "default": None, "group": "Логи"},
    ],
    tables=[{"id": "active", "title": "Активные каналы", "columns": [
        {"key": "name", "label": "Канал"}, {"key": "owner", "label": "Владелец"}, {"key": "members", "label": "Внутри"},
        {"key": "created_ts", "label": "Создан", "format": "time"}]}],
))

ROLE_DM = modules.register(modules.Module(
    key="role_dm", title="ЛС по ролям", icon="✉️", category="community", default_enabled=False,
    description="Присылает участнику личное сообщение, когда он получает или теряет выбранную роль.",
    fields=[{"key": "rules", "label": "Правила", "type": "rules", "max": 25, "default": [], "item_fields": [
        {"key": "role", "label": "Роль", "type": "role", "default": None},
        {"key": "on_add", "label": "ЛС при получении роли", "type": "longtext", "default": "", "help": "{user} {name} {server} {role}; пусто — не писать"},
        {"key": "on_remove", "label": "ЛС при потере роли", "type": "longtext", "default": ""}]}],
))

AUTO_PUBLISH = modules.register(modules.Module(
    key="auto_publish", title="Автопубликация", icon="📢", category="community", default_enabled=False,
    description="Сообщения в каналах объявлений публикуются сами — кнопку «Опубликовать» нажимать не нужно.",
    fields=[
        {"key": "all_channels", "label": "Все каналы объявлений", "type": "bool", "default": True},
        {"key": "channels", "label": "Только эти каналы", "type": "channels", "kind": "text", "default": [],
         "help": "Работает, если «Все каналы объявлений» выключено"},
        {"key": "skip_bots", "label": "Не публиковать сообщения ботов", "type": "bool", "default": False},
    ],
))

AUTO_PING = modules.register(modules.Module(
    key="auto_ping", title="Автопинг", icon="🔔", category="community", default_enabled=False,
    description="Ненадолго упоминает новичка в выбранных каналах, чтобы они отмечались для него непрочитанными.",
    fields=[
        {"key": "delay", "label": "Задержка после входа, сек", "type": "number", "default": 5, "min": 0, "max": 600},
        {"key": "channels", "label": "Каналы (до 5)", "type": "channels", "kind": "text", "default": []},
    ],
))

MATCH_OPTIONS = [{"value": "contains", "label": "содержит"}, {"value": "exact", "label": "точное совпадение"},
                 {"value": "starts", "label": "начинается с"}]
AUTO_RESPONDER = modules.register(modules.Module(
    key="auto_responder", title="Автоответчик", icon="💬", category="community", default_enabled=False,
    description="Отвечает в чате, когда в сообщении встречается заданное слово или фраза.",
    fields=[{"key": "rules", "label": "Триггеры", "type": "rules", "max": 50, "default": [], "item_fields": [
        {"key": "trigger", "label": "Слово или фраза", "type": "text", "default": ""},
        {"key": "match", "label": "Совпадение", "type": "select", "default": "contains", "options": MATCH_OPTIONS},
        {"key": "response", "label": "Ответ", "type": "longtext", "default": "", "help": "{user} {name} {server} {channel}"},
        {"key": "cooldown", "label": "Пауза, сек", "type": "number", "default": 30, "min": 0, "max": 86400}]}],
))

CUSTOM_COMMANDS = modules.register(modules.Module(
    key="custom_commands", title="Конструктор команд", icon="⌨️", category="tools", default_enabled=False,
    description="Свои команды с готовым ответом: участники вызывают их через /cmd и выбирают название из списка.",
    fields=[{"key": "commands", "label": "Команды", "type": "rules", "max": 50, "default": [], "item_fields": [
        {"key": "name", "label": "Название", "type": "text", "default": ""},
        {"key": "response", "label": "Ответ", "type": "longtext", "default": "", "help": "{user} {name} {server} {channel}"},
        {"key": "embed", "label": "В виде embed", "type": "bool", "default": False},
        {"key": "ephemeral", "label": "Видно только вызвавшему", "type": "bool", "default": False},
        {"key": "role", "label": "Только для роли (пусто — для всех)", "type": "role", "default": None}]}],
))


# ── pure helpers (unit-tested) ──────────────────────────────────────────────

def next_slow_level(current: int, rate: float, busy_from: float, max_slow: int) -> int:
    """New slowmode seconds given the recent message rate (messages/min)."""
    steps = [0] + [s for s in SLOW_STEPS if s <= max_slow]
    idx = max((i for i, s in enumerate(steps) if s <= current), default=0)
    if rate >= busy_from and idx < len(steps) - 1:
        idx += 1
    elif rate < busy_from / 2 and idx > 0:
        idx -= 1
    return steps[idx]


def trigger_matches(rule: dict, content: str) -> bool:
    trig = (rule.get("trigger") or "").strip().lower()
    text = (content or "").strip().lower()
    if not trig or not text:
        return False
    mode = rule.get("match", "contains")
    if mode == "exact":
        return text == trig
    if mode == "starts":
        return text.startswith(trig)
    return trig in text


def temp_name(template: str, display_name: str) -> str:
    return ((template or "Канал {name}").replace("{name}", display_name) or display_name)[:100]


def human_slow(seconds: int) -> str:
    if seconds == 0:
        return "выключен"
    return f"{seconds} с" if seconds < 60 else f"{seconds // 60} мин"


# ── cog ─────────────────────────────────────────────────────────────────────

class ServerTools(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.msg_times: dict[int, collections.deque] = collections.defaultdict(collections.deque)
        self.notice: dict[int, int] = {}                      # channel id -> last slowmode notice message id
        self.cooldowns: dict[tuple[int, int], float] = {}     # (guild, rule index) -> ts

    async def cog_load(self):
        self.slow_loop.start()
        cmds = install_tools(self.bot, self)
        cmds["cmd"].autocomplete("name")(self._cc_autocomplete)

    async def cog_unload(self):
        self.slow_loop.cancel()
        uninstall_tools(self)

    # auto slowmode ---------------------------------------------------------

    @tasks.loop(seconds=20)
    async def slow_loop(self):
        now = time.time()
        for guild in self.bot.guilds:
            cfg = modules.get_config(guild.id, "auto_slowmode")
            if not cfg["enabled"]:
                continue
            for cid in cfg["channels"][:10]:
                ch = guild.get_channel(cid)
                if not isinstance(ch, discord.TextChannel):
                    continue
                times = self.msg_times[cid]
                while times and now - times[0] > 60:
                    times.popleft()
                target = next_slow_level(ch.slowmode_delay, len(times), cfg["busy_from"], int(cfg["max_slowmode"]))
                if target == ch.slowmode_delay:
                    continue
                try:
                    await ch.edit(slowmode_delay=target, reason="Auto Slowmode")
                except (discord.Forbidden, discord.HTTPException):
                    continue
                if cfg["announce"]:
                    await self._announce_slow(ch, target)

    async def _announce_slow(self, ch, seconds: int):
        old = self.notice.pop(ch.id, None)
        if old:
            try:
                await ch.get_partial_message(old).delete()
            except (discord.Forbidden, discord.HTTPException):
                pass
        try:
            m = await ch.send(f"🐌 Slowmode теперь: **{human_slow(seconds)}**")
            self.notice[ch.id] = m.id
        except (discord.Forbidden, discord.HTTPException):
            pass

    @slow_loop.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @slow_loop.error
    async def _slow_err(self, error):
        print(f"WARNING: auto_slowmode loop crashed, restarting: {error}")
        if not self.slow_loop.is_running():
            self.slow_loop.start()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return
        gid = message.guild.id
        if not message.author.bot:
            cfg = modules.get_config(gid, "auto_slowmode")
            if cfg["enabled"] and message.channel.id in cfg["channels"]:
                self.msg_times[message.channel.id].append(time.time())
        await self._auto_publish(message)
        if message.author.bot:
            return
        await self._auto_respond(message)

    # auto publish ----------------------------------------------------------

    async def _auto_publish(self, message: discord.Message):
        if message.channel.type != discord.ChannelType.news or message.flags.crossposted:
            return
        cfg = modules.get_config(message.guild.id, "auto_publish")
        if not cfg["enabled"] or (cfg["skip_bots"] and message.author.bot):
            return
        if not cfg["all_channels"] and message.channel.id not in cfg["channels"]:
            return
        try:
            await message.publish()
        except (discord.Forbidden, discord.HTTPException):
            pass

    # auto responder --------------------------------------------------------

    async def _auto_respond(self, message: discord.Message):
        cfg = modules.get_config(message.guild.id, "auto_responder")
        if not cfg["enabled"]:
            return
        now = time.time()
        for i, rule in enumerate(cfg["rules"]):
            if not trigger_matches(rule, message.content) or not (rule.get("response") or "").strip():
                continue
            key = (message.guild.id, i)
            if now - self.cooldowns.get(key, 0) < (rule.get("cooldown") or 0):
                continue
            self.cooldowns[key] = now
            try:
                await message.channel.send(render(rule["response"], member=message.author, guild=message.guild, channel=message.channel),
                                           reference=message, mention_author=False,
                                           allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException):
                pass
            return

    # role DMs --------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles == after.roles or after.bot:
            return
        cfg = modules.get_config(after.guild.id, "role_dm")
        if not cfg["enabled"]:
            return
        gained = {r.id for r in after.roles} - {r.id for r in before.roles}
        lost = {r.id for r in before.roles} - {r.id for r in after.roles}
        for rule in cfg["rules"]:
            rid = rule.get("role")
            if not rid:
                continue
            if rid in gained and (rule.get("on_add") or "").strip():
                await self._dm(after, rule["on_add"], after.guild.get_role(rid))
            elif rid in lost and (rule.get("on_remove") or "").strip():
                await self._dm(after, rule["on_remove"], after.guild.get_role(rid))

    @staticmethod
    async def _dm(member, text, role):
        body = render(text, member=member, guild=member.guild).replace("{role}", role.name if role else "")
        try:
            await member.send(body[:2000])
        except (discord.Forbidden, discord.HTTPException):
            pass

    # auto ping -------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        cfg = modules.get_config(member.guild.id, "auto_ping")
        if not cfg["enabled"] or not cfg["channels"]:
            return
        await asyncio.sleep(cfg["delay"])
        for cid in cfg["channels"][:5]:
            ch = member.guild.get_channel(cid)
            if ch is None or not hasattr(ch, "send"):
                continue
            try:
                m = await ch.send(member.mention, allowed_mentions=discord.AllowedMentions(users=[member]))
                await m.delete()
            except (discord.Forbidden, discord.HTTPException):
                pass

    # temp voice ------------------------------------------------------------

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        cfg = modules.get_config(member.guild.id, "temp_voice")
        if not cfg["enabled"]:
            return
        if after.channel and after.channel.id in cfg["creators"][:5] and after.channel != before.channel:
            await self._create_temp(member, after.channel, cfg)
        if before.channel and before.channel != after.channel and not before.channel.members:
            await self._maybe_delete_temp(before.channel, cfg)

    async def _create_temp(self, member, creator, cfg):
        try:
            ch = await member.guild.create_voice_channel(
                temp_name(cfg["name_template"], member.display_name), category=creator.category,
                user_limit=cfg["user_limit"] or 0, reason="Временный голосовой канал",
                overwrites={member: discord.PermissionOverwrite(manage_channels=True, move_members=True, connect=True)})
            await db.run(db.execute, "INSERT OR REPLACE INTO temp_vc (channel_id, guild_id, owner_id, created_ts) VALUES (?,?,?,?)",
                         (ch.id, member.guild.id, member.id, int(time.time())))
            await member.move_to(ch)
            await self._log(member.guild, cfg, f"🔊 {member.mention} создал канал **{ch.name}**")
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _maybe_delete_temp(self, channel, cfg):
        rows = await db.run(db.query, "SELECT 1 FROM temp_vc WHERE channel_id=?", (channel.id,))
        if not rows:
            return
        name = channel.name
        try:
            await channel.delete(reason="Временный канал опустел")
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            pass
        await db.run(db.execute, "DELETE FROM temp_vc WHERE channel_id=?", (channel.id,))
        await self._log(channel.guild, cfg, f"🗑 Канал **{name}** удалён (пуст)")

    @staticmethod
    async def _log(guild, cfg, text):
        ch = guild.get_channel(cfg["log_channel"]) if cfg.get("log_channel") else None
        if ch is not None:
            try:
                await ch.send(embed=embeds.make(description=text, color=0x5865F2))
            except (discord.Forbidden, discord.HTTPException):
                pass

    # custom commands -------------------------------------------------------

    @tool("cmd", "Своя команда сервера")
    @app_commands.describe(name="Название команды")
    async def cmd(self, interaction: discord.Interaction, name: str):
        cfg = modules.get_config(interaction.guild_id, "custom_commands")
        if not cfg["enabled"]:
            return await interaction.response.send_message("Конструктор команд выключен.", ephemeral=True)
        c = next((c for c in cfg["commands"] if (c.get("name") or "").lower() == name.lower()), None)
        if c is None:
            return await interaction.response.send_message("Такой команды нет.", ephemeral=True)
        if c.get("role") and c["role"] not in {r.id for r in interaction.user.roles}:
            return await interaction.response.send_message("Эта команда вам недоступна.", ephemeral=True)
        text = render(c["response"], member=interaction.user, guild=interaction.guild, channel=interaction.channel)
        kw = {"ephemeral": bool(c.get("ephemeral")), "allowed_mentions": discord.AllowedMentions(users=[interaction.user])}
        if c.get("embed"):
            await interaction.response.send_message(embed=embeds.make(c["name"], text), **kw)
        else:
            await interaction.response.send_message(text[:2000] or "—", **kw)

    async def _cc_autocomplete(self, interaction: discord.Interaction, current: str):
        cfg = modules.get_config(interaction.guild_id, "custom_commands")
        if not cfg["enabled"]:
            return []
        roles = {r.id for r in getattr(interaction.user, "roles", [])}
        names = [c["name"] for c in cfg["commands"] if c.get("name") and (not c.get("role") or c["role"] in roles)]
        return [app_commands.Choice(name=n, value=n) for n in names if current.lower() in n.lower()][:25]


def _temp_table(guild):
    out = []
    for r in db.query("SELECT * FROM temp_vc WHERE guild_id=? ORDER BY created_ts DESC", (guild.id,)):
        ch = guild.get_channel(r["channel_id"])
        owner = guild.get_member(r["owner_id"])
        out.append({"name": ch.name if ch else f"удалён · {r['channel_id']}", "owner": owner.display_name if owner else str(r["owner_id"]),
                    "members": len(ch.members) if ch else 0, "created_ts": r["created_ts"]})
    return out


modules.register_table("temp_voice", "active", _temp_table)


async def setup(bot):
    await bot.add_cog(ServerTools(bot))
