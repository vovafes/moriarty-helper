"""
Team tools:

* Activity check -- counts messages and voice minutes of team members per day
  and posts a daily report of who didn't reach the minimum. Members marked
  AFK / inactive in the family panels (legacy lists) are excluded.
* Team rating -- /rate <member> <1-5> [comment]: anyone can rate a team member,
  the panel shows averages and comments.
"""

import datetime
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import db, embeds, modules

db.register_schema("""
CREATE TABLE IF NOT EXISTS team_activity (
    guild_id  INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    ymd       TEXT    NOT NULL,
    msgs      INTEGER NOT NULL DEFAULT 0,
    voice_min INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id, ymd)
);
CREATE TABLE IF NOT EXISTS activity_state (
    guild_id INTEGER PRIMARY KEY,
    last_ymd TEXT
);
CREATE TABLE IF NOT EXISTS team_ratings (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id  INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    rater_id  INTEGER NOT NULL,
    stars     INTEGER NOT NULL,
    comment   TEXT,
    ts        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS team_ratings_target ON team_ratings (guild_id, target_id);
""")

MSK = datetime.timezone(datetime.timedelta(hours=3))

ACTIVITY = modules.register(modules.Module(
    key="activity_check", title="Проверка активности", icon="📋", category="team", default_enabled=False,
    description="Считает сообщения и минуты в войсе у команды и каждый день публикует, кто не набрал минимум.",
    fields=[
        {"key": "team_roles", "label": "Роли команды", "type": "roles", "default": [], "group": "Кого проверяем"},
        {"key": "skip_absent", "label": "Не считать тех, кто в АФК / инактиве", "type": "bool", "default": True, "group": "Кого проверяем",
         "help": "Берётся из панелей «АФК и инактив»"},
        {"key": "min_messages", "label": "Минимум сообщений в день", "type": "number", "default": 20, "min": 0, "max": 10000, "group": "Норма",
         "help": "Достаточно выполнить ИЛИ норму по сообщениям, ИЛИ по голосу"},
        {"key": "min_voice", "label": "Минимум минут в голосовом", "type": "number", "default": 30, "min": 0, "max": 1440, "group": "Норма"},
        {"key": "report_channel", "label": "Канал для отчёта", "type": "channel", "kind": "text", "default": None, "group": "Отчёт"},
        {"key": "hour", "label": "Во сколько публиковать отчёт за вчера (час, МСК)", "type": "number", "default": 10, "min": 0, "max": 23, "group": "Отчёт"},
        {"key": "ping_role", "label": "Кого тегать в отчёте", "type": "role", "default": None, "group": "Отчёт"},
    ],
    tables=[{"id": "today", "title": "Активность за сегодня и вчера", "columns": [
        {"key": "user", "label": "Участник"}, {"key": "today", "label": "Сегодня (сообщ./мин)"},
        {"key": "yesterday", "label": "Вчера (сообщ./мин)"}, {"key": "status", "label": "Вчера по норме"}]}],
))
RATING = modules.register(modules.Module(
    key="team_rating", title="Оценки команды", icon="⭐", category="team", default_enabled=False,
    description="Участники оценивают сотрудников командой /rate (1–5 звёзд и комментарий).",
    fields=[
        {"key": "team_roles", "label": "Кого можно оценивать (роли)", "type": "roles", "default": []},
        {"key": "cooldown_hours", "label": "Повторная оценка того же человека, часов", "type": "number", "default": 24, "min": 0, "max": 720},
        {"key": "log_channel", "label": "Канал для новых оценок", "type": "channel", "kind": "text", "default": None},
    ],
    tables=[{"id": "summary", "title": "Рейтинг", "columns": [
        {"key": "user", "label": "Сотрудник"}, {"key": "avg", "label": "Средняя"}, {"key": "count", "label": "Оценок"}]},
        {"id": "recent", "title": "Последние оценки", "columns": [
        {"key": "target", "label": "Кому"}, {"key": "stars", "label": "★"}, {"key": "comment", "label": "Комментарий"},
        {"key": "ts", "label": "Когда", "format": "time"}]}],
))


