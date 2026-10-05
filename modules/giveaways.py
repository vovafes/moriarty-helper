"""
Giveaways -- /giveaway start|end|reroll|cancel (and the same from the panel).
A giveaway is a message with a persistent "participate" button; it ends by
itself at the deadline (checked every 15 s), picks winners among members who
are still on the server and announces them. Click the button again to leave.
Optional role requirement. State is in SQLite so it survives restarts.
"""

import json
import random
import time

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from core import db, embeds, modules
from modules.moderation import human_duration, parse_duration

db.register_schema("""
CREATE TABLE IF NOT EXISTS giveaways (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id      INTEGER NOT NULL,
    channel_id    INTEGER NOT NULL,
    message_id    INTEGER,
    prize         TEXT    NOT NULL,
    winners_count INTEGER NOT NULL,
    host_id       INTEGER,
    required_role INTEGER,
    ends_ts       INTEGER NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'active',
    winners       TEXT,
    created_ts    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS giveaways_due ON giveaways (status, ends_ts);
CREATE INDEX IF NOT EXISTS giveaways_msg ON giveaways (message_id);
CREATE TABLE IF NOT EXISTS giveaway_entries (
    giveaway_id INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    PRIMARY KEY (giveaway_id, user_id)
);
""")

MODULE = modules.register(modules.Module(
    key="giveaways", title="Розыгрыши", icon="🎉", category="community",
    description="Розыгрыши с кнопкой участия, автоматическим подведением итогов, рероллом и требованием роли.",
    fields=[
        {"key": "manager_roles", "label": "Кто может проводить", "type": "roles", "default": [],
         "help": "Помимо тех, у кого есть право «Управление сервером»"},
        {"key": "default_winners", "label": "Победителей по умолчанию", "type": "number", "default": 1, "min": 1, "max": 50},
        {"key": "button_label", "label": "Текст кнопки", "type": "text", "default": "Участвовать"},
        {"key": "ping_role", "label": "Кого тегать при старте", "type": "role", "default": None,
         "help": "Тег уйдёт отдельным сообщением при запуске розыгрыша"},
    ],
    tables=[{"id": "list", "title": "Розыгрыши", "columns": [
        {"key": "id", "label": "ID"}, {"key": "prize", "label": "Приз"}, {"key": "status_label", "label": "Статус"},
        {"key": "ends_ts", "label": "Окончание", "format": "time"}, {"key": "entries", "label": "Участников"},
        {"key": "winners_text", "label": "Победители"}]}],
))

STATUS_LABELS = {"active": "идёт", "ended": "завершён", "cancelled": "отменён"}


# ── storage (sync) ──────────────────────────────────────────────────────────

