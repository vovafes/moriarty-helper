"""
Level System -- XP for messages (and optionally voice time), level-up
announcements and role rewards. /rank shows a member's progress,
/leaderboard the top ten. The panel has the full leaderboard and tools to
adjust or reset XP.

Curve (same as the popular bots): reaching level L+1 from L needs
5*L^2 + 50*L + 100 XP.
"""

import random
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import db, embeds, modules

db.register_schema("""
CREATE TABLE IF NOT EXISTS levels (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    xp       INTEGER NOT NULL DEFAULT 0,
    msgs     INTEGER NOT NULL DEFAULT 0,
    last_ts  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);
CREATE INDEX IF NOT EXISTS levels_rank ON levels (guild_id, xp DESC);
CREATE TABLE IF NOT EXISTS level_rewards (
    guild_id INTEGER NOT NULL,
    level    INTEGER NOT NULL,
    role_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, level)
);
""")

MODULE = modules.register(modules.Module(
    key="levels", title="Уровни", icon="⭐", category="community",
    description="Опыт за сообщения и голос, уровни, награды-роли, /rank и таблица лидеров.",
    fields=[
        {"key": "xp_min", "label": "Опыт за сообщение, от", "type": "number", "default": 15, "min": 0, "max": 1000, "group": "Начисление"},
        {"key": "xp_max", "label": "Опыт за сообщение, до", "type": "number", "default": 25, "min": 0, "max": 1000, "group": "Начисление"},
        {"key": "cooldown", "label": "Пауза между начислениями, сек", "type": "number", "default": 60, "min": 0, "max": 3600, "group": "Начисление"},
        {"key": "voice_xp", "label": "Опыт за минуту в голосовом", "type": "number", "default": 0, "min": 0, "max": 1000, "group": "Начисление",
         "help": "0 — голос не даёт опыта. Не начисляется в АФК-режиме (заглушён микрофон и звук)"},
        {"key": "ignored_channels", "label": "Каналы без опыта", "type": "channels", "default": [], "group": "Исключения"},
        {"key": "ignored_roles", "label": "Роли без опыта", "type": "roles", "default": [], "group": "Исключения"},
        {"key": "announce", "label": "Объявлять новый уровень", "type": "select", "default": "same", "group": "Объявления",
         "options": [{"value": "same", "label": "В том же канале"}, {"value": "channel", "label": "В отдельном канале"},
                     {"value": "off", "label": "Не объявлять"}]},
        {"key": "announce_channel", "label": "Канал объявлений", "type": "channel", "kind": "text", "default": None, "group": "Объявления"},
        {"key": "announce_text", "label": "Текст объявления", "type": "text", "default": "🎉 {user} достиг {level} уровня!", "group": "Объявления",
         "help": "{user} {level}"},
        {"key": "stack_roles", "label": "Сохранять роли прошлых уровней", "type": "bool", "default": True, "group": "Награды",
         "help": "Выкл — у участника остаётся только роль за самый высокий уровень"},
    ],
    tables=[
        {"id": "board", "title": "Таблица лидеров (топ-100)", "columns": [
            {"key": "rank", "label": "#"}, {"key": "user", "label": "Участник"}, {"key": "level", "label": "Уровень"},
            {"key": "xp", "label": "Опыт"}, {"key": "msgs", "label": "Сообщений"}]},
        {"id": "rewards", "title": "Награды за уровни", "columns": [{"key": "level", "label": "Уровень"}, {"key": "role", "label": "Роль"}]},
    ],
))


# ── curve ───────────────────────────────────────────────────────────────────

def xp_to_next(level: int) -> int:
    return 5 * level * level + 50 * level + 100


def total_xp(level: int) -> int:
    return sum(xp_to_next(l) for l in range(level))


def level_for(xp: int) -> int:
    level = 0
    while xp >= total_xp(level + 1):
        level += 1
    return level


def progress(xp: int) -> tuple[int, int, int]:
    """(level, xp into the level, xp the level needs)."""
    lvl = level_for(xp)
    return lvl, xp - total_xp(lvl), xp_to_next(lvl)


# ── storage ─────────────────────────────────────────────────────────────────

