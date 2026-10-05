"""
Suggestions -- /suggest posts an idea with 👍/👎 buttons; staff approve or deny
it with a reason (/suggestion approve|deny, or from the panel) and the author
is told. One vote per member, click again to take it back, click the other
button to switch. Separate from the legacy "Предложения" form (Feedback).
"""

import time

import discord
from discord import app_commands, ui
from discord.ext import commands

from core import db, embeds, modules

db.register_schema("""
CREATE TABLE IF NOT EXISTS suggestions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    message_id  INTEGER,
    author_id   INTEGER NOT NULL,
    author_name TEXT,
    text        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    decided_by  TEXT,
    reason      TEXT,
    created_ts  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS suggestions_msg ON suggestions (message_id);
CREATE TABLE IF NOT EXISTS suggestion_votes (
    suggestion_id INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    vote          INTEGER NOT NULL,
    PRIMARY KEY (suggestion_id, user_id)
);
""")

MODULE = modules.register(modules.Module(
    key="suggestions", title="Идеи и голосования", icon="💡", category="community",
    description="Участники предлагают идеи командой /suggest, остальные голосуют кнопками, администрация принимает или отклоняет.",
    fields=[
        {"key": "channel", "label": "Канал идей", "type": "channel", "kind": "text", "default": None,
         "help": "Сюда бот публикует идеи"},
        {"key": "staff_roles", "label": "Кто принимает решения", "type": "roles", "default": [],
         "help": "Помимо права «Управление сервером»"},
        {"key": "anonymous", "label": "Скрывать автора", "type": "bool", "default": False},
        {"key": "dm_author", "label": "Сообщать автору о решении в ЛС", "type": "bool", "default": True},
        {"key": "cooldown", "label": "Пауза между идеями одного человека, сек", "type": "number", "default": 300, "min": 0, "max": 86400},
    ],
    tables=[{"id": "list", "title": "Идеи", "columns": [
        {"key": "id", "label": "#"}, {"key": "text", "label": "Идея"}, {"key": "author", "label": "Автор"},
        {"key": "status_label", "label": "Статус"}, {"key": "votes", "label": "👍/👎"}, {"key": "created_ts", "label": "Когда", "format": "time"}]}],
))

STATUS = {"open": ("На рассмотрении", 0x5865F2), "approved": ("Принято", 0x3BA55D), "denied": ("Отклонено", 0xED4245)}


def create_row(guild_id, channel_id, author, text) -> int:
    return db.execute("INSERT INTO suggestions (guild_id, channel_id, author_id, author_name, text, created_ts) VALUES (?,?,?,?,?,?)",
                      (guild_id, channel_id, author.id, str(author), text, int(time.time()))).lastrowid


def get_row(sid: int, guild_id: int | None = None) -> dict | None:
    q, p = "SELECT * FROM suggestions WHERE id=?", [sid]
    if guild_id is not None:
        q += " AND guild_id=?"; p.append(guild_id)
    rows = db.query(q, tuple(p))
    return dict(rows[0]) if rows else None


def get_by_message(message_id: int) -> dict | None:
    rows = db.query("SELECT * FROM suggestions WHERE message_id=?", (message_id,))
    return dict(rows[0]) if rows else None


def votes(sid: int) -> tuple[int, int]:
    r = db.query("SELECT SUM(vote=1) AS up, SUM(vote=-1) AS down FROM suggestion_votes WHERE suggestion_id=?", (sid,))[0]
    return r["up"] or 0, r["down"] or 0


def cast(sid: int, user_id: int, vote: int) -> int:
    """vote is +1/-1; the same vote again removes it. Returns the resulting vote (0 = none)."""
    with db._lock:
        cur = db.query("SELECT vote FROM suggestion_votes WHERE suggestion_id=? AND user_id=?", (sid, user_id))
        if cur and cur[0]["vote"] == vote:
            db.execute("DELETE FROM suggestion_votes WHERE suggestion_id=? AND user_id=?", (sid, user_id))
            return 0
        db.execute("INSERT INTO suggestion_votes (suggestion_id, user_id, vote) VALUES (?,?,?) "
                   "ON CONFLICT(suggestion_id, user_id) DO UPDATE SET vote=excluded.vote", (sid, user_id, vote))
        return vote


