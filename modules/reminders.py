"""
Reminders -- /remind <when> <text>: the bot DMs you (or pings you in the
channel if your DMs are closed) when the time comes. <when> is a duration
(10m, 2h30m, 1d), a time of day (20:30, MSK) or a date (25.12 18:00).
"""

import datetime
import re
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import db, embeds, modules
from modules.moderation import human_duration, parse_duration

db.register_schema("""
CREATE TABLE IF NOT EXISTS reminders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    channel_id INTEGER,
    text       TEXT NOT NULL,
    due_ts     INTEGER NOT NULL,
    dm         INTEGER NOT NULL DEFAULT 1,
    created_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS reminders_due ON reminders (due_ts);
""")

MODULE = modules.register(modules.Module(
    key="reminders", title="Напоминания", icon="⏰", category="community", default_enabled=True,
    description="Участники ставят себе напоминания командой /remind — бот напишет в нужный момент.",
    fields=[
        {"key": "max_per_user", "label": "Напоминаний на человека", "type": "number", "default": 10, "min": 1, "max": 100},
        {"key": "max_days", "label": "Максимальный срок, дней", "type": "number", "default": 365, "min": 1, "max": 3650},
    ],
    tables=[{"id": "list", "title": "Активные напоминания", "columns": [
        {"key": "id", "label": "ID"}, {"key": "user", "label": "Кому"}, {"key": "text", "label": "Текст"},
        {"key": "due_ts", "label": "Когда", "format": "time"}]}],
))

MSK = datetime.timezone(datetime.timedelta(hours=3))
_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")
_DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\s+(\d{1,2}):(\d{2})$")


def parse_when(text: str, now: datetime.datetime | None = None) -> int | None:
    """Timestamp for '10m' / '20:30' / '25.12 18:00' / '25.12.2027 18:00' (times are MSK), else None."""
    now = (now or datetime.datetime.now(MSK)).astimezone(MSK)
    text = (text or "").strip().lower()
    secs = parse_duration(text)
    if secs is not None:
        return int(now.timestamp()) + secs
    try:
        if m := _TIME.match(text):
            t = now.replace(hour=int(m[1]), minute=int(m[2]), second=0, microsecond=0)
            return int((t if t > now else t + datetime.timedelta(days=1)).timestamp())
        if m := _DATE.match(text):
            day, month, year, hh, mm = int(m[1]), int(m[2]), m[3], int(m[4]), int(m[5])
            t = datetime.datetime(int(year) if year else now.year, month, day, hh, mm, tzinfo=MSK)
            if not year and t <= now:
                t = t.replace(year=now.year + 1)
            return int(t.timestamp())
    except ValueError:
        return None
    return None


def count_for(guild_id: int, user_id: int) -> int:
    return db.query("SELECT COUNT(*) AS n FROM reminders WHERE guild_id=? AND user_id=?", (guild_id, user_id))[0]["n"]


def add(guild_id, user_id, channel_id, text, due_ts, dm) -> int:
    return db.execute("INSERT INTO reminders (guild_id, user_id, channel_id, text, due_ts, dm, created_ts) VALUES (?,?,?,?,?,?,?)",
                      (guild_id, user_id, channel_id, text, due_ts, int(dm), int(time.time()))).lastrowid


def due(now: int) -> list[dict]:
    return [dict(r) for r in db.query("SELECT * FROM reminders WHERE due_ts<=? ORDER BY due_ts", (now,))]


def mine(guild_id: int, user_id: int) -> list[dict]:
    return [dict(r) for r in db.query("SELECT * FROM reminders WHERE guild_id=? AND user_id=? ORDER BY due_ts", (guild_id, user_id))]


def cancel(rid: int, guild_id: int, user_id: int | None = None) -> bool:
    q, p = "DELETE FROM reminders WHERE id=? AND guild_id=?", [rid, guild_id]
    if user_id is not None:
        q += " AND user_id=?"; p.append(user_id)
    return db.execute(q, tuple(p)).rowcount > 0


async def deliver(bot, r: dict) -> None:
    guild = bot.get_guild(r["guild_id"])
    user = bot.get_user(r["user_id"]) or (guild.get_member(r["user_id"]) if guild else None)
    text = f"⏰ **Напоминание:** {r['text']}"
    if r["dm"] and user is not None:
        try:
            await user.send(text)
            return
        except (discord.Forbidden, discord.HTTPException):
            pass
    ch = guild.get_channel(r["channel_id"]) if guild and r["channel_id"] else None
    if ch is not None:
        try:
            await ch.send(f"<@{r['user_id']}> {text}", allowed_mentions=discord.AllowedMentions(users=[discord.Object(r["user_id"])]))
        except (discord.Forbidden, discord.HTTPException):
            pass


