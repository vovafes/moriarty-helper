"""
Polls -- /poll (or the panel) posts a question with a button per option.
Live bars, one vote per member (switch by clicking another option; with
"several answers" each option toggles on its own), closes by itself at the
deadline. Votes are anonymous. Buttons are DynamicItems so they survive restarts.
"""

import json
import time

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from core import db, embeds, modules
from modules.moderation import human_duration, parse_duration

db.register_schema("""
CREATE TABLE IF NOT EXISTS polls (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    question   TEXT NOT NULL,
    options    TEXT NOT NULL,
    multi      INTEGER NOT NULL DEFAULT 0,
    ends_ts    INTEGER,
    status     TEXT NOT NULL DEFAULT 'open',
    author_id  INTEGER
);
CREATE TABLE IF NOT EXISTS poll_votes (
    poll_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    idx     INTEGER NOT NULL,
    PRIMARY KEY (poll_id, user_id, idx)
);
""")

MODULE = modules.register(modules.Module(
    key="polls", title="Опросы", icon="📊", category="community", default_enabled=True,
    description="Опросы с кнопками, живыми полосками результатов и автозакрытием.",
    fields=[{"key": "manager_roles", "label": "Кто может создавать опросы командой", "type": "roles", "default": [],
             "help": "Помимо права «Управление сообщениями». Из панели создавать можно всегда"}],
    tables=[{"id": "list", "title": "Опросы", "columns": [
        {"key": "id", "label": "ID"}, {"key": "question", "label": "Вопрос"}, {"key": "status_label", "label": "Статус"},
        {"key": "ends_ts", "label": "Закрытие", "format": "time"}, {"key": "total", "label": "Голосов"}]}],
))


def parse_options(text: str) -> list[str]:
    parts = [p.strip() for p in (text or "").replace(";", "\n").splitlines()]
    seen, out = set(), []
    for p in parts:
        if p and p.lower() not in seen:
            seen.add(p.lower()); out.append(p[:70])
    return out[:10]


def bar(count: int, total: int, width: int = 12) -> str:
    filled = round(width * count / total) if total else 0
    return "█" * filled + "░" * (width - filled)


def tally(poll_id: int, n: int) -> list[int]:
    counts = [0] * n
    for r in db.query("SELECT idx, COUNT(*) AS c FROM poll_votes WHERE poll_id=? GROUP BY idx", (poll_id,)):
        if r["idx"] < n:
            counts[r["idx"]] = r["c"]
    return counts


def voters(poll_id: int) -> int:
    return db.query("SELECT COUNT(DISTINCT user_id) AS n FROM poll_votes WHERE poll_id=?", (poll_id,))[0]["n"]


def vote(poll_id: int, user_id: int, idx: int, multi: bool) -> bool:
    """Returns True if the vote is now set, False if it was removed."""
    with db._lock:
        had = bool(db.query("SELECT 1 FROM poll_votes WHERE poll_id=? AND user_id=? AND idx=?", (poll_id, user_id, idx)))
        if had:
            db.execute("DELETE FROM poll_votes WHERE poll_id=? AND user_id=? AND idx=?", (poll_id, user_id, idx))
            return False
        if not multi:
            db.execute("DELETE FROM poll_votes WHERE poll_id=? AND user_id=?", (poll_id, user_id))
        db.execute("INSERT INTO poll_votes (poll_id, user_id, idx) VALUES (?,?,?)", (poll_id, user_id, idx))
        return True


def get_poll(pid: int, guild_id: int | None = None) -> dict | None:
    q, p = "SELECT * FROM polls WHERE id=?", [pid]
    if guild_id is not None:
        q += " AND guild_id=?"; p.append(guild_id)
    rows = db.query(q, tuple(p))
    if not rows:
        return None
    d = dict(rows[0]); d["options"] = json.loads(d["options"])
    return d