def last_by(guild_id: int, user_id: int) -> int:
    r = db.query("SELECT MAX(created_ts) AS t FROM suggestions WHERE guild_id=? AND author_id=?", (guild_id, user_id))[0]["t"]
    return r or 0


def decide_row(sid: int, status: str, by: str, reason: str | None) -> None:
    db.execute("UPDATE suggestions SET status=?, decided_by=?, reason=? WHERE id=?", (status, by, reason, sid))


def build_embed(row: dict, up: int, down: int, anonymous: bool) -> discord.Embed:
    label, color = STATUS[row["status"]]
    e = embeds.make(f"Идея #{row['id']}", row["text"], color)
    e.add_field(name="Автор", value="Скрыт" if anonymous else f"<@{row['author_id']}>")
    e.add_field(name="Статус", value=label)
    e.add_field(name="Голоса", value=f"👍 {up} · 👎 {down}")
    if row["reason"]:
        e.add_field(name=f"Комментарий ({row['decided_by']})", value=embeds.clip(row["reason"], 900), inline=False)
    return e


class VoteView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _vote(self, interaction: discord.Interaction, vote: int):
        row = await db.run(get_by_message, interaction.message.id)
        if row is None or row["status"] != "open":
            return await interaction.response.send_message("Голосование по этой идее закрыто.", ephemeral=True)
        result = await db.run(cast, row["id"], interaction.user.id, vote)
        up, down = await db.run(votes, row["id"])
        cfg = modules.get_config(interaction.guild_id, "suggestions")
        await interaction.message.edit(embed=build_embed(row, up, down, cfg["anonymous"]))
        await interaction.response.send_message({1: "👍 Ваш голос: за", -1: "👎 Ваш голос: против", 0: "Голос снят"}[result], ephemeral=True)

    @ui.button(label="За", emoji="👍", style=discord.ButtonStyle.success, custom_id="sug:up")
    async def up(self, interaction: discord.Interaction, button: ui.Button):
        await self._vote(interaction, 1)

    @ui.button(label="Против", emoji="👎", style=discord.ButtonStyle.danger, custom_id="sug:down")
    async def down(self, interaction: discord.Interaction, button: ui.Button):
        await self._vote(interaction, -1)


def can_decide(member: discord.Member) -> bool:
    if member.guild_permissions.manage_guild or member.guild_permissions.administrator:
        return True
    return any(r.id in (modules.get_config(member.guild.id, "suggestions")["staff_roles"] or []) for r in member.roles)


async def post_suggestion(guild: discord.Guild, author, text: str) -> int:
    cfg = modules.get_config(guild.id, "suggestions")
    ch = guild.get_channel(cfg["channel"]) if cfg["channel"] else None
    if ch is None:
        raise modules.ValidationError("Канал для идей не настроен — сообщите администрации")
    sid = await db.run(create_row, guild.id, ch.id, author, text)
    row = await db.run(get_row, sid)
    try:
        msg = await ch.send(embed=build_embed(row, 0, 0, cfg["anonymous"]), view=VoteView())
    except discord.Forbidden:
        raise modules.ValidationError("У бота нет прав писать в канал идей")
    await db.run(db.execute, "UPDATE suggestions SET message_id=? WHERE id=?", (msg.id, sid))
    return sid


async def decide(guild: discord.Guild, sid: int, status: str, by: str, reason: str | None) -> dict:
    row = await db.run(get_row, sid, guild.id)
    if row is None:
        raise modules.ValidationError("Идея с таким номером не найдена")
    if row["status"] != "open":
        raise modules.ValidationError("По этой идее решение уже принято")
    await db.run(decide_row, sid, status, by, reason)
    row = await db.run(get_row, sid)
    cfg = modules.get_config(guild.id, "suggestions")
    ch = guild.get_channel(row["channel_id"])
    if ch and row["message_id"]:
        try:
            msg = await ch.fetch_message(row["message_id"])
            up, down = await db.run(votes, sid)
            await msg.edit(embed=build_embed(row, up, down, cfg["anonymous"]), view=None)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
    author = guild.get_member(row["author_id"])
    if author and cfg["dm_author"]:
        try:
            await author.send(f"Ваша идея #{sid} на сервере **{guild.name}**: **{STATUS[status][0].lower()}**."
                              + (f"\nКомментарий: {reason}" if reason else ""))
        except (discord.Forbidden, discord.HTTPException):
            pass
    return row