# ── activity ────────────────────────────────────────────────────────────────

def today_ymd(now: datetime.datetime | None = None) -> str:
    return (now or datetime.datetime.now(MSK)).date().isoformat()


def bump(guild_id, user_id, ymd, msgs=0, voice=0) -> None:
    db.execute("INSERT INTO team_activity (guild_id, user_id, ymd, msgs, voice_min) VALUES (?,?,?,?,?) "
               "ON CONFLICT(guild_id, user_id, ymd) DO UPDATE SET msgs=msgs+?, voice_min=voice_min+?",
               (guild_id, user_id, ymd, msgs, voice, msgs, voice))


def stats_for(guild_id, ymd) -> dict[int, tuple[int, int]]:
    return {r["user_id"]: (r["msgs"], r["voice_min"]) for r in
            db.query("SELECT user_id, msgs, voice_min FROM team_activity WHERE guild_id=? AND ymd=?", (guild_id, ymd))}


def met_norm(msgs: int, voice: int, min_msgs: int, min_voice: int) -> bool:
    if min_msgs <= 0 and min_voice <= 0:
        return True
    return (min_msgs > 0 and msgs >= min_msgs) or (min_voice > 0 and voice >= min_voice)


def team_members(guild: discord.Guild, role_ids) -> list[discord.Member]:
    wanted = set(role_ids or [])
    return [m for m in guild.members if not m.bot and any(r.id in wanted for r in m.roles)]


def absent_ids(guild_id: int) -> set[int]:
    try:
        from legacy.state import afk_list, inactive_list
    except Exception:
        return set()
    return set(afk_list.get(guild_id, {})) | set(inactive_list.get(guild_id, {}))


def build_report(guild, cfg, ymd) -> tuple[list[str], int, int]:
    """(lines of members who missed the norm, passed count, excused count)."""
    stats = stats_for(guild.id, ymd)
    excused = absent_ids(guild.id) if cfg["skip_absent"] else set()
    bad, ok, skipped = [], 0, 0
    for m in team_members(guild, cfg["team_roles"]):
        if m.id in excused:
            skipped += 1
            continue
        msgs, voice = stats.get(m.id, (0, 0))
        if met_norm(msgs, voice, cfg["min_messages"], cfg["min_voice"]):
            ok += 1
        else:
            bad.append(f"{m.mention} — {msgs} сообщ., {voice} мин")
    return bad, ok, skipped