def build_embed(poll: dict, counts: list[int], people: int) -> discord.Embed:
    total = sum(counts)
    lines = [f"**{i + 1}. {o}**\n{bar(c, total)} {c} ({round(100 * c / total) if total else 0}%)" for i, (o, c) in enumerate(zip(poll["options"], counts))]
    closed = poll["status"] != "open"
    e = embeds.make(f"📊 {poll['question']}", "\n\n".join(lines), 0x99AAB5 if closed else 0x5865F2)
    foot = f"Проголосовало: {people}" + (" · опрос закрыт" if closed else (" · можно выбрать несколько вариантов" if poll["multi"] else ""))
    e.add_field(name="​", value=foot + ("" if closed or not poll["ends_ts"] else f"\nЗакроется <t:{poll['ends_ts']}:R>"), inline=False)
    return e


class OptionButton(ui.DynamicItem[ui.Button], template=r"poll:(?P<poll>\d+):(?P<idx>\d+)"):
    def __init__(self, poll_id: int, idx: int, label: str = "…"):
        super().__init__(ui.Button(label=f"{idx + 1}. {label}"[:80], style=discord.ButtonStyle.secondary, custom_id=f"poll:{poll_id}:{idx}"))
        self.poll_id, self.idx = poll_id, idx

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["poll"]), int(match["idx"]))

    async def callback(self, interaction: discord.Interaction):
        poll = await db.run(get_poll, self.poll_id, interaction.guild_id)
        if poll is None or poll["status"] != "open" or self.idx >= len(poll["options"]):
            return await interaction.response.send_message("Этот опрос закрыт.", ephemeral=True)
        now_set = await db.run(vote, self.poll_id, interaction.user.id, self.idx, bool(poll["multi"]))
        counts = await db.run(tally, self.poll_id, len(poll["options"]))
        await interaction.message.edit(embed=build_embed(poll, counts, await db.run(voters, self.poll_id)))
        await interaction.response.send_message(f"{'✅ Голос принят' if now_set else '➖ Голос снят'}: {poll['options'][self.idx]}", ephemeral=True)


def build_view(poll_id: int, options: list[str]) -> ui.View:
    view = ui.View(timeout=None)
    for i, o in enumerate(options):
        item = OptionButton(poll_id, i, o); item.item.row = i // 5
        view.add_item(item)
    return view


async def create_poll(guild: discord.Guild, channel, author_id, question: str, options: list[str], multi: bool, seconds: int | None) -> int:
    if len(options) < 2:
        raise modules.ValidationError("Нужно минимум два варианта ответа")
    ends = int(time.time()) + seconds if seconds else None
    pid = await db.run(db.execute, "INSERT INTO polls (guild_id, channel_id, question, options, multi, ends_ts, author_id) VALUES (?,?,?,?,?,?,?)",
                       (guild.id, channel.id, question, json.dumps(options, ensure_ascii=False), int(multi), ends, author_id))
    pid = pid.lastrowid
    poll = await db.run(get_poll, pid)
    try:
        msg = await channel.send(embed=build_embed(poll, [0] * len(options), 0), view=build_view(pid, options))
    except discord.Forbidden:
        await db.run(db.execute, "DELETE FROM polls WHERE id=?", (pid,))
        raise modules.ValidationError("У бота нет прав писать в этот канал")
    await db.run(db.execute, "UPDATE polls SET message_id=? WHERE id=?", (msg.id, pid))
    return pid


async def close_poll(guild: discord.Guild, poll: dict) -> None:
    await db.run(db.execute, "UPDATE polls SET status='closed' WHERE id=?", (poll["id"],))
    ch = guild.get_channel(poll["channel_id"])
    if ch is None or not poll["message_id"]:
        return
    counts = await db.run(tally, poll["id"], len(poll["options"]))
    try:
        msg = await ch.fetch_message(poll["message_id"])
        await msg.edit(embed=build_embed({**poll, "status": "closed"}, counts, await db.run(voters, poll["id"])), view=None)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


