"""
Moderation -- /mute /unmute /kick /ban /tempban /unban /purge /cases, every
action recorded as a numbered case (SQLite) and shown in the dashboard.

Separate from the legacy !warn system in main.py (role-based 1/2/3 warnings
tied to the shop/recruit cabinet) -- that keeps working untouched.
Who may use the commands: Discord permission (moderate_members / kick_members /
ban_members / manage_messages) OR one of the module's "mod roles".
"""

import datetime
import re
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import db, embeds, modules

db.register_schema("""
CREATE TABLE IF NOT EXISTS mod_cases (
    guild_id   INTEGER NOT NULL,
    case_no    INTEGER NOT NULL,
    type       TEXT    NOT NULL,
    user_id    INTEGER NOT NULL,
    user_name  TEXT,
    mod_id     INTEGER,
    mod_name   TEXT,
    reason     TEXT,
    ts         INTEGER NOT NULL,
    duration_s INTEGER,
    expires_ts INTEGER,
    active     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, case_no)
);
CREATE INDEX IF NOT EXISTS mod_cases_user ON mod_cases (guild_id, user_id);
CREATE INDEX IF NOT EXISTS mod_cases_expiry ON mod_cases (type, active, expires_ts);
""")

modules.register(modules.Module(
    key="moderation", title="Модерация", icon="🛡", category="moderation",
    description="Мут, кик, бан, временный бан и очистка чата командами. Каждое действие сохраняется как дело.",
    fields=[
        {"key": "mod_roles", "label": "Роли модераторов", "type": "roles", "default": [],
         "help": "Могут пользоваться командами без прав в Discord (но не могут трогать тех, кто выше их по роли)"},
        {"key": "log_channel", "label": "Канал дел", "type": "channel", "kind": "text", "default": None,
         "help": "Сюда отправляется карточка каждого действия"},
        {"key": "dm_user", "label": "Уведомлять в ЛС", "type": "bool", "default": True,
         "help": "Наказанный получит сообщение с причиной"},
        {"key": "require_reason", "label": "Причина обязательна", "type": "bool", "default": False},
        {"key": "purge_max", "label": "Максимум сообщений за /purge", "type": "number", "default": 100, "min": 1, "max": 500},
    ],
    table={"title": "Последние дела", "columns": [
        {"key": "case_no", "label": "#"}, {"key": "type_label", "label": "Тип"},
        {"key": "user_name", "label": "Кому"}, {"key": "mod_name", "label": "Кто"},
        {"key": "reason", "label": "Причина"}, {"key": "ts", "label": "Когда", "format": "time"}]},
))

TYPE_LABELS = {"mute": "Мут", "unmute": "Снятие мута", "kick": "Кик", "ban": "Бан",
               "tempban": "Временный бан", "unban": "Разбан"}
TYPE_COLORS = {"mute": 0xFEE75C, "unmute": 0x3BA55D, "kick": 0xE67E22, "ban": 0xED4245,
               "tempban": 0xED4245, "unban": 0x3BA55D}
MAX_TIMEOUT_S = 28 * 86400
_DUR = re.compile(r"(\d+)\s*([smhdw])", re.I)
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(text: str) -> int | None:
    """'90m', '2h30m', '1d' -> seconds. None when it isn't a duration."""
    text = (text or "").strip().lower().replace(" ", "")
    if not text:
        return None
    pos, total = 0, 0
    for m in _DUR.finditer(text):
        if m.start() != pos:
            return None
        total += int(m.group(1)) * _UNIT[m.group(2).lower()]
        pos = m.end()
    return total if pos == len(text) and total > 0 else None


def human_duration(seconds: int) -> str:
    parts = []
    for name, size in (("д", 86400), ("ч", 3600), ("м", 60), ("с", 1)):
        if seconds >= size:
            parts.append(f"{seconds // size}{name}")
            seconds %= size
    return " ".join(parts) or "0с"


# ── cases storage (sync, called through db.run from async code) ─────────────

def add_case(guild_id: int, type_: str, user, mod, reason: str | None,
             duration_s: int | None = None, active: bool = False) -> int:
    with db._lock:   # next case number + insert must be one step
        row = db.query("SELECT COALESCE(MAX(case_no), 0) + 1 AS n FROM mod_cases WHERE guild_id=?", (guild_id,))[0]
        n = row["n"]
        now = int(time.time())
        db.execute(
            "INSERT INTO mod_cases (guild_id, case_no, type, user_id, user_name, mod_id, mod_name, reason, ts,"
            " duration_s, expires_ts, active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (guild_id, n, type_, user.id, str(user), mod.id if mod else None, str(mod) if mod else None,
             reason, now, duration_s, now + duration_s if duration_s else None, int(active)))
        return n


