"""
Role tools in one cog:

* Sticky roles  -- remember a member's roles when they leave, give them back if they return.
* Temporary roles -- /temprole add <user> <role> <duration>; removed automatically.
* Role prefixes -- nickname prefix by role, e.g. "[ADM] Vova".
"""

import json
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import db, embeds, modules
from modules.moderation import human_duration, parse_duration

db.register_schema("""
CREATE TABLE IF NOT EXISTS sticky_roles (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    roles    TEXT    NOT NULL,
    left_ts  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS temp_roles (
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    role_id    INTEGER NOT NULL,
    expires_ts INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id, role_id)
);
CREATE INDEX IF NOT EXISTS temp_roles_due ON temp_roles (expires_ts);
CREATE TABLE IF NOT EXISTS role_prefixes (
    guild_id INTEGER NOT NULL,
    role_id  INTEGER NOT NULL,
    prefix   TEXT    NOT NULL,
    PRIMARY KEY (guild_id, role_id)
);
""")

STICKY = modules.register(modules.Module(
    key="sticky_roles", title="Возврат ролей", icon="🔁", category="moderation", default_enabled=False,
    description="Запоминает роли ушедшего участника и возвращает их, если он снова зайдёт.",
    fields=[
        {"key": "ignored_roles", "label": "Не запоминать эти роли", "type": "roles", "default": [],
         "help": "Например, роль «не верифицирован» или временные роли"},
        {"key": "expire_days", "label": "Хранить, дней", "type": "number", "default": 30, "min": 0, "max": 3650, "help": "0 — бессрочно"},
    ],
    tables=[{"id": "saved", "title": "Запомненные роли", "columns": [
        {"key": "user", "label": "Участник"}, {"key": "count", "label": "Ролей"}, {"key": "left_ts", "label": "Вышел", "format": "time"}]}],
))
TEMP = modules.register(modules.Module(
    key="temp_roles", title="Временные роли", icon="⏳", category="moderation", default_enabled=True,
    description="Роль на заданный срок: бот снимет её сам. Команда /temprole и панель.",
    fields=[{"key": "log_channel", "label": "Канал логов", "type": "channel", "kind": "text", "default": None}],
    tables=[{"id": "active", "title": "Активные временные роли", "columns": [
        {"key": "user", "label": "Участник"}, {"key": "role", "label": "Роль"}, {"key": "expires_ts", "label": "Истекает", "format": "time"}]}],
))
PREFIX = modules.register(modules.Module(
    key="role_prefixes", title="Префиксы по ролям", icon="🏷", category="moderation", default_enabled=False,
    description="Добавляет префикс к нику по роли, например «[АДМ] Вова». Работает при выдаче и снятии ролей.",
    fields=[{"key": "separator", "label": "Разделитель", "type": "text", "default": " ", "help": "Между префиксом и ником"}],
    tables=[{"id": "rules", "title": "Правила (сверху вниз — приоритет по роли)", "columns": [
        {"key": "role", "label": "Роль"}, {"key": "prefix", "label": "Префикс"}]}],
))


# ── sticky roles ────────────────────────────────────────────────────────────

def save_roles(guild_id, user_id, role_ids) -> None:
    if not role_ids:
        db.execute("DELETE FROM sticky_roles WHERE guild_id=? AND user_id=?", (guild_id, user_id))
        return
    db.execute("INSERT INTO sticky_roles (guild_id, user_id, roles, left_ts) VALUES (?,?,?,?) "
               "ON CONFLICT(guild_id, user_id) DO UPDATE SET roles=excluded.roles, left_ts=excluded.left_ts",
               (guild_id, user_id, json.dumps(role_ids), int(time.time())))