class Team(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.voice_loop.start()
        self.report_loop.start()

    async def cog_unload(self):
        self.voice_loop.cancel()
        self.report_loop.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or not hasattr(message.author, "roles"):
            return
        cfg = modules.get_config(message.guild.id, "activity_check")
        if cfg["enabled"] and any(r.id in (cfg["team_roles"] or []) for r in message.author.roles):
            await db.run(bump, message.guild.id, message.author.id, today_ymd(), 1, 0)

    @tasks.loop(seconds=60)
    async def voice_loop(self):
        ymd = today_ymd()
        for guild in self.bot.guilds:
            cfg = modules.get_config(guild.id, "activity_check")
            if not cfg["enabled"]:
                continue
            wanted = set(cfg["team_roles"] or [])
            for vc in guild.voice_channels:
                for m in vc.members:
                    vs = m.voice
                    if m.bot or vs is None or (vs.self_mute and vs.self_deaf) or vs.afk:
                        continue
                    if any(r.id in wanted for r in m.roles):
                        await db.run(bump, guild.id, m.id, ymd, 0, 1)

    async def post_report(self, guild: discord.Guild, ymd: str, cfg: dict) -> bool:
        ch = guild.get_channel(cfg["report_channel"]) if cfg["report_channel"] else None
        if ch is None:
            return False
        bad, ok, skipped = build_report(guild, cfg, ymd)
        e = embeds.make(f"📋 Активность команды за {datetime.date.fromisoformat(ymd).strftime('%d.%m.%Y')}",
                        color=0xED4245 if bad else 0x3BA55D)
        e.add_field(name=f"❌ Не выполнили норму ({len(bad)})", value=embeds.clip("\n".join(bad), 1000) or "Все молодцы!", inline=False)
        e.add_field(name="Итог", value=f"✅ выполнили: {ok} · ⏸ освобождены: {skipped}")
        e.set_footer(text=f"MORIARTY · норма: {cfg['min_messages']} сообщ. или {cfg['min_voice']} мин в войсе")
        role = guild.get_role(cfg["ping_role"]) if cfg["ping_role"] else None
        try:
            await ch.send(role.mention if role else None, embed=e,
                          allowed_mentions=discord.AllowedMentions(roles=[role] if role else [], users=False))
        except (discord.Forbidden, discord.HTTPException):
            return False
        return True

    async def run_reports(self, now: datetime.datetime) -> None:
        for guild in self.bot.guilds:
            cfg = modules.get_config(guild.id, "activity_check")
            if not cfg["enabled"] or now.hour < cfg["hour"]:
                continue
            ymd_today = now.date().isoformat()
            last = await db.run(db.query, "SELECT last_ymd FROM activity_state WHERE guild_id=?", (guild.id,))
            if last and last[0]["last_ymd"] == ymd_today:
                continue
            await db.run(db.execute, "INSERT INTO activity_state (guild_id, last_ymd) VALUES (?,?) "
                         "ON CONFLICT(guild_id) DO UPDATE SET last_ymd=excluded.last_ymd", (guild.id, ymd_today))
            await self.post_report(guild, (now.date() - datetime.timedelta(days=1)).isoformat(), cfg)

    @tasks.loop(minutes=10)
    async def report_loop(self):
        await self.run_reports(datetime.datetime.now(MSK))

    @voice_loop.before_loop
    @report_loop.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @voice_loop.error
    async def _verr(self, error):
        print(f"WARNING: activity voice_loop crashed, restarting: {error}")
        if not self.voice_loop.is_running():
            self.voice_loop.start()

    @report_loop.error
    async def _rerr(self, error):
        print(f"WARNING: activity report_loop crashed, restarting: {error}")
        if not self.report_loop.is_running():
            self.report_loop.start()

    # ── rating ──────────────────────────────────────────────────────────────
    @app_commands.command(name="rate", description="Оценить сотрудника команды")
    @app_commands.describe(member="Кого оцениваем", stars="От 1 до 5", comment="Комментарий (необязательно)")
    @app_commands.guild_only()
    async def rate(self, interaction: discord.Interaction, member: discord.Member, stars: app_commands.Range[int, 1, 5],
                   comment: app_commands.Range[str, 0, 500] = ""):
        cfg = modules.get_config(interaction.guild_id, "team_rating")
        if not cfg["enabled"]:
            return await interaction.response.send_message("Оценки выключены.", ephemeral=True)
        if member.id == interaction.user.id or member.bot:
            return await interaction.response.send_message("❌ Нельзя оценить себя или бота.", ephemeral=True)
        if cfg["team_roles"] and not any(r.id in cfg["team_roles"] for r in member.roles):
            return await interaction.response.send_message("❌ Этот участник не из команды.", ephemeral=True)
        last = await db.run(last_rating, interaction.guild_id, member.id, interaction.user.id)
        wait = cfg["cooldown_hours"] * 3600 - (time.time() - last)
        if wait > 0:
            return await interaction.response.send_message(f"❌ Этого человека можно оценить снова через {int(wait // 3600) + 1} ч.", ephemeral=True)
        await db.run(add_rating, interaction.guild_id, member.id, interaction.user.id, stars, comment)
        ch = interaction.guild.get_channel(cfg["log_channel"]) if cfg["log_channel"] else None
        if ch:
            try:
                await ch.send(embed=embeds.make("⭐ Новая оценка", f"{member.mention}: {'★' * stars}{'☆' * (5 - stars)}\n{comment or '*без комментария*'}"),
                              allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException):
                pass
        await interaction.response.send_message("✅ Спасибо за оценку!", ephemeral=True)


def last_rating(guild_id, target_id, rater_id) -> int:
    r = db.query("SELECT MAX(ts) AS t FROM team_ratings WHERE guild_id=? AND target_id=? AND rater_id=?", (guild_id, target_id, rater_id))[0]["t"]
    return r or 0


def add_rating(guild_id, target_id, rater_id, stars, comment) -> None:
    db.execute("INSERT INTO team_ratings (guild_id, target_id, rater_id, stars, comment, ts) VALUES (?,?,?,?,?,?)",
               (guild_id, target_id, rater_id, stars, comment, int(time.time())))


def summary(guild_id) -> list[dict]:
    return [dict(r) for r in db.query(
        "SELECT target_id, ROUND(AVG(stars), 2) AS avg, COUNT(*) AS count FROM team_ratings WHERE guild_id=? GROUP BY target_id ORDER BY avg DESC, count DESC",
        (guild_id,))]


# ── panel ───────────────────────────────────────────────────────────────────

def _name(guild, uid):
    m = guild.get_member(uid)
    return f"{m.display_name} ({uid})" if m else f"[{uid}]"


def _activity_table(guild):
    cfg = modules.get_config(guild.id, "activity_check")
    now = datetime.datetime.now(MSK)
    t, y = stats_for(guild.id, now.date().isoformat()), stats_for(guild.id, (now.date() - datetime.timedelta(days=1)).isoformat())
    excused = absent_ids(guild.id) if cfg["skip_absent"] else set()
    out = []
    for m in team_members(guild, cfg["team_roles"]):
        ym, yv = y.get(m.id, (0, 0)); tm, tv = t.get(m.id, (0, 0))
        status = "в АФК/инактиве" if m.id in excused else ("да" if met_norm(ym, yv, cfg["min_messages"], cfg["min_voice"]) else "нет")
        out.append({"user": f"{m.display_name} ({m.id})", "today": f"{tm} / {tv}", "yesterday": f"{ym} / {yv}", "status": status})
    return sorted(out, key=lambda r: r["status"])


def _summary_table(guild):
    return [{"user": _name(guild, r["target_id"]), "avg": r["avg"], "count": r["count"]} for r in summary(guild.id)]


def _recent_table(guild):
    return [{"target": _name(guild, r["target_id"]), "stars": "★" * r["stars"], "comment": r["comment"] or "", "ts": r["ts"]}
            for r in db.query("SELECT * FROM team_ratings WHERE guild_id=? ORDER BY id DESC LIMIT 100", (guild.id,))]


modules.register_table("activity_check", "today", _activity_table)
modules.register_table("team_rating", "summary", _summary_table)
modules.register_table("team_rating", "recent", _recent_table)


async def _report_now(ctx, p):
    cfg = modules.get_config(ctx.guild.id, "activity_check")
    day = datetime.date.today() - datetime.timedelta(days=1) if p["day"] == "yesterday" else datetime.date.today()
    if not await Team(ctx.bot).post_report(ctx.guild, day.isoformat(), cfg):
        raise modules.ValidationError("Не удалось отправить: выберите канал отчёта (и проверьте права бота)")
    return "Отчёт отправлен"


async def _reset_rating(ctx, p):
    n = await db.run(lambda: db.execute("DELETE FROM team_ratings WHERE guild_id=? AND target_id=?", (ctx.guild.id, p["user"])).rowcount)
    if not n:
        raise modules.ValidationError("У этого сотрудника нет оценок")
    return f"Удалено оценок: {n}"


ACTIVITY.actions.append(modules.Action("report", "Отправить отчёт сейчас", _report_now, params=[
    {"key": "day", "label": "За какой день", "type": "select", "default": "yesterday",
     "options": [{"value": "yesterday", "label": "Вчера"}, {"value": "today", "label": "Сегодня (на текущий момент)"}]}]))
RATING.actions.append(modules.Action("reset", "Сбросить оценки сотрудника", _reset_rating, params=[
    {"key": "user", "label": "Сотрудник (ID)", "type": "user"}], danger=True, confirm="Все оценки этого сотрудника будут удалены."))


async def setup(bot):
    await bot.add_cog(Team(bot))