class Reminders(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.loop.start()

    async def cog_unload(self):
        self.loop.cancel()

    @app_commands.command(name="remind", description="Поставить напоминание")
    @app_commands.describe(when="Когда: 10m, 2h30m, 1d, 20:30 или 25.12 18:00 (МСК)", text="О чём напомнить",
                           dm="Написать в личку (иначе — в этом канале)")
    @app_commands.guild_only()
    async def remind(self, interaction: discord.Interaction, when: str, text: app_commands.Range[str, 1, 500], dm: bool = True):
        cfg = modules.get_config(interaction.guild_id, "reminders")
        if not cfg["enabled"]:
            return await interaction.response.send_message("Напоминания выключены.", ephemeral=True)
        ts = parse_when(when)
        now = int(time.time())
        if ts is None or ts <= now + 4:
            return await interaction.response.send_message("❌ Время вида `10m`, `2h30m`, `20:30` или `25.12 18:00` (в будущем).", ephemeral=True)
        if ts - now > cfg["max_days"] * 86400:
            return await interaction.response.send_message(f"❌ Не дальше чем на {cfg['max_days']} дн.", ephemeral=True)
        if await db.run(count_for, interaction.guild_id, interaction.user.id) >= cfg["max_per_user"]:
            return await interaction.response.send_message(f"❌ Лимит: {cfg['max_per_user']} напоминаний.", ephemeral=True)
        rid = await db.run(add, interaction.guild_id, interaction.user.id, interaction.channel_id, text, ts, dm)
        await interaction.response.send_message(f"✅ Напомню <t:{ts}:R> (#{rid}).", ephemeral=True)

    @app_commands.command(name="reminders", description="Мои напоминания")
    @app_commands.guild_only()
    async def reminders(self, interaction: discord.Interaction):
        rows = await db.run(mine, interaction.guild_id, interaction.user.id)
        if not rows:
            return await interaction.response.send_message("Напоминаний нет.", ephemeral=True)
        lines = [f"`#{r['id']}` <t:{r['due_ts']}:f> — {embeds.clip(r['text'], 80)}" for r in rows]
        await interaction.response.send_message(embed=embeds.make("Ваши напоминания", "\n".join(lines)), ephemeral=True)

    @app_commands.command(name="reminder_cancel", description="Отменить напоминание")
    @app_commands.guild_only()
    async def reminder_cancel(self, interaction: discord.Interaction, id: int):
        ok = await db.run(cancel, id, interaction.guild_id, interaction.user.id)
        await interaction.response.send_message("✅ Отменено." if ok else "❌ Такого вашего напоминания нет.", ephemeral=True)

    @tasks.loop(seconds=20)
    async def loop(self):
        for r in await db.run(due, int(time.time())):
            await db.run(cancel, r["id"], r["guild_id"])      # drop first: a delivery error must never re-fire forever
            await deliver(self.bot, r)

    @loop.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @loop.error
    async def _loop_error(self, error):
        print(f"WARNING: reminders loop crashed, restarting: {error}")
        if not self.loop.is_running():
            self.loop.start()


def _table(guild):
    out = []
    for r in db.query("SELECT * FROM reminders WHERE guild_id=? ORDER BY due_ts LIMIT 200", (guild.id,)):
        m = guild.get_member(r["user_id"])
        out.append({"id": r["id"], "user": f"{m.display_name} ({r['user_id']})" if m else f"[{r['user_id']}]",
                    "text": embeds.clip(r["text"], 100), "due_ts": r["due_ts"]})
    return out


modules.register_table("reminders", "list", _table)


async def _act_cancel(ctx, p):
    if not await db.run(cancel, int(p["id"]), ctx.guild.id):
        raise modules.ValidationError("Напоминание с таким ID не найдено")
    return "Напоминание удалено"


MODULE.actions.append(modules.Action("cancel", "Удалить напоминание", _act_cancel, params=[
    {"key": "id", "label": "ID напоминания", "type": "number", "min": 1}], danger=True))


async def setup(bot):
    await bot.add_cog(Reminders(bot))