def cases_for_user(guild_id: int, user_id: int, limit: int = 15) -> list[dict]:
    return [dict(r) for r in db.query(
        "SELECT * FROM mod_cases WHERE guild_id=? AND user_id=? ORDER BY case_no DESC LIMIT ?",
        (guild_id, user_id, limit))]


def recent_cases(guild_id: int) -> list[dict]:
    out = []
    for r in db.query("SELECT * FROM mod_cases WHERE guild_id=? ORDER BY case_no DESC LIMIT 100", (guild_id,)):
        d = dict(r)
        d["type_label"] = TYPE_LABELS.get(d["type"], d["type"])
        d["user_name"] = f"{d['user_name']} ({d['user_id']})"
        out.append(d)
    return out


modules.register_data("moderation", recent_cases)


def due_tempbans(now: int) -> list[dict]:
    return [dict(r) for r in db.query(
        "SELECT * FROM mod_cases WHERE type='tempban' AND active=1 AND expires_ts<=?", (now,))]


def close_case(guild_id: int, case_no: int) -> None:
    db.execute("UPDATE mod_cases SET active=0 WHERE guild_id=? AND case_no=?", (guild_id, case_no))


# ── permissions ─────────────────────────────────────────────────────────────

PERMS = {"mute": "moderate_members", "kick": "kick_members", "ban": "ban_members",
         "purge": "manage_messages", "cases": "moderate_members"}


def may_use(member: discord.Member, action: str) -> bool:
    if getattr(member.guild_permissions, PERMS[action], False) or member.guild_permissions.administrator:
        return True
    mod_roles = set(modules.get_config(member.guild.id, "moderation")["mod_roles"] or [])
    return any(r.id in mod_roles for r in member.roles)