class Suggestions(commands.Cog):
    suggestion = app_commands.Group(name="suggestion", description="Решения по идеям", guild_only=True)

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(VoteView())

    @app_commands.command(name="suggest", description="Предложить идею")
    @app_commands.describe(text="Ваша идея")
    @app_commands.guild_only()
    async def suggest(self, interaction: discord.Interaction, text: app_commands.Range[str, 5, 1500]):
        cfg = modules.get_config(interaction.guild_id, "suggestions")
        if not cfg["enabled"]:
            return await interaction.response.send_message("Приём идей выключен.", ephemeral=True)
        wait = cfg["cooldown"] - (time.time() - await db.run(last_by, interaction.guild_id, interaction.user.id))
        if wait > 0:
            return await interaction.response.send_message(f"Следующую идею можно отправить через {int(wait)} сек.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            sid = await post_suggestion(interaction.guild, interaction.user, text)
        except modules.ValidationError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        await interaction.followup.send(f"✅ Идея #{sid} опубликована.", ephemeral=True)

    async def _decide(self, interaction, sid, status, reason):
        if not can_decide(interaction.user):
            return await interaction.response.send_message("❌ Недостаточно прав.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            await decide(interaction.guild, sid, status, str(interaction.user), reason)
        except modules.ValidationError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        await interaction.followup.send("✅ Готово.", ephemeral=True)

    @suggestion.command(name="approve", description="Принять идею")
    async def approve(self, interaction: discord.Interaction, id: int, reason: str | None = None):
        await self._decide(interaction, id, "approved", reason)

    @suggestion.command(name="deny", description="Отклонить идею")
    async def deny(self, interaction: discord.Interaction, id: int, reason: str | None = None):
        await self._decide(interaction, id, "denied", reason)


def _table(guild):
    out = []
    for r in db.query("SELECT * FROM suggestions WHERE guild_id=? ORDER BY id DESC LIMIT 200", (guild.id,)):
        d = dict(r); up, down = votes(d["id"])
        out.append({"id": d["id"], "text": embeds.clip(d["text"], 120), "author": d["author_name"],
                    "status_label": STATUS[d["status"]][0], "votes": f"{up}/{down}", "created_ts": d["created_ts"]})
    return out


modules.register_table("suggestions", "list", _table)

_ID_REASON = [{"key": "id", "label": "Номер идеи", "type": "number", "min": 1},
              {"key": "reason", "label": "Комментарий", "type": "text", "required": False}]


def _decider(status, text):
    async def run(ctx, p):
        await decide(ctx.guild, int(p["id"]), status, ctx.user_name or "панель", p.get("reason"))
        return text
    return run


async def _delete(ctx, p):
    row = await db.run(get_row, int(p["id"]), ctx.guild.id)
    if row is None:
        raise modules.ValidationError("Идея с таким номером не найдена")
    ch = ctx.guild.get_channel(row["channel_id"])
    if ch and row["message_id"]:
        try:
            await (await ch.fetch_message(row["message_id"])).delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
    await db.run(db.execute, "DELETE FROM suggestions WHERE id=?", (row["id"],))
    await db.run(db.execute, "DELETE FROM suggestion_votes WHERE suggestion_id=?", (row["id"],))
    return "Идея удалена"


MODULE.actions.extend([
    modules.Action("approve", "Принять идею", _decider("approved", "Идея принята"), params=_ID_REASON),
    modules.Action("deny", "Отклонить идею", _decider("denied", "Идея отклонена"), params=_ID_REASON),
    modules.Action("delete", "Удалить идею", _delete, params=_ID_REASON[:1], danger=True),
])


async def setup(bot):
    await bot.add_cog(Suggestions(bot))
