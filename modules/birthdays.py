"""
Birthdays -- members save their birthday with /birthday set DD.MM[.YYYY];
every day from the chosen hour (MSK) the bot congratulates them in a channel
and, if configured, gives a birthday role for that day.
"""

import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import db, embeds, modules

db.register_schema("""
CREATE TABLE IF NOT EXISTS birthdays (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    day      INTEGER NOT NULL,
    month    INTEGER NOT NULL,
    year     INTEGER,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS birthday_state (
    guild_id INTEGER PRIMARY KEY,
    last_ymd TEXT
);
CREATE TABLE IF NOT EXISTS birthday_roles (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    ymd      TEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);
""")

MODULE = modules.register(modules.Module(
    key="birthdays", title="Дни рождения", icon="🎂", category="community",
    description="Участники сохраняют дату командой /birthday set — бот поздравит в канале и выдаст роль на день.",
    fields=[
        {"key": "channel", "label": "Канал поздравлений", "type": "channel", "kind": "text", "default": None},
        {"key": "role", "label": "Роль именинника на день", "type": "role", "default": None},
        {"key": "hour", "label": "Во сколько поздравлять (час, МСК)", "type": "number", "default": 9, "min": 0, "max": 23},
        {"key": "message", "label": "Текст поздравления", "type": "text", "default": "🎂 Сегодня день рождения у {user}! Поздравляем!",
         "help": "{user} {age}"},
    ],
    tables=[{"id": "list", "title": "Дни рождения", "columns": [
        {"key": "user", "label": "Участник"}, {"key": "date", "label": "Дата"}, {"key": "next", "label": "Ближайший"}]}],
))

MSK = datetime.timezone(datetime.timedelta(hours=3))


def parse_date(text: str) -> tuple[int, int, int | None] | None:
    parts = (text or "").strip().replace("/", ".").replace("-", ".").split(".")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) not in (2, 3):
        return None
    day, month = nums[0], nums[1]
    year = nums[2] if len(nums) == 3 else None
    now_year = datetime.datetime.now(MSK).year
    if year is not None and not 1900 <= year <= now_year:
        return None
    try:
        datetime.date(year or 2000, month, day)          # 2000 is a leap year: 29.02 stays valid without a year
    except ValueError:
        return None
    return day, month, year


def is_today(day: int, month: int, today: datetime.date) -> bool:
    if (day, month) == (29, 2) and not _leap(today.year):
        return (today.day, today.month) == (28, 2)       # leap-day babies celebrate on 28.02 in common years
    return (day, month) == (today.day, today.month)


def _leap(y: int) -> bool:
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def next_date(day: int, month: int, today: datetime.date) -> datetime.date:
    for year in (today.year, today.year + 1, today.year + 2, today.year + 4):
        try:
            d = datetime.date(year, month, day)
        except ValueError:
            d = datetime.date(year, 2, 28)
        if d >= today:
            return d
    return today


def save(guild_id, user_id, day, month, year) -> None:
    db.execute("INSERT INTO birthdays (guild_id, user_id, day, month, year) VALUES (?,?,?,?,?) "
               "ON CONFLICT(guild_id, user_id) DO UPDATE SET day=excluded.day, month=excluded.month, year=excluded.year",
               (guild_id, user_id, day, month, year))


def remove(guild_id, user_id) -> bool:
    return db.execute("DELETE FROM birthdays WHERE guild_id=? AND user_id=?", (guild_id, user_id)).rowcount > 0


def all_rows(guild_id) -> list[dict]:
    return [dict(r) for r in db.query("SELECT * FROM birthdays WHERE guild_id=?", (guild_id,))]


def todays(guild_id: int, today: datetime.date) -> list[dict]:
    return [r for r in all_rows(guild_id) if is_today(r["day"], r["month"], today)]


def upcoming(guild_id: int, today: datetime.date, limit: int = 8) -> list[tuple[datetime.date, dict]]:
    return sorted(((next_date(r["day"], r["month"], today), r) for r in all_rows(guild_id)), key=lambda t: t[0])[:limit]