def pop_roles(guild_id, user_id, expire_days: int, now: int | None = None) -> list[int]:
    rows = db.query("SELECT roles, left_ts FROM sticky_roles WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    if not rows:
        return []
    db.execute("DELETE FROM sticky_roles WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    if expire_days and (now or int(time.time())) - rows[0]["left_ts"] > expire_days * 86400:
        return []
    return json.loads(rows[0]["roles"])


# ── temporary roles ─────────────────────────────────────────────────────────

def add_temp(guild_id, user_id, role_id, expires_ts) -> None:
    db.execute("INSERT INTO temp_roles (guild_id, user_id, role_id, expires_ts) VALUES (?,?,?,?) "
               "ON CONFLICT(guild_id, user_id, role_id) DO UPDATE SET expires_ts=excluded.expires_ts",
               (guild_id, user_id, role_id, expires_ts))


def remove_temp(guild_id, user_id, role_id) -> bool:
    return db.execute("DELETE FROM temp_roles WHERE guild_id=? AND user_id=? AND role_id=?", (guild_id, user_id, role_id)).rowcount > 0


def due_temp(now: int) -> list[dict]:
    return [dict(r) for r in db.query("SELECT * FROM temp_roles WHERE expires_ts<=?", (now,))]


# ── prefixes ────────────────────────────────────────────────────────────────

def prefix_rules(guild_id) -> list[dict]:
    return [dict(r) for r in db.query("SELECT role_id, prefix FROM role_prefixes WHERE guild_id=?", (guild_id,))]


def strip_prefix(nick: str, known: list[str], sep: str) -> str:
    changed = True
    while changed:                        # a name can carry a stale prefix from an earlier rule
        changed = False
        for p in sorted(known, key=len, reverse=True):
            if p and nick.startswith(p + sep):
                nick, changed = nick[len(p) + len(sep):], True
    return nick


def wanted_nick(base_name: str, current_nick: str | None, prefix: str | None, known: list[str], sep: str) -> str | None:
    """Nickname the member should have (None = no nickname, the account name shows)."""
    base = strip_prefix(current_nick or base_name, known, sep)
    if prefix:
        return f"{prefix}{sep}{base}"[:32]
    return None if base == base_name else base


class RolesExtra(commands.Cog):
    temprole = app_commands.Group(name="temprole", description="Временные роли", guild_only=True)

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.temp_loop.start()

    async def cog_unload(self):
        self.temp_loop.cancel()

    # sticky roles
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        cfg = modules.get_config(member.guild.id, "sticky_roles")
        if not cfg["enabled"] or member.bot:
            return
        ignored = set(cfg["ignored_roles"] or [])
        ids = [r.id for r in member.roles if not r.is_default() and not r.managed and r.id not in ignored]
        await db.run(save_roles, member.guild.id, member.id, ids)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        cfg = modules.get_config(member.guild.id, "sticky_roles")
        if not cfg["enabled"] or member.bot:
            return
        ids = await db.run(pop_roles, member.guild.id, member.id, cfg["expire_days"])
        top = member.guild.me.top_role
        roles = [r for r in (member.guild.get_role(i) for i in ids) if r and not r.managed and r < top]
        if roles:
            try:
                await member.add_roles(*roles, reason="Возврат ролей")
            except discord.Forbidden:
                pass

    # temp roles
    async def grant_temp(self, guild: discord.Guild, member: discord.Member, role: discord.Role, seconds: int) -> None:
        if role.managed or role.is_default():
            raise modules.ValidationError("Нельзя выдавать служебные роли")
        if role >= guild.me.top_role:
            raise modules.ValidationError("Роль выше роли бота — бот не сможет её выдать")
        try:
            await member.add_roles(role, reason="Временная роль")
        except discord.Forbidden:
            raise modules.ValidationError("У бота нет прав выдать эту роль")
        await db.run(add_temp, guild.id, member.id, role.id, int(time.time()) + seconds)

    async def _tlog(self, guild, text):
        cfg = modules.get_config(guild.id, "temp_roles")
        ch = guild.get_channel(cfg["log_channel"]) if cfg["log_channel"] else None
        if ch:
            try:
                await ch.send(embed=embeds.make("⏳ Временные роли", text), allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException):
                pass

    @temprole.command(name="add", description="Выдать роль на время")
    @app_commands.describe(duration="Срок: 30m, 2h, 7d")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def add(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role, duration: str):
        secs = parse_duration(duration)
        if secs is None:
            return await interaction.response.send_message("❌ Срок вида `30m`, `2h`, `7d`.", ephemeral=True)
        if role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
            return await interaction.response.send_message("❌ Эта роль не ниже вашей.", ephemeral=True)
        try:
            await self.grant_temp(interaction.guild, user, role, secs)
        except modules.ValidationError as exc:
            return await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
        await self._tlog(interaction.guild, f"{interaction.user.mention} выдал {role.mention} участнику {user.mention} на {human_duration(secs)}")
        await interaction.response.send_message(f"✅ {user.mention}: {role.mention} на {human_duration(secs)}.", ephemeral=True)

    @temprole.command(name="remove", description="Снять временную роль сейчас")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def remove(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role):
        ok = await db.run(remove_temp, interaction.guild_id, user.id, role.id)
        if not ok:
            return await interaction.response.send_message("❌ Такой временной роли у участника нет.", ephemeral=True)
        try:
            await user.remove_roles(role, reason="Временная роль снята вручную")
        except discord.Forbidden:
            pass
        await interaction.response.send_message("✅ Снято.", ephemeral=True)

    @tasks.loop(seconds=30)
    async def temp_loop(self):
        for r in await db.run(due_temp, int(time.time())):
            await db.run(remove_temp, r["guild_id"], r["user_id"], r["role_id"])
            guild = self.bot.get_guild(r["guild_id"])
            member = guild.get_member(r["user_id"]) if guild else None
            role = guild.get_role(r["role_id"]) if guild else None
            if member and role and role in member.roles:
                try:
                    await member.remove_roles(role, reason="Срок временной роли истёк")
                    await self._tlog(guild, f"У {member.mention} истекла роль {role.mention}")
                except discord.Forbidden:
                    pass

    @temp_loop.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @temp_loop.error
    async def _loop_error(self, error):
        print(f"WARNING: temp_roles loop crashed, restarting: {error}")
        if not self.temp_loop.is_running():
            self.temp_loop.start()

    # prefixes
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles == after.roles or after.bot:
            return
        cfg = modules.get_config(after.guild.id, "role_prefixes")
        if not cfg["enabled"]:
            return
        await self.apply_prefix(after, cfg)

    async def apply_prefix(self, member: discord.Member, cfg: dict) -> None:
        rules = await db.run(prefix_rules, member.guild.id)
        if not rules:
            return
        by_role = {r["role_id"]: r["prefix"] for r in rules}
        mine = [r for r in member.roles if r.id in by_role]
        prefix = by_role[max(mine, key=lambda r: r.position).id] if mine else None
        new = wanted_nick(member.name, member.nick, prefix, list(by_role.values()), cfg["separator"])
        if new != member.nick and member.id != member.guild.owner_id:
            try:
                await member.edit(nick=new, reason="Префикс по роли")
            except (discord.Forbidden, discord.HTTPException):
                pass


async def setup(bot):
    await bot.add_cog(RolesExtra(bot))


# ── panel ───────────────────────────────────────────────────────────────────

def _sticky_table(guild):
    out = []
    for r in db.query("SELECT * FROM sticky_roles WHERE guild_id=? ORDER BY left_ts DESC LIMIT 200", (guild.id,)):
        out.append({"user": f"[{r['user_id']}]", "count": len(json.loads(r["roles"])), "left_ts": r["left_ts"]})
    return out


def _temp_table(guild):
    out = []
    for r in db.query("SELECT * FROM temp_roles WHERE guild_id=? ORDER BY expires_ts", (guild.id,)):
        m, role = guild.get_member(r["user_id"]), guild.get_role(r["role_id"])
        out.append({"user": f"{m.display_name} ({r['user_id']})" if m else f"[{r['user_id']}]",
                    "role": role.name if role else f"[{r['role_id']}]", "expires_ts": r["expires_ts"]})
    return out


def _prefix_table(guild):
    out = []
    for r in prefix_rules(guild.id):
        role = guild.get_role(r["role_id"])
        out.append((role.position if role else -1, {"role": role.name if role else f"[{r['role_id']}]", "prefix": r["prefix"]}))
    return [row for _, row in sorted(out, key=lambda t: -t[0])]


modules.register_table("sticky_roles", "saved", _sticky_table)
modules.register_table("temp_roles", "active", _temp_table)
modules.register_table("role_prefixes", "rules", _prefix_table)


async def _clear_sticky(ctx, p):
    if not await db.run(lambda: db.execute("DELETE FROM sticky_roles WHERE guild_id=? AND user_id=?", (ctx.guild.id, p["user"])).rowcount):
        raise modules.ValidationError("Для этого участника ничего не сохранено")
    return "Сохранённые роли удалены"


async def _temp_add(ctx, p):
    member, role = ctx.guild.get_member(p["user"]), ctx.guild.get_role(p["role"])
    secs = parse_duration(p["duration"])
    if member is None or role is None:
        raise modules.ValidationError("Участник или роль не найдены")
    if secs is None:
        raise modules.ValidationError("Срок вида 30m, 2h, 7d")
    await RolesExtra(ctx.bot).grant_temp(ctx.guild, member, role, secs)
    return f"{member.display_name}: «{role.name}» на {human_duration(secs)}"


async def _temp_remove(ctx, p):
    member, role = ctx.guild.get_member(p["user"]), ctx.guild.get_role(p["role"])
    if not await db.run(remove_temp, ctx.guild.id, p["user"], p["role"]):
        raise modules.ValidationError("Такой временной роли нет")
    if member and role:
        try:
            await member.remove_roles(role, reason="Временная роль снята из панели")
        except discord.Forbidden:
            pass
    return "Роль снята"


async def _prefix_add(ctx, p):
    role = ctx.guild.get_role(p["role"])
    prefix = (p["prefix"] or "").strip()
    if role is None or role.is_default() or role.managed:
        raise modules.ValidationError("Выберите обычную роль")
    if not prefix or len(prefix) > 12:
        raise modules.ValidationError("Префикс — от 1 до 12 символов")
    await db.run(db.execute, "INSERT INTO role_prefixes (guild_id, role_id, prefix) VALUES (?,?,?) "
                 "ON CONFLICT(guild_id, role_id) DO UPDATE SET prefix=excluded.prefix", (ctx.guild.id, role.id, prefix))
    return f"«{role.name}» → «{prefix}»"


async def _prefix_remove(ctx, p):
    if not await db.run(lambda: db.execute("DELETE FROM role_prefixes WHERE guild_id=? AND role_id=?", (ctx.guild.id, p["role"])).rowcount):
        raise modules.ValidationError("Для этой роли префикса нет")
    return "Правило удалено"


_USER = {"key": "user", "label": "Участник (ID)", "type": "user"}
_ROLE = {"key": "role", "label": "Роль", "type": "role"}
STICKY.actions.append(modules.Action("clear", "Забыть роли участника", _clear_sticky, params=[_USER], danger=True))
TEMP.actions.extend([
    modules.Action("add", "Выдать роль на время", _temp_add, params=[_USER, _ROLE, {"key": "duration", "label": "Срок (30m, 2h, 7d)", "type": "text"}]),
    modules.Action("remove", "Снять сейчас", _temp_remove, params=[_USER, _ROLE]),
])
PREFIX.actions.extend([
    modules.Action("add", "Добавить правило", _prefix_add, params=[_ROLE, {"key": "prefix", "label": "Префикс (например [АДМ])", "type": "text"}]),
    modules.Action("remove", "Удалить правило", _prefix_remove, params=[_ROLE]),
])