def create_row(guild_id, channel_id, prize, winners, host_id, required_role, ends_ts) -> int:
    return db.execute(
        "INSERT INTO giveaways (guild_id, channel_id, prize, winners_count, host_id, required_role, ends_ts, created_ts)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (guild_id, channel_id, prize, winners, host_id, required_role, ends_ts, int(time.time()))).lastrowid


def set_message(gid: int, message_id: int) -> None:
    db.execute("UPDATE giveaways SET message_id=? WHERE id=?", (message_id, gid))


def get_row(gid: int, guild_id: int | None = None) -> dict | None:
    q, p = "SELECT * FROM giveaways WHERE id=?", [gid]
    if guild_id is not None:
        q += " AND guild_id=?"; p.append(guild_id)
    rows = db.query(q, tuple(p))
    return dict(rows[0]) if rows else None


def get_by_message(message_id: int) -> dict | None:
    rows = db.query("SELECT * FROM giveaways WHERE message_id=?", (message_id,))
    return dict(rows[0]) if rows else None


def entries_of(gid: int) -> list[int]:
    return [r["user_id"] for r in db.query("SELECT user_id FROM giveaway_entries WHERE giveaway_id=?", (gid,))]


def toggle_entry(gid: int, user_id: int) -> bool:
    """True if the user is now in, False if they just left."""
    with db._lock:
        if db.query("SELECT 1 FROM giveaway_entries WHERE giveaway_id=? AND user_id=?", (gid, user_id)):
            db.execute("DELETE FROM giveaway_entries WHERE giveaway_id=? AND user_id=?", (gid, user_id))
            return False
        db.execute("INSERT INTO giveaway_entries (giveaway_id, user_id) VALUES (?,?)", (gid, user_id))
        return True


def due_rows(now: int) -> list[dict]:
    return [dict(r) for r in db.query("SELECT * FROM giveaways WHERE status='active' AND ends_ts<=?", (now,))]


def mark(gid: int, status: str, winners: list[int] | None = None) -> None:
    db.execute("UPDATE giveaways SET status=?, winners=? WHERE id=?",
               (status, json.dumps(winners) if winners is not None else None, gid))


def list_rows(guild_id: int) -> list[dict]:
    out = []
    for r in db.query("SELECT * FROM giveaways WHERE guild_id=? ORDER BY id DESC LIMIT 100", (guild_id,)):
        d = dict(r)
        d["entries"] = db.query("SELECT COUNT(*) AS n FROM giveaway_entries WHERE giveaway_id=?", (d["id"],))[0]["n"]
        d["status_label"] = STATUS_LABELS.get(d["status"], d["status"])
        w = json.loads(d["winners"]) if d["winners"] else []
        d["winners_text"] = ", ".join(str(u) for u in w) or "—"
        out.append(d)
    return out


# ── logic ───────────────────────────────────────────────────────────────────

def pick_winners(candidates: list[int], n: int, rng=random) -> list[int]:
    pool = list(dict.fromkeys(candidates))
    return rng.sample(pool, min(n, len(pool)))


def build_embed(row: dict, entries: int | None = None, winners: list[int] | None = None) -> discord.Embed:
    status = row["status"]
    color = {"active": 0x5865F2, "ended": 0x3BA55D, "cancelled": 0x99AAB5}[status]
    e = embeds.make(f"🎉 {row['prize']}", color=color)
    if status == "active":
        e.description = (f"Нажмите кнопку ниже, чтобы участвовать (ещё раз — чтобы выйти).\n\n"
                         f"⏰ Итоги: <t:{row['ends_ts']}:R> (<t:{row['ends_ts']}:f>)\n"
                         f"🏆 Победителей: **{row['winners_count']}**\n"
                         f"👤 Организатор: <@{row['host_id']}>")
        if row["required_role"]:
            e.description += f"\n🔒 Нужна роль: <@&{row['required_role']}>"
    elif status == "ended":
        names = " ".join(f"<@{u}>" for u in (winners or [])) or "*никто не участвовал*"
        e.description = f"**Завершён** · участников: **{entries or 0}**\n🏆 Победители: {names}\n👤 Организатор: <@{row['host_id']}>"
    else:
        e.description = "**Отменён**"
    return e


class GiveawayView(ui.View):
    """Persistent: one custom_id for every giveaway, the message id tells which one."""

    def __init__(self, label: str = "Участвовать"):
        super().__init__(timeout=None)
        self.add_item(GiveawayButton(label))


class GiveawayButton(ui.Button):
    def __init__(self, label: str):
        super().__init__(label=label, emoji="🎉", style=discord.ButtonStyle.primary, custom_id="giveaway:enter")

    async def callback(self, interaction: discord.Interaction):
        row = await db.run(get_by_message, interaction.message.id)
        if not row or row["status"] != "active" or row["ends_ts"] <= time.time():
            return await interaction.response.send_message("Этот розыгрыш уже завершён.", ephemeral=True)
        if row["required_role"] and not any(r.id == row["required_role"] for r in interaction.user.roles):
            return await interaction.response.send_message(f"Для участия нужна роль <@&{row['required_role']}>.", ephemeral=True)
        joined = await db.run(toggle_entry, row["id"], interaction.user.id)
        await interaction.response.send_message("✅ Вы участвуете!" if joined else "Вы вышли из розыгрыша.", ephemeral=True)


async def start_giveaway(guild: discord.Guild, channel, host, prize: str, seconds: int, winners: int,
                         required_role: int | None) -> int:
    cfg = modules.get_config(guild.id, "giveaways")
    ends = int(time.time()) + seconds
    gid = await db.run(create_row, guild.id, channel.id, prize, winners, host.id, required_role, ends)
    row = await db.run(get_row, gid)
    ping = guild.get_role(cfg["ping_role"]) if cfg["ping_role"] else None
    try:
        msg = await channel.send(embed=build_embed(row), view=GiveawayView(cfg["button_label"] or "Участвовать"))
        if ping:
            await channel.send(ping.mention, allowed_mentions=discord.AllowedMentions(roles=[ping]), delete_after=5)
    except discord.Forbidden:
        await db.run(mark, gid, "cancelled")
        raise modules.ValidationError("У бота нет прав писать в этот канал")
    await db.run(set_message, gid, msg.id)
    return gid


async def finish_giveaway(guild: discord.Guild, row: dict, *, reroll: bool = False) -> list[int]:
    """Pick winners, update the message, announce. Returns the winners' ids."""
    entries = await db.run(entries_of, row["id"])
    eligible = []
    for uid in entries:
        m = guild.get_member(uid)
        if m is None or m.bot:
            continue
        if row["required_role"] and not any(r.id == row["required_role"] for r in m.roles):
            continue
        eligible.append(uid)
    winners = pick_winners(eligible, row["winners_count"])
    await db.run(mark, row["id"], "ended", winners)
    ch = guild.get_channel(row["channel_id"])
    if ch is not None:
        done = {**row, "status": "ended"}
        try:
            if row["message_id"]:
                msg = await ch.fetch_message(row["message_id"])
                await msg.edit(embed=build_embed(done, len(entries), winners), view=None)
            text = (f"🎉 {'Новые победители' if reroll else 'Победители'} розыгрыша **{row['prize']}**: "
                    + " ".join(f"<@{u}>" for u in winners)) if winners else f"😕 В розыгрыше **{row['prize']}** никто не участвовал."
            await ch.send(text, allowed_mentions=discord.AllowedMentions(users=[discord.Object(u) for u in winners]))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
    return winners


def _can_manage(member: discord.Member) -> bool:
    if member.guild_permissions.manage_guild or member.guild_permissions.administrator:
        return True
    allowed = set(modules.get_config(member.guild.id, "giveaways")["manager_roles"] or [])
    return any(r.id in allowed for r in member.roles)


class Giveaways(commands.Cog):
    group = app_commands.Group(name="giveaway", description="Розыгрыши", guild_only=True)

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(GiveawayView())      # keeps old buttons alive after a restart
        self.check_loop.start()

    async def cog_unload(self):
        self.check_loop.cancel()

    async def _guard(self, interaction) -> bool:
        if not modules.is_enabled(interaction.guild_id, "giveaways"):
            await interaction.response.send_message("❌ Модуль розыгрышей выключен в панели.", ephemeral=True)
            return False
        if not _can_manage(interaction.user):
            await interaction.response.send_message("❌ Недостаточно прав.", ephemeral=True)
            return False
        return True

    @group.command(name="start", description="Запустить розыгрыш")
    @app_commands.describe(prize="Что разыгрываем", duration="Срок: 30m, 2h, 1d", winners="Сколько победителей",
                           channel="Куда отправить (по умолчанию — этот канал)", required_role="Роль, нужная для участия")
    async def start(self, interaction: discord.Interaction, prize: str, duration: str,
                    winners: app_commands.Range[int, 1, 50] | None = None,
                    channel: discord.TextChannel | None = None, required_role: discord.Role | None = None):
        if not await self._guard(interaction):
            return
        secs = parse_duration(duration)
        if secs is None or secs < 10:
            return await interaction.response.send_message("❌ Срок вида `30m`, `2h`, `1d` (минимум 10 секунд).", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        cfg = modules.get_config(interaction.guild_id, "giveaways")
        try:
            gid = await start_giveaway(interaction.guild, channel or interaction.channel, interaction.user, prize, secs,
                                       winners or cfg["default_winners"], required_role.id if required_role else None)
        except modules.ValidationError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        await interaction.followup.send(f"✅ Розыгрыш #{gid} запущен на {human_duration(secs)}.", ephemeral=True)

    async def _row(self, interaction, giveaway_id: int, *, statuses: tuple[str, ...]):
        row = await db.run(get_row, giveaway_id, interaction.guild_id)
        if row is None or row["status"] not in statuses:
            await interaction.response.send_message("❌ Такого розыгрыша нет или он в неподходящем состоянии.", ephemeral=True)
            return None
        return row

    @group.command(name="end", description="Завершить розыгрыш прямо сейчас")
    async def end(self, interaction: discord.Interaction, giveaway_id: int):
        if not await self._guard(interaction):
            return
        row = await self._row(interaction, giveaway_id, statuses=("active",))
        if row:
            await interaction.response.defer(ephemeral=True)
            w = await finish_giveaway(interaction.guild, row)
            await interaction.followup.send(f"✅ Завершён, победителей: {len(w)}.", ephemeral=True)

    @group.command(name="reroll", description="Выбрать новых победителей")
    async def reroll(self, interaction: discord.Interaction, giveaway_id: int):
        if not await self._guard(interaction):
            return
        row = await self._row(interaction, giveaway_id, statuses=("ended",))
        if row:
            await interaction.response.defer(ephemeral=True)
            w = await finish_giveaway(interaction.guild, row, reroll=True)
            await interaction.followup.send(f"✅ Новых победителей: {len(w)}.", ephemeral=True)

    @group.command(name="cancel", description="Отменить розыгрыш без победителей")
    async def cancel(self, interaction: discord.Interaction, giveaway_id: int):
        if not await self._guard(interaction):
            return
        row = await self._row(interaction, giveaway_id, statuses=("active",))
        if row:
            await db.run(mark, row["id"], "cancelled")
            await _edit_cancelled(interaction.guild, row)
            await interaction.response.send_message("✅ Розыгрыш отменён.", ephemeral=True)

    @tasks.loop(seconds=15)
    async def check_loop(self):
        for row in await db.run(due_rows, int(time.time())):
            guild = self.bot.get_guild(row["guild_id"])
            if guild is None:
                continue
            try:
                await finish_giveaway(guild, row)
            except Exception as exc:
                print(f"WARNING: giveaway {row['id']} failed to finish: {exc}")
                await db.run(mark, row["id"], "ended", [])      # don't retry forever

    @check_loop.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @check_loop.error
    async def _loop_error(self, error):
        print(f"WARNING: giveaway check_loop crashed, restarting: {error}")
        if not self.check_loop.is_running():
            self.check_loop.start()


async def _edit_cancelled(guild: discord.Guild, row: dict) -> None:
    ch = guild.get_channel(row["channel_id"])
    if ch is None or not row["message_id"]:
        return
    try:
        msg = await ch.fetch_message(row["message_id"])
        await msg.edit(embed=build_embed({**row, "status": "cancelled"}), view=None)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


# ── panel ───────────────────────────────────────────────────────────────────

modules.register_table("giveaways", "list", lambda guild: list_rows(guild.id))


async def _act_start(ctx, p):
    ch = ctx.guild.get_channel(p["channel"])
    if ch is None or not hasattr(ch, "send"):
        raise modules.ValidationError("Выберите текстовый канал")
    secs = parse_duration(p["duration"])
    if secs is None or secs < 10:
        raise modules.ValidationError("Срок вида 30m, 2h, 1d (минимум 10 секунд)")
    host = ctx.guild.get_member(ctx.user_id) or ctx.guild.me
    gid = await start_giveaway(ctx.guild, ch, host, p["prize"], secs, int(p["winners"] or 1), p.get("role"))
    return f"Розыгрыш #{gid} запущен"


def _row_action(statuses, run):
    async def handler(ctx, p):
        row = await db.run(get_row, int(p["id"]), ctx.guild.id)
        if row is None or row["status"] not in statuses:
            raise modules.ValidationError("Нет такого розыгрыша или он в неподходящем состоянии")
        return await run(ctx, row)
    return handler


async def _act_end(ctx, row):
    return f"Завершён, победителей: {len(await finish_giveaway(ctx.guild, row))}"


async def _act_reroll(ctx, row):
    return f"Новых победителей: {len(await finish_giveaway(ctx.guild, row, reroll=True))}"


async def _act_cancel(ctx, row):
    await db.run(mark, row["id"], "cancelled")
    await _edit_cancelled(ctx.guild, row)
    return "Розыгрыш отменён"


_ID = [{"key": "id", "label": "ID розыгрыша (из таблицы)", "type": "number", "min": 1}]
MODULE.actions.extend([
    modules.Action("start", "Запустить розыгрыш", _act_start, params=[
        {"key": "channel", "label": "Канал", "type": "channel", "kind": "text"},
        {"key": "prize", "label": "Приз", "type": "text"},
        {"key": "duration", "label": "Срок (30m, 2h, 1d)", "type": "text", "default": "1d"},
        {"key": "winners", "label": "Победителей", "type": "number", "default": 1, "min": 1, "max": 50},
        {"key": "role", "label": "Нужная роль (необязательно)", "type": "role", "required": False}]),
    modules.Action("end", "Завершить сейчас", _row_action(("active",), _act_end), params=_ID),
    modules.Action("reroll", "Реролл победителей", _row_action(("ended",), _act_reroll), params=_ID),
    modules.Action("cancel", "Отменить", _row_action(("active",), _act_cancel), params=_ID, danger=True,
                   confirm="Розыгрыш будет отменён без победителей."),
])


async def setup(bot):
    await bot.add_cog(Giveaways(bot))