class Birthdays(commands.Cog):
    group = app_commands.Group(name="birthday", description="Дни рождения", guild_only=True)

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.loop.start()

    async def cog_unload(self):
        self.loop.cancel()

    @group.command(name="set", description="Сохранить дату рождения")
    @app_commands.describe(date="ДД.ММ или ДД.ММ.ГГГГ (год можно не указывать)")
    async def set(self, interaction: discord.Interaction, date: str):
        if not modules.is_enabled(interaction.guild_id, "birthdays"):
            return await interaction.response.send_message("Модуль выключен.", ephemeral=True)
        parsed = parse_date(date)
        if parsed is None:
            return await interaction.response.send_message("❌ Дата вида `25.12` или `25.12.1999`.", ephemeral=True)
        await db.run(save, interaction.guild_id, interaction.user.id, *parsed)
        await interaction.response.send_message(f"✅ Запомнил: {parsed[0]:02d}.{parsed[1]:02d}", ephemeral=True)

    @group.command(name="remove", description="Удалить мою дату рождения")
    async def remove(self, interaction: discord.Interaction):
        ok = await db.run(remove, interaction.guild_id, interaction.user.id)
        await interaction.response.send_message("✅ Удалено." if ok else "У вас не было сохранённой даты.", ephemeral=True)

    @group.command(name="upcoming", description="Ближайшие дни рождения")
    async def upcoming(self, interaction: discord.Interaction):
        today = datetime.datetime.now(MSK).date()
        rows = await db.run(upcoming, interaction.guild_id, today)
        if not rows:
            return await interaction.response.send_message("Пока никто не сохранил дату.", ephemeral=True)
        lines = [f"🎂 <@{r['user_id']}> — {d.day:02d}.{d.month:02d}" + (" (сегодня!)" if d == today else "") for d, r in rows]
        await interaction.response.send_message(embed=embeds.make("Ближайшие дни рождения", "\n".join(lines)),
                                                allowed_mentions=discord.AllowedMentions.none())

    async def run_guild(self, guild: discord.Guild, now: datetime.datetime) -> None:
        cfg = modules.get_config(guild.id, "birthdays")
        if not cfg["enabled"]:
            return
        today = now.date(); ymd = today.isoformat()
        role = guild.get_role(cfg["role"]) if cfg["role"] else None
        # take yesterday's birthday role away (any hour, so nothing stays stuck)
        for r in await db.run(db.query, "SELECT user_id FROM birthday_roles WHERE guild_id=? AND ymd<?", (guild.id, ymd)):
            m = guild.get_member(r["user_id"])
            if m and role and role in m.roles:
                try:
                    await m.remove_roles(role, reason="День рождения закончился")
                except discord.Forbidden:
                    pass
            await db.run(db.execute, "DELETE FROM birthday_roles WHERE guild_id=? AND user_id=?", (guild.id, r["user_id"]))
        if now.hour < cfg["hour"]:
            return
        last = await db.run(db.query, "SELECT last_ymd FROM birthday_state WHERE guild_id=?", (guild.id,))
        if last and last[0]["last_ymd"] == ymd:
            return
        await db.run(db.execute, "INSERT INTO birthday_state (guild_id, last_ymd) VALUES (?,?) "
                     "ON CONFLICT(guild_id) DO UPDATE SET last_ymd=excluded.last_ymd", (guild.id, ymd))
        ch = guild.get_channel(cfg["channel"]) if cfg["channel"] else None
        for r in await db.run(todays, guild.id, today):
            m = guild.get_member(r["user_id"])
            if m is None:
                continue
            if role:
                try:
                    await m.add_roles(role, reason="День рождения")
                    await db.run(db.execute, "INSERT OR REPLACE INTO birthday_roles (guild_id, user_id, ymd) VALUES (?,?,?)", (guild.id, m.id, ymd))
                except discord.Forbidden:
                    pass
            if ch:
                age = f"{today.year - r['year']}" if r["year"] else ""
                text = (cfg["message"] or "").replace("{user}", m.mention).replace("{age}", age)
                try:
                    await ch.send(text, allowed_mentions=discord.AllowedMentions(users=[m]))
                except (discord.Forbidden, discord.HTTPException):
                    pass

    @tasks.loop(minutes=10)
    async def loop(self):
        now = datetime.datetime.now(MSK)
        for guild in self.bot.guilds:
            await self.run_guild(guild, now)

    @loop.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @loop.error
    async def _loop_error(self, error):
        print(f"WARNING: birthdays loop crashed, restarting: {error}")
        if not self.loop.is_running():
            self.loop.start()


def _table(guild):
    today = datetime.datetime.now(MSK).date()
    out = []
    for d, r in upcoming(guild.id, today, 500):
        m = guild.get_member(r["user_id"])
        out.append({"user": f"{m.display_name} ({r['user_id']})" if m else f"[{r['user_id']}]",
                    "date": f"{r['day']:02d}.{r['month']:02d}" + (f".{r['year']}" if r["year"] else ""),
                    "next": d.strftime("%d.%m.%Y")})
    return out


modules.register_table("birthdays", "list", _table)


async def _act_set(ctx, p):
    parsed = parse_date(p["date"])
    if parsed is None:
        raise modules.ValidationError("Дата вида 25.12 или 25.12.1999")
    if ctx.guild.get_member(p["user"]) is None:
        raise modules.ValidationError("Участник не найден на сервере")
    await db.run(save, ctx.guild.id, p["user"], *parsed)
    return f"Сохранено: {parsed[0]:02d}.{parsed[1]:02d}"


async def _act_remove(ctx, p):
    if not await db.run(remove, ctx.guild.id, p["user"]):
        raise modules.ValidationError("У участника нет сохранённой даты")
    return "Удалено"


MODULE.actions.extend([
    modules.Action("set", "Задать дату участнику", _act_set, params=[
        {"key": "user", "label": "Участник (ID)", "type": "user"}, {"key": "date", "label": "Дата (ДД.ММ или ДД.ММ.ГГГГ)", "type": "text"}]),
    modules.Action("remove", "Удалить дату участника", _act_remove, params=[{"key": "user", "label": "Участник (ID)", "type": "user"}]),
])


async def setup(bot):
    await bot.add_cog(Birthdays(bot))