def hierarchy_problem(guild: discord.Guild, mod: discord.Member, target) -> str | None:
    """Why `mod` may not act on `target` (None if fine). Non-members can't be checked -> allowed."""
    me = guild.me
    if target.id == mod.id:
        return "Нельзя применить это к себе."
    if target.id == guild.owner_id:
        return "Нельзя трогать владельца сервера."
    if not hasattr(target, "top_role"):      # plain discord.User (ban by id): nothing to compare
        return None
    if target.id == me.id:
        return "Нельзя применить это к боту."
    if mod.id != guild.owner_id and target.top_role >= mod.top_role:
        return "Роль этого участника не ниже вашей."
    if target.top_role >= me.top_role:
        return "Роль участника не ниже роли бота — подними роль бота выше."
    return None


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.tempban_loop.start()

    async def cog_unload(self):
        self.tempban_loop.cancel()

    # ── shared flow ─────────────────────────────────────────────────────────
    async def _precheck(self, interaction: discord.Interaction, action: str, target, reason: str | None) -> bool:
        cfg = modules.get_config(interaction.guild_id, "moderation")
        problem = None
        if not cfg["enabled"]:
            problem = "Модуль модерации выключен в панели."
        elif not may_use(interaction.user, action):
            problem = "Недостаточно прав для этой команды."
        elif cfg["require_reason"] and not (reason or "").strip():
            problem = "Нужно указать причину."
        elif target is not None:
            problem = hierarchy_problem(interaction.guild, interaction.user, target)
        if problem:
            await interaction.response.send_message(f"❌ {problem}", ephemeral=True)
            return False
        return True

    async def _record(self, interaction, type_: str, target, reason, duration_s=None, active=False, dm_text=None) -> int:
        guild = interaction.guild
        cfg = modules.get_config(guild.id, "moderation")
        case_no = await db.run(add_case, guild.id, type_, target, interaction.user, reason, duration_s, active)
        if cfg["dm_user"] and dm_text and isinstance(target, (discord.Member, discord.User)):
            try:
                await target.send(embed=embeds.make(f"{TYPE_LABELS[type_]} · {guild.name}", dm_text, TYPE_COLORS[type_]))
            except (discord.Forbidden, discord.HTTPException):
                pass
        ch = guild.get_channel(cfg["log_channel"]) if cfg["log_channel"] else None
        if ch:
            e = embeds.make(f"Дело #{case_no} · {TYPE_LABELS[type_]}", color=TYPE_COLORS[type_])
            e.add_field(name="Кому", value=f"{target.mention} (`{target.id}`)")
            e.add_field(name="Кто", value=interaction.user.mention)
            if duration_s:
                e.add_field(name="Срок", value=human_duration(duration_s))
            e.add_field(name="Причина", value=embeds.clip(reason, 900) or "*не указана*", inline=False)
            try:
                await ch.send(embed=e, allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException):
                pass
        return case_no

    @staticmethod
    def _reason_for_audit(interaction, reason):
        return f"{interaction.user} ({interaction.user.id}): {reason or 'без причины'}"[:512]

    # ── commands ────────────────────────────────────────────────────────────
    @app_commands.command(name="mute", description="Выдать таймаут участнику")
    @app_commands.describe(user="Кому", duration="Срок: 10m, 2h, 1d (максимум 28d)", reason="Причина")
    @app_commands.guild_only()
    async def mute(self, interaction: discord.Interaction, user: discord.Member, duration: str, reason: str | None = None):
        if not await self._precheck(interaction, "mute", user, reason):
            return
        secs = parse_duration(duration)
        if secs is None or secs > MAX_TIMEOUT_S:
            return await interaction.response.send_message("❌ Срок вида `10m`, `2h`, `1d`, не больше 28 дней.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            await user.timeout(datetime.timedelta(seconds=secs), reason=self._reason_for_audit(interaction, reason))
        except discord.Forbidden:
            return await interaction.followup.send("❌ У бота нет прав выдать таймаут.", ephemeral=True)
        n = await self._record(interaction, "mute", user, reason, secs,
                               dm_text=f"Вам выдан таймаут на **{human_duration(secs)}**.\nПричина: {reason or 'не указана'}")
        await interaction.followup.send(f"🔇 {user.mention} в таймауте на {human_duration(secs)} · дело #{n}", ephemeral=True)

    @app_commands.command(name="unmute", description="Снять таймаут")
    @app_commands.guild_only()
    async def unmute(self, interaction: discord.Interaction, user: discord.Member, reason: str | None = None):
        if not await self._precheck(interaction, "mute", user, reason):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await user.timeout(None, reason=self._reason_for_audit(interaction, reason))
        except discord.Forbidden:
            return await interaction.followup.send("❌ У бота нет прав снять таймаут.", ephemeral=True)
        n = await self._record(interaction, "unmute", user, reason)
        await interaction.followup.send(f"🔊 Таймаут с {user.mention} снят · дело #{n}", ephemeral=True)

    @app_commands.command(name="kick", description="Выгнать участника")
    @app_commands.guild_only()
    async def kick(self, interaction: discord.Interaction, user: discord.Member, reason: str | None = None):
        if not await self._precheck(interaction, "kick", user, reason):
            return
        await interaction.response.defer(ephemeral=True)
        n_dm = f"Вас выгнали с сервера.\nПричина: {reason or 'не указана'}"
        n = await self._record(interaction, "kick", user, reason, dm_text=n_dm)   # DM first: afterwards we share no server
        try:
            await user.kick(reason=self._reason_for_audit(interaction, reason))
        except discord.Forbidden:
            return await interaction.followup.send(f"❌ У бота нет прав на кик (дело #{n} уже записано, отметьте вручную).", ephemeral=True)
        await interaction.followup.send(f"👢 {user} выгнан · дело #{n}", ephemeral=True)

    @app_commands.command(name="ban", description="Забанить участника")
    @app_commands.describe(user="Кого", reason="Причина", delete_days="Удалить сообщения за N дней (0–7)")
    @app_commands.guild_only()
    async def ban(self, interaction: discord.Interaction, user: discord.User, reason: str | None = None,
                  delete_days: app_commands.Range[int, 0, 7] = 0):
        target = interaction.guild.get_member(user.id) or user
        if not await self._precheck(interaction, "ban", target, reason):
            return
        await interaction.response.defer(ephemeral=True)
        n = await self._record(interaction, "ban", user, reason,
                               dm_text=f"Вы забанены.\nПричина: {reason or 'не указана'}")
        try:
            await interaction.guild.ban(user, reason=self._reason_for_audit(interaction, reason), delete_message_days=delete_days)
        except discord.Forbidden:
            return await interaction.followup.send(f"❌ У бота нет прав на бан (дело #{n} уже записано, отметьте вручную).", ephemeral=True)
        await interaction.followup.send(f"🔨 {user} забанен · дело #{n}", ephemeral=True)

    @app_commands.command(name="tempban", description="Забанить на время")
    @app_commands.describe(duration="Срок: 1d, 12h, 2w")
    @app_commands.guild_only()
    async def tempban(self, interaction: discord.Interaction, user: discord.User, duration: str, reason: str | None = None):
        target = interaction.guild.get_member(user.id) or user
        if not await self._precheck(interaction, "ban", target, reason):
            return
        secs = parse_duration(duration)
        if secs is None:
            return await interaction.response.send_message("❌ Срок вида `12h`, `3d`, `2w`.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        n = await self._record(interaction, "tempban", user, reason, secs, active=True,
                               dm_text=f"Вы забанены на **{human_duration(secs)}**.\nПричина: {reason or 'не указана'}")
        try:
            await interaction.guild.ban(user, reason=self._reason_for_audit(interaction, f"tempban {duration}: {reason}"))
        except discord.Forbidden:
            await db.run(close_case, interaction.guild_id, n)
            return await interaction.followup.send("❌ У бота нет прав на бан.", ephemeral=True)
        await interaction.followup.send(f"⏳ {user} забанен на {human_duration(secs)} · дело #{n}", ephemeral=True)

    @app_commands.command(name="unban", description="Разбанить по ID")
    @app_commands.describe(user_id="ID пользователя")
    @app_commands.guild_only()
    async def unban(self, interaction: discord.Interaction, user_id: str, reason: str | None = None):
        if not await self._precheck(interaction, "ban", None, reason):
            return
        if not user_id.isdigit():
            return await interaction.response.send_message("❌ Нужен числовой ID.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        user = discord.Object(int(user_id))
        try:
            await interaction.guild.unban(user, reason=self._reason_for_audit(interaction, reason))
        except discord.NotFound:
            return await interaction.followup.send("❌ Такого пользователя нет в списке банов.", ephemeral=True)
        except discord.Forbidden:
            return await interaction.followup.send("❌ У бота нет прав на разбан.", ephemeral=True)
        full = await self.bot.fetch_user(int(user_id))
        await db.run(self._close_tempbans, interaction.guild_id, int(user_id))
        n = await self._record(interaction, "unban", full, reason)
        await interaction.followup.send(f"♻️ {full} разбанен · дело #{n}", ephemeral=True)

    @staticmethod
    def _close_tempbans(guild_id: int, user_id: int) -> None:
        db.execute("UPDATE mod_cases SET active=0 WHERE guild_id=? AND user_id=? AND type='tempban'", (guild_id, user_id))

    @app_commands.command(name="purge", description="Удалить последние сообщения в канале")
    @app_commands.describe(amount="Сколько сообщений проверить", user="Удалять только сообщения этого участника")
    @app_commands.guild_only()
    async def purge(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 500],
                    user: discord.Member | None = None):
        if not await self._precheck(interaction, "purge", None, None):
            return
        cap = modules.get_config(interaction.guild_id, "moderation")["purge_max"]
        if amount > cap:
            return await interaction.response.send_message(f"❌ Максимум {cap} сообщений за раз (настройка в панели).", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(
                limit=amount, check=(lambda m: m.author.id == user.id) if user else None,
                reason=self._reason_for_audit(interaction, "purge"))
        except discord.Forbidden:
            return await interaction.followup.send("❌ У бота нет права удалять сообщения здесь.", ephemeral=True)
        await interaction.followup.send(f"🧹 Удалено сообщений: {len(deleted)}", ephemeral=True)

    @app_commands.command(name="cases", description="История наказаний участника")
    @app_commands.guild_only()
    async def cases(self, interaction: discord.Interaction, user: discord.User):
        if not await self._precheck(interaction, "cases", None, None):
            return
        rows = await db.run(cases_for_user, interaction.guild_id, user.id)
        if not rows:
            return await interaction.response.send_message(f"У {user} дел нет.", ephemeral=True)
        e = embeds.make(f"Дела · {user}", color=0x5865F2)
        for r in rows:
            when = discord.utils.format_dt(datetime.datetime.fromtimestamp(r["ts"], datetime.timezone.utc), "d")
            extra = f" · {human_duration(r['duration_s'])}" if r["duration_s"] else ""
            e.add_field(name=f"#{r['case_no']} {TYPE_LABELS.get(r['type'], r['type'])}{extra}",
                        value=f"{when} · {r['mod_name'] or '—'}\n{embeds.clip(r['reason'], 200) or '*без причины*'}", inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    # ── tempban expiry ──────────────────────────────────────────────────────
    @tasks.loop(minutes=1)
    async def tempban_loop(self):
        for case in await db.run(due_tempbans, int(time.time())):
            guild = self.bot.get_guild(case["guild_id"])
            await db.run(close_case, case["guild_id"], case["case_no"])
            if guild is None:
                continue
            try:
                await guild.unban(discord.Object(case["user_id"]), reason="tempban expired")
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue
            user = await self.bot.fetch_user(case["user_id"])
            await db.run(add_case, guild.id, "unban", user, None, "Срок временного бана истёк")

    @tempban_loop.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @tempban_loop.error
    async def _loop_error(self, error):
        print(f"WARNING: tempban_loop crashed, restarting: {error}")
        if not self.tempban_loop.is_running():
            self.tempban_loop.start()


async def setup(bot):
    await bot.add_cog(Moderation(bot))