def get_xp(guild_id: int, user_id: int) -> tuple[int, int, int]:
    rows = db.query("SELECT xp, msgs, last_ts FROM levels WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    return (rows[0]["xp"], rows[0]["msgs"], rows[0]["last_ts"]) if rows else (0, 0, 0)


def add_xp(guild_id: int, user_id: int, amount: int, *, message: bool = False, now: int | None = None) -> tuple[int, int]:
    """Returns (level before, level after)."""
    with db._lock:
        xp, msgs, _ = get_xp(guild_id, user_id)
        new = max(0, xp + amount)
        db.execute(
            "INSERT INTO levels (guild_id, user_id, xp, msgs, last_ts) VALUES (?,?,?,?,?) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET xp=excluded.xp, msgs=msgs+?, last_ts=CASE WHEN ?=1 THEN excluded.last_ts ELSE last_ts END",
            (guild_id, user_id, new, 1 if message else 0, now or int(time.time()), 1 if message else 0, 1 if message else 0))
        return level_for(xp), level_for(new)


def set_xp(guild_id: int, user_id: int, xp: int) -> None:
    db.execute("INSERT INTO levels (guild_id, user_id, xp) VALUES (?,?,?) ON CONFLICT(guild_id, user_id) DO UPDATE SET xp=excluded.xp",
               (guild_id, user_id, max(0, xp)))


def rank_of(guild_id: int, user_id: int) -> int:
    xp, _, _ = get_xp(guild_id, user_id)
    return db.query("SELECT COUNT(*) AS n FROM levels WHERE guild_id=? AND xp>?", (guild_id, xp))[0]["n"] + 1


def top(guild_id: int, limit: int = 10) -> list[dict]:
    return [dict(r) for r in db.query("SELECT user_id, xp, msgs FROM levels WHERE guild_id=? AND xp>0 ORDER BY xp DESC LIMIT ?", (guild_id, limit))]


def rewards(guild_id: int) -> list[dict]:
    return [dict(r) for r in db.query("SELECT level, role_id FROM level_rewards WHERE guild_id=? ORDER BY level", (guild_id,))]


def roles_for_level(reward_rows: list[dict], level: int, stack: bool) -> tuple[list[int], list[int]]:
    """(roles the member should have, reward roles they should not)."""
    earned = [r for r in reward_rows if r["level"] <= level]
    if not earned:
        return [], [r["role_id"] for r in reward_rows]
    keep = [r["role_id"] for r in earned] if stack else [max(earned, key=lambda r: r["level"])["role_id"]]
    return keep, [r["role_id"] for r in reward_rows if r["role_id"] not in keep]


# ── cog ─────────────────────────────────────────────────────────────────────

class Levels(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.voice_loop.start()

    async def cog_unload(self):
        self.voice_loop.cancel()

    async def sync_roles(self, member: discord.Member, level: int, cfg: dict) -> None:
        rows = await db.run(rewards, member.guild.id)
        if not rows:
            return
        keep, drop = roles_for_level(rows, level, cfg["stack_roles"])
        have = {r.id for r in member.roles}
        add = [r for r in (member.guild.get_role(i) for i in keep) if r and r.id not in have]
        remove = [r for r in (member.guild.get_role(i) for i in drop) if r and r.id in have]
        try:
            if add:
                await member.add_roles(*add, reason="Награда за уровень")
            if remove:
                await member.remove_roles(*remove, reason="Награда за уровень")
        except discord.Forbidden:
            pass

    async def announce(self, member: discord.Member, level: int, cfg: dict, source_channel) -> None:
        if cfg["announce"] == "off":
            return
        ch = member.guild.get_channel(cfg["announce_channel"]) if cfg["announce"] == "channel" and cfg["announce_channel"] else source_channel
        if ch is None:
            return
        text = (cfg["announce_text"] or "").replace("{user}", member.mention).replace("{level}", str(level))
        try:
            await ch.send(text, allowed_mentions=discord.AllowedMentions(users=[member]))
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def grant(self, member: discord.Member, amount: int, cfg: dict, channel, *, message: bool) -> None:
        before, after = await db.run(add_xp, member.guild.id, member.id, amount, message=message)
        if after != before:
            await self.sync_roles(member, after, cfg)
            if after > before:
                await self.announce(member, after, cfg, channel)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        cfg = modules.get_config(message.guild.id, "levels")
        if not cfg["enabled"] or message.channel.id in (cfg["ignored_channels"] or []):
            return
        if any(r.id in (cfg["ignored_roles"] or []) for r in message.author.roles):
            return
        _, _, last = await db.run(get_xp, message.guild.id, message.author.id)
        now = int(time.time())
        if now - last < cfg["cooldown"]:
            return
        lo, hi = sorted((cfg["xp_min"], cfg["xp_max"]))
        await self.grant(message.author, random.randint(lo, hi), cfg, message.channel, message=True)

    @tasks.loop(seconds=60)
    async def voice_loop(self):
        for guild in self.bot.guilds:
            cfg = modules.get_config(guild.id, "levels")
            if not cfg["enabled"] or not cfg["voice_xp"]:
                continue
            for vc in guild.voice_channels:
                if vc.id in (cfg["ignored_channels"] or []):
                    continue
                humans = [m for m in vc.members if not m.bot]
                if len(humans) < 2:                 # nobody to talk to -> no free XP
                    continue
                for m in humans:
                    vs = m.voice
                    if vs is None or (vs.self_mute and vs.self_deaf) or vs.afk:
                        continue
                    if any(r.id in (cfg["ignored_roles"] or []) for r in m.roles):
                        continue
                    await self.grant(m, cfg["voice_xp"], cfg, None, message=False)

    @voice_loop.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @voice_loop.error
    async def _loop_error(self, error):
        print(f"WARNING: levels voice_loop crashed, restarting: {error}")
        if not self.voice_loop.is_running():
            self.voice_loop.start()

    @app_commands.command(name="rank", description="Ваш уровень и опыт")
    @app_commands.guild_only()
    async def rank(self, interaction: discord.Interaction, user: discord.Member | None = None):
        if not modules.is_enabled(interaction.guild_id, "levels"):
            return await interaction.response.send_message("Уровни на этом сервере выключены.", ephemeral=True)
        user = user or interaction.user
        xp, msgs, _ = await db.run(get_xp, interaction.guild_id, user.id)
        lvl, into, need = progress(xp)
        pos = await db.run(rank_of, interaction.guild_id, user.id)
        filled = int(10 * into / need)
        e = embeds.make(f"{user.display_name}", color=0xF1C40F)
        e.set_thumbnail(url=user.display_avatar.url)
        e.add_field(name="Уровень", value=str(lvl)); e.add_field(name="Место", value=f"#{pos}"); e.add_field(name="Сообщений", value=str(msgs))
        e.add_field(name="Опыт", value=f"{'🟨' * filled}{'⬛' * (10 - filled)}\n{into} / {need}  (всего {xp})", inline=False)
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="leaderboard", description="Топ-10 по уровню")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction):
        if not modules.is_enabled(interaction.guild_id, "levels"):
            return await interaction.response.send_message("Уровни на этом сервере выключены.", ephemeral=True)
        rows = await db.run(top, interaction.guild_id, 10)
        if not rows:
            return await interaction.response.send_message("Пока никто не набрал опыт.", ephemeral=True)
        medals = ["🥇", "🥈", "🥉"]
        lines = [f"{medals[i] if i < 3 else f'`{i + 1}.`'} <@{r['user_id']}> — уровень **{level_for(r['xp'])}** · {r['xp']} XP" for i, r in enumerate(rows)]
        await interaction.response.send_message(embed=embeds.make("🏆 Таблица лидеров", "\n".join(lines), 0xF1C40F),
                                                allowed_mentions=discord.AllowedMentions.none())