class Polls(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_dynamic_items(OptionButton)
        self.check_loop.start()

    async def cog_unload(self):
        self.check_loop.cancel()

    @app_commands.command(name="poll", description="Создать опрос")
    @app_commands.describe(question="Вопрос", options="Варианты через ; (от 2 до 10)", duration="Срок: 30m, 2h, 1d (пусто — без срока)",
                           multi="Можно выбрать несколько вариантов")
    @app_commands.guild_only()
    async def poll(self, interaction: discord.Interaction, question: str, options: str, duration: str | None = None, multi: bool = False):
        cfg = modules.get_config(interaction.guild_id, "polls")
        member = interaction.user
        allowed = cfg["enabled"] and (member.guild_permissions.manage_messages or member.guild_permissions.administrator
                                      or any(r.id in (cfg["manager_roles"] or []) for r in member.roles))
        if not allowed:
            return await interaction.response.send_message("❌ Недостаточно прав или модуль выключен.", ephemeral=True)
        secs = parse_duration(duration) if duration else None
        if duration and secs is None:
            return await interaction.response.send_message("❌ Срок вида `30m`, `2h`, `1d`.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            pid = await create_poll(interaction.guild, interaction.channel, member.id, question[:200], parse_options(options), multi, secs)
        except modules.ValidationError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        await interaction.followup.send(f"✅ Опрос #{pid} создан" + (f", закроется через {human_duration(secs)}." if secs else "."), ephemeral=True)

    @tasks.loop(seconds=15)
    async def check_loop(self):
        for r in await db.run(db.query, "SELECT id FROM polls WHERE status='open' AND ends_ts IS NOT NULL AND ends_ts<=?", (int(time.time()),)):
            poll = await db.run(get_poll, r["id"])
            guild = self.bot.get_guild(poll["guild_id"])
            if guild:
                await close_poll(guild, poll)

    @check_loop.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @check_loop.error
    async def _loop_error(self, error):
        print(f"WARNING: polls check_loop crashed, restarting: {error}")
        if not self.check_loop.is_running():
            self.check_loop.start()


def _table(guild):
    out = []
    for r in db.query("SELECT id FROM polls WHERE guild_id=? ORDER BY id DESC LIMIT 100", (guild.id,)):
        p = get_poll(r["id"])
        out.append({"id": p["id"], "question": p["question"], "status_label": "идёт" if p["status"] == "open" else "закрыт",
                    "ends_ts": p["ends_ts"], "total": voters(p["id"])})
    return out


modules.register_table("polls", "list", _table)


async def _act_create(ctx, p):
    ch = ctx.guild.get_channel(p["channel"])
    if ch is None or not hasattr(ch, "send"):
        raise modules.ValidationError("Выберите текстовый канал")
    secs = parse_duration(p["duration"]) if p.get("duration") else None
    if p.get("duration") and secs is None:
        raise modules.ValidationError("Срок вида 30m, 2h, 1d (или оставьте пустым)")
    pid = await create_poll(ctx.guild, ch, ctx.user_id, p["question"][:200], parse_options(p["options"]), bool(p.get("multi")), secs)
    return f"Опрос #{pid} создан"


async def _act_close(ctx, p):
    poll = await db.run(get_poll, int(p["id"]), ctx.guild.id)
    if poll is None or poll["status"] != "open":
        raise modules.ValidationError("Открытого опроса с таким ID нет")
    await close_poll(ctx.guild, poll)
    return "Опрос закрыт"


MODULE.actions.extend([
    modules.Action("create", "Создать опрос", _act_create, params=[
        {"key": "channel", "label": "Канал", "type": "channel", "kind": "text"},
        {"key": "question", "label": "Вопрос", "type": "text"},
        {"key": "options", "label": "Варианты (по строке на вариант)", "type": "longtext"},
        {"key": "duration", "label": "Срок (30m, 2h, 1d)", "type": "text", "required": False},
        {"key": "multi", "label": "Можно несколько вариантов", "type": "bool", "default": False}]),
    modules.Action("close", "Закрыть опрос", _act_close, params=[{"key": "id", "label": "ID опроса", "type": "number", "min": 1}], danger=True),
])


async def setup(bot):
    await bot.add_cog(Polls(bot))
