"""
Support tickets -- private channels per request, with categories, claiming,
closing, an optional 1-5 rating and a permanent transcript archive viewable
from the dashboard. Separate from the legacy "Заявки" (recruitment
applications) which keep working as before.

Flow: panel select (category) -> private channel (opener + staff) with a
Close / Claim control message -> close saves the transcript (SQLite), posts
it as an HTML file to the transcript channel, DMs the opener a rating prompt
and deletes the channel.
"""

import datetime
import html
import io
import time

import discord
from aiohttp import web
from discord import app_commands, ui
from discord.ext import commands

from core import db, embeds, modules

db.register_schema("""
CREATE TABLE IF NOT EXISTS support_tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    opener_id   INTEGER NOT NULL,
    opener_name TEXT,
    category    TEXT,
    status      TEXT    NOT NULL DEFAULT 'open',
    claimed_by  INTEGER,
    claimed_name TEXT,
    created_ts  INTEGER NOT NULL,
    closed_ts   INTEGER,
    rating      INTEGER
);
CREATE INDEX IF NOT EXISTS support_tickets_guild ON support_tickets (guild_id, status);
CREATE INDEX IF NOT EXISTS support_tickets_channel ON support_tickets (channel_id);
CREATE TABLE IF NOT EXISTS support_transcripts (
    ticket_id INTEGER PRIMARY KEY,
    guild_id  INTEGER NOT NULL,
    html      TEXT    NOT NULL,
    messages  INTEGER NOT NULL DEFAULT 0
);
""")

MODULE = modules.register(modules.Module(
    key="support_tickets", title="Тикеты поддержки", icon="🎫", category="support",
    description="Обращения в приватных каналах: категории, закрепление за сотрудником, оценка и архив переписок.",
    fields=[
        {"key": "category_channel", "label": "Категория для тикетов", "type": "channel", "kind": "category", "default": None,
         "group": "Каналы", "help": "Новые тикеты создаются в ней"},
        {"key": "transcript_channel", "label": "Канал для архива переписок", "type": "channel", "kind": "text", "default": None,
         "group": "Каналы"},
        {"key": "staff_roles", "label": "Роли поддержки", "type": "roles", "default": [], "group": "Доступ",
         "help": "Видят все тикеты и могут их закрывать"},
        {"key": "ping_staff", "label": "Тегать поддержку при новом тикете", "type": "bool", "default": True, "group": "Доступ"},
        {"key": "categories", "label": "Категории обращений", "type": "longtext", "default": "Общий вопрос|❓|Любой вопрос\nЖалоба|⚠️|Жалоба на участника",
         "group": "Тикеты", "help": "По строке на категорию: название|эмодзи|описание. До 25 строк"},
        {"key": "max_open", "label": "Открытых тикетов на человека", "type": "number", "default": 1, "min": 1, "max": 10, "group": "Тикеты"},
        {"key": "ask_rating", "label": "Просить оценку после закрытия", "type": "bool", "default": True, "group": "Тикеты"},
        {"key": "welcome", "label": "Приветствие в тикете", "type": "longtext",
         "default": "Опишите вашу проблему — скоро к вам подключится поддержка.", "group": "Тикеты"},
    ],
    tables=[{"id": "tickets", "title": "Тикеты", "columns": [
        {"key": "id", "label": "#"}, {"key": "opener", "label": "Автор"}, {"key": "category", "label": "Категория"},
        {"key": "status_label", "label": "Статус"}, {"key": "claimed", "label": "Взял"}, {"key": "rating", "label": "Оценка"},
        {"key": "created_ts", "label": "Создан", "format": "time"}, {"key": "transcript", "label": "Переписка", "format": "link"}]}],
))


# ── storage ─────────────────────────────────────────────────────────────────

def parse_categories(text: str) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        parts = [p.strip() for p in line.split("|")]
        if not parts or not parts[0]:
            continue
        out.append({"label": parts[0][:100], "emoji": (parts[1] if len(parts) > 1 and parts[1] else None),
                    "description": (parts[2][:100] if len(parts) > 2 and parts[2] else None)})
    return out[:25]