# ── panel ───────────────────────────────────────────────────────────────────

def _board(guild):
    out = []
    for i, r in enumerate(top(guild.id, 100), 1):
        m = guild.get_member(r["user_id"])
        out.append({"rank": i, "user": f"{m.display_name} ({r['user_id']})" if m else f"[{r['user_id']}]",
                    "level": level_for(r["xp"]), "xp": r["xp"], "msgs": r["msgs"]})
    return out


def _rewards_table(guild):
    return [{"level": r["level"], "role": getattr(guild.get_role(r["role_id"]), "name", f"[{r['role_id']}]")} for r in rewards(guild.id)]


modules.register_table("levels", "board", _board)
modules.register_table("levels", "rewards", _rewards_table)


async def _reward_add(ctx, p):
    role = ctx.guild.get_role(p["role"])
    if role is None or role.managed or role.is_default():
        raise modules.ValidationError("Выберите обычную роль")
    await db.run(db.execute, "INSERT INTO level_rewards (guild_id, level, role_id) VALUES (?,?,?) "
                 "ON CONFLICT(guild_id, level) DO UPDATE SET role_id=excluded.role_id", (ctx.guild.id, int(p["level"]), role.id))
    return f"С уровня {int(p['level'])} выдаётся «{role.name}»"


async def _reward_remove(ctx, p):
    cur = await db.run(db.execute, "DELETE FROM level_rewards WHERE guild_id=? AND level=?", (ctx.guild.id, int(p["level"])))
    if not cur.rowcount:
        raise modules.ValidationError("Для этого уровня награды нет")
    return "Награда удалена"


async def _set_xp(ctx, p):
    member = ctx.guild.get_member(p["user"])
    if member is None:
        raise modules.ValidationError("Участник не найден на сервере")
    await db.run(set_xp, ctx.guild.id, member.id, int(p["xp"]))
    cfg = modules.get_config(ctx.guild.id, "levels")
    await Levels(ctx.bot).sync_roles(member, level_for(int(p["xp"])), cfg)
    return f"{member.display_name}: {int(p['xp'])} XP (уровень {level_for(int(p['xp']))})"


async def _reset_all(ctx, p):
    await db.run(db.execute, "DELETE FROM levels WHERE guild_id=?", (ctx.guild.id,))
    return "Весь опыт на сервере сброшен"


MODULE.actions.extend([
    modules.Action("reward_add", "Добавить награду", _reward_add, params=[
        {"key": "level", "label": "С какого уровня", "type": "number", "min": 1, "max": 500},
        {"key": "role", "label": "Роль", "type": "role"}]),
    modules.Action("reward_remove", "Удалить награду", _reward_remove, params=[
        {"key": "level", "label": "Уровень награды", "type": "number", "min": 1, "max": 500}]),
    modules.Action("set_xp", "Задать опыт участнику", _set_xp, params=[
        {"key": "user", "label": "Участник (ID)", "type": "user"}, {"key": "xp", "label": "Опыт", "type": "number", "min": 0, "max": 100000000}]),
    modules.Action("reset_all", "Сбросить весь опыт", _reset_all, danger=True, confirm="Опыт всех участников будет обнулён. Это нельзя отменить."),
])


async def setup(bot):
    await bot.add_cog(Levels(bot))