def open_count(guild_id: int, user_id: int) -> int:
    return db.query("SELECT COUNT(*) AS n FROM support_tickets WHERE guild_id=? AND opener_id=? AND status='open'",
                    (guild_id, user_id))[0]["n"]


def new_ticket(guild_id, channel_id, opener, category) -> int:
    return db.execute(
        "INSERT INTO support_tickets (guild_id, channel_id, opener_id, opener_name, category, created_ts) VALUES (?,?,?,?,?,?)",
        (guild_id, channel_id, opener.id, str(opener), category, int(time.time()))).lastrowid


def ticket_by_channel(channel_id: int) -> dict | None:
    rows = db.query("SELECT * FROM support_tickets WHERE channel_id=? AND status='open'", (channel_id,))
    return dict(rows[0]) if rows else None


def get_ticket(tid: int, guild_id: int | None = None) -> dict | None:
    q, p = "SELECT * FROM support_tickets WHERE id=?", [tid]
    if guild_id is not None:
        q += " AND guild_id=?"; p.append(guild_id)
    rows = db.query(q, tuple(p))
    return dict(rows[0]) if rows else None


def claim(tid: int, user) -> bool:
    with db._lock:
        row = db.query("SELECT claimed_by FROM support_tickets WHERE id=?", (tid,))[0]
        if row["claimed_by"]:
            return False
        db.execute("UPDATE support_tickets SET claimed_by=?, claimed_name=? WHERE id=?", (user.id, str(user), tid))
        return True


def close_ticket(tid: int, html_text: str, count: int, guild_id: int) -> None:
    now = int(time.time())
    db.execute("UPDATE support_tickets SET status='closed', closed_ts=? WHERE id=?", (now, tid))
    db.execute("INSERT OR REPLACE INTO support_transcripts (ticket_id, guild_id, html, messages) VALUES (?,?,?,?)",
               (tid, guild_id, html_text, count))


def rate(tid: int, opener_id: int, stars: int) -> bool:
    cur = db.execute("UPDATE support_tickets SET rating=? WHERE id=? AND opener_id=? AND rating IS NULL", (stars, tid, opener_id))
    return cur.rowcount > 0


def transcript_html(tid: int, guild_id: int) -> str | None:
    rows = db.query("SELECT html FROM support_transcripts WHERE ticket_id=? AND guild_id=?", (tid, guild_id))
    return rows[0]["html"] if rows else None


def list_tickets(guild_id: int) -> list[dict]:
    out = []
    for r in db.query("SELECT * FROM support_tickets WHERE guild_id=? ORDER BY id DESC LIMIT 200", (guild_id,)):
        d = dict(r)
        has = db.query("SELECT 1 FROM support_transcripts WHERE ticket_id=?", (d["id"],))
        out.append({"id": d["id"], "opener": d["opener_name"], "category": d["category"],
                    "status_label": "открыт" if d["status"] == "open" else "закрыт", "claimed": d["claimed_name"],
                    "rating": f"{'★' * d['rating']}" if d["rating"] else None, "created_ts": d["created_ts"],
                    "transcript": f"/transcripts/{guild_id}/{d['id']}" if has else None})
    return out


modules.register_table("support_tickets", "tickets", lambda guild: list_tickets(guild.id))


# ── transcript ──────────────────────────────────────────────────────────────

MSK = datetime.timezone(datetime.timedelta(hours=3))


def render_transcript(ticket: dict, messages: list) -> str:
    """Self-contained HTML. Everything user-controlled goes through html.escape."""
    rows = []
    for m in messages:
        when = m.created_at.astimezone(MSK).strftime("%d.%m.%Y %H:%M")
        body = html.escape(m.content or "").replace("\n", "<br>")
        extra = "".join(f'<div class="att">📎 <a href="{html.escape(a.url)}">{html.escape(a.filename)}</a></div>' for a in m.attachments)
        extra += "".join(f'<div class="att">▫ {html.escape(e.title or e.description or "embed")[:200]}</div>' for e in m.embeds)
        rows.append(f'<div class="msg"><b>{html.escape(str(m.author))}</b> <span>{when}</span><div>{body}</div>{extra}</div>')
    created = datetime.datetime.fromtimestamp(ticket["created_ts"], MSK).strftime("%d.%m.%Y %H:%M")
    return (f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Тикет #{ticket["id"]}</title>'
            '<style>body{font:14px system-ui,sans-serif;background:#16181d;color:#e6e8ee;max-width:820px;margin:24px auto;padding:0 16px}'
            '.msg{padding:8px 0;border-top:1px solid #2a2f3a}.msg span{color:#8b93a5;font-size:12px;margin-left:8px}'
            '.att{color:#8b93a5;font-size:13px}a{color:#7289ff}h1{font-size:20px}</style></head><body>'
            f'<h1>Тикет #{ticket["id"]} · {html.escape(ticket["category"] or "")}</h1>'
            f'<p>Автор: {html.escape(ticket["opener_name"] or "")} · создан {created} · сообщений: {len(messages)}</p>'
            + "".join(rows) + "</body></html>")


# ── views ───────────────────────────────────────────────────────────────────

class CategorySelect(ui.Select):
    def __init__(self, options: list[discord.SelectOption] | None = None):
        super().__init__(custom_id="support:open", placeholder="Выберите тему обращения…",
                         options=options or [discord.SelectOption(label="…")])

    async def callback(self, interaction: discord.Interaction):
        await open_ticket(interaction, self.values[0])


class PanelView(ui.View):
    def __init__(self, options=None):
        super().__init__(timeout=None)
        self.add_item(CategorySelect(options))


class TicketControls(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Закрыть", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="support:close")
    async def close(self, interaction: discord.Interaction, button: ui.Button):
        await close_from_interaction(interaction)

    @ui.button(label="Взять", emoji="🙋", style=discord.ButtonStyle.success, custom_id="support:claim")
    async def claim(self, interaction: discord.Interaction, button: ui.Button):
        cfg = modules.get_config(interaction.guild_id, "support_tickets")
        if not is_staff(interaction.user, cfg):
            return await interaction.response.send_message("Только поддержка может взять тикет.", ephemeral=True)
        t = await db.run(ticket_by_channel, interaction.channel_id)
        if t is None:
            return await interaction.response.send_message("Это не активный тикет.", ephemeral=True)
        if not await db.run(claim, t["id"], interaction.user):
            return await interaction.response.send_message("Тикет уже взят.", ephemeral=True)
        await interaction.response.send_message(f"🙋 {interaction.user.mention} взял этот тикет.")


class RatingView(ui.View):
    def __init__(self, ticket_id: int, opener_id: int):
        super().__init__(timeout=86400)
        self.ticket_id, self.opener_id = ticket_id, opener_id
        for stars in range(1, 6):
            b = ui.Button(label="★" * stars, style=discord.ButtonStyle.secondary)
            b.callback = self._make(stars)
            self.add_item(b)

    def _make(self, stars):
        async def cb(interaction: discord.Interaction):
            ok = await db.run(rate, self.ticket_id, self.opener_id, stars)
            await interaction.response.edit_message(content="Спасибо за оценку! " + "★" * stars if ok else "Оценка уже сохранена.", view=None)
        return cb


def is_staff(member: discord.Member, cfg: dict) -> bool:
    if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
        return True
    return any(r.id in (cfg["staff_roles"] or []) for r in member.roles)


async def open_ticket(interaction: discord.Interaction, category: str) -> None:
    guild, user = interaction.guild, interaction.user
    cfg = modules.get_config(guild.id, "support_tickets")
    if not cfg["enabled"]:
        return await interaction.response.send_message("Приём обращений сейчас выключен.", ephemeral=True)
    if await db.run(open_count, guild.id, user.id) >= cfg["max_open"]:
        return await interaction.response.send_message(f"У вас уже есть открытый тикет (лимит {cfg['max_open']}).", ephemeral=True)
    parent = guild.get_channel(cfg["category_channel"]) if cfg["category_channel"] else None
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True),
    }
    staff = [r for r in (guild.get_role(i) for i in cfg["staff_roles"] or []) if r]
    for r in staff:
        overwrites[r] = discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, read_message_history=True)
    await interaction.response.defer(ephemeral=True)
    try:
        name = f"ticket-{user.name}"[:90]
        channel = await guild.create_text_channel(name, category=parent if getattr(parent, "type", None) == discord.ChannelType.category else None,
                                                  overwrites=overwrites, topic=f"Тикет · {category} · {user} ({user.id})",
                                                  reason=f"Тикет поддержки от {user}")
    except discord.Forbidden:
        return await interaction.followup.send("❌ У бота нет права создавать каналы.", ephemeral=True)
    tid = await db.run(new_ticket, guild.id, channel.id, user, category)
    e = embeds.make(f"🎫 Тикет #{tid} · {category}", cfg["welcome"] or "Опишите вашу проблему.", 0x5865F2)
    ping = " ".join([user.mention] + ([r.mention for r in staff] if cfg["ping_staff"] else []))
    await channel.send(ping, embed=e, view=TicketControls(),
                       allowed_mentions=discord.AllowedMentions(users=[user], roles=staff if cfg["ping_staff"] else []))
    await interaction.followup.send(f"✅ Тикет создан: {channel.mention}", ephemeral=True)


async def close_from_interaction(interaction: discord.Interaction) -> None:
    cfg = modules.get_config(interaction.guild_id, "support_tickets")
    t = await db.run(ticket_by_channel, interaction.channel_id)
    if t is None:
        return await interaction.response.send_message("Это не активный тикет.", ephemeral=True)
    if interaction.user.id != t["opener_id"] and not is_staff(interaction.user, cfg):
        return await interaction.response.send_message("Закрыть тикет может автор или поддержка.", ephemeral=True)
    await interaction.response.send_message("🔒 Закрываю, сохраняю переписку…")
    await finish_ticket(interaction.guild, interaction.channel, t, cfg, interaction.user)


async def finish_ticket(guild: discord.Guild, channel, ticket: dict, cfg: dict, closer) -> None:
    messages = [m async for m in channel.history(limit=5000, oldest_first=True)]
    page = render_transcript(ticket, messages)
    await db.run(close_ticket, ticket["id"], page, len(messages), guild.id)
    log = guild.get_channel(cfg["transcript_channel"]) if cfg["transcript_channel"] else None
    if log:
        e = embeds.make(f"Тикет #{ticket['id']} закрыт", color=0x99AAB5)
        e.add_field(name="Автор", value=f"<@{ticket['opener_id']}>")
        e.add_field(name="Категория", value=ticket["category"] or "—")
        e.add_field(name="Закрыл", value=closer.mention if hasattr(closer, "mention") else str(closer))
        e.add_field(name="Сообщений", value=str(len(messages)))
        try:
            await log.send(embed=e, file=discord.File(io.BytesIO(page.encode("utf-8")), filename=f"ticket-{ticket['id']}.html"),
                           allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            pass
    opener = guild.get_member(ticket["opener_id"])
    if opener and cfg["ask_rating"]:
        try:
            await opener.send(f"Ваш тикет #{ticket['id']} на сервере **{guild.name}** закрыт. Как вам помощь?",
                              view=RatingView(ticket["id"], opener.id))
        except (discord.Forbidden, discord.HTTPException):
            pass
    try:
        await channel.delete(reason=f"Тикет #{ticket['id']} закрыт")
    except (discord.Forbidden, discord.NotFound):
        pass


class SupportTickets(commands.Cog):
    ticket = app_commands.Group(name="ticket", description="Тикеты поддержки", guild_only=True)

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(PanelView())
        self.bot.add_view(TicketControls())

    async def _staff(self, interaction) -> dict | None:
        cfg = modules.get_config(interaction.guild_id, "support_tickets")
        t = await db.run(ticket_by_channel, interaction.channel_id)
        if t is None:
            await interaction.response.send_message("Эта команда работает только внутри тикета.", ephemeral=True)
        elif not is_staff(interaction.user, cfg):
            await interaction.response.send_message("Только для поддержки.", ephemeral=True)
        else:
            return cfg
        return None

    @ticket.command(name="close", description="Закрыть этот тикет")
    async def close(self, interaction: discord.Interaction):
        await close_from_interaction(interaction)

    @ticket.command(name="add", description="Добавить участника в тикет")
    async def add(self, interaction: discord.Interaction, user: discord.Member):
        if await self._staff(interaction) is None:
            return
        await interaction.channel.set_permissions(user, view_channel=True, send_messages=True, read_message_history=True)
        await interaction.response.send_message(f"✅ {user.mention} добавлен в тикет.")

    @ticket.command(name="remove", description="Убрать участника из тикета")
    async def remove(self, interaction: discord.Interaction, user: discord.Member):
        cfg = await self._staff(interaction)
        if cfg is None:
            return
        t = await db.run(ticket_by_channel, interaction.channel_id)
        if user.id == t["opener_id"]:
            return await interaction.response.send_message("Автора тикета убрать нельзя.", ephemeral=True)
        await interaction.channel.set_permissions(user, overwrite=None)
        await interaction.response.send_message(f"✅ {user.display_name} убран из тикета.")


# ── panel ───────────────────────────────────────────────────────────────────

async def _publish(ctx, p):
    cats = parse_categories(modules.get_config(ctx.guild.id, "support_tickets")["categories"])
    if not cats:
        raise modules.ValidationError("Добавьте хотя бы одну категорию обращений и сохраните настройки")
    ch = ctx.guild.get_channel(p["channel"])
    if ch is None or not hasattr(ch, "send"):
        raise modules.ValidationError("Выберите текстовый канал")
    options = [discord.SelectOption(label=c["label"], value=c["label"], emoji=c["emoji"], description=c["description"]) for c in cats]
    e = embeds.make("🎫 Поддержка", "Выберите тему в меню ниже — для вас создадут приватный канал с поддержкой.", 0x5865F2)
    try:
        await ch.send(embed=e, view=PanelView(options))
    except discord.Forbidden:
        raise modules.ValidationError("У бота нет прав писать в этот канал")
    return f"Панель опубликована в #{ch.name}"


MODULE.actions.append(modules.Action("publish", "Опубликовать панель обращений", _publish, params=[
    {"key": "channel", "label": "Канал", "type": "channel", "kind": "text"}],
    description="Отправит сообщение с выбором темы. После изменения категорий опубликуйте панель заново"))


async def _close_by_id(ctx, p):
    t = await db.run(get_ticket, int(p["id"]), ctx.guild.id)
    if t is None or t["status"] != "open":
        raise modules.ValidationError("Открытого тикета с таким номером нет")
    ch = ctx.guild.get_channel(t["channel_id"])
    if ch is None:
        await db.run(close_ticket, t["id"], render_transcript(t, []), 0, ctx.guild.id)
        return "Канала уже нет — тикет помечен закрытым"
    await finish_ticket(ctx.guild, ch, t, modules.get_config(ctx.guild.id, "support_tickets"), ctx.guild.get_member(ctx.user_id) or "панель")
    return "Тикет закрыт, переписка сохранена"


MODULE.actions.append(modules.Action("close", "Закрыть тикет", _close_by_id, params=[
    {"key": "id", "label": "Номер тикета", "type": "number", "min": 1}], danger=True,
    confirm="Канал тикета будет удалён, а переписка сохранится в архиве."))


# transcripts are viewable from the panel (same login as everything else)
def _register_routes(app: web.Application) -> None:
    from dashboard import server

    async def view(request: web.Request):
        _, guild = server._guild_for(request)
        try:
            tid = int(request.match_info["tid"])
        except ValueError:
            raise web.HTTPBadRequest()
        page = await db.run(transcript_html, tid, guild.id)
        if page is None:
            raise web.HTTPNotFound(text="Переписка не найдена")
        return web.Response(text=page, content_type="text/html",
                            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:"})

    app.router.add_get("/transcripts/{gid}/{tid}", view)


from dashboard import server as _server  # noqa: E402
_server.register_routes(_register_routes)


async def setup(bot):
    await bot.add_cog(SupportTickets(bot))
