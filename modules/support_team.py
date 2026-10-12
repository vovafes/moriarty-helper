"""
Support & team modules (ported from the AlphaCloud panel):

Voice support:  support_notify, support_sort (queue), support_hours, support_call, voice_activity, support_entries
Team:           teamlist, team_updates, absences
Misc:           ap (mandatory recording for suspected cheaters), frak (factions)
"""

import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import db, embeds, modules
from core.tools_group import install as install_tools, tool, uninstall as uninstall_tools
from modules.community_tools import parse_color

db.register_schema("""
CREATE TABLE IF NOT EXISTS support_hours (
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, ymd TEXT NOT NULL, minutes INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id, ymd)
);
CREATE TABLE IF NOT EXISTS support_state (
    guild_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT, PRIMARY KEY (guild_id, key)
);
CREATE TABLE IF NOT EXISTS voice_points (
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, points INTEGER NOT NULL DEFAULT 0,
    convs INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS support_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, team_id INTEGER NOT NULL,
    user_id INTEGER, note TEXT, ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS teamlist_msgs (
    guild_id INTEGER NOT NULL, name TEXT NOT NULL, channel_id INTEGER, message_id INTEGER, PRIMARY KEY (guild_id, name)
);
CREATE TABLE IF NOT EXISTS absences (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, reason TEXT,
    start_ts INTEGER NOT NULL, end_ts INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1, old_nick TEXT
);
CREATE TABLE IF NOT EXISTS ap_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, moderator_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending', clip TEXT, check_msg_id INTEGER, ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS factions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, name TEXT NOT NULL, leader_id INTEGER,
    role_id INTEGER, warns INTEGER NOT NULL DEFAULT 0, ts INTEGER NOT NULL
);
""")

MSK_OFFSET = 3 * 3600
TEAM_ROLE_HELP = "Роль сотрудников поддержки"


def _ch(label, key, kind="text", **kw):
    return {"key": key, "label": label, "type": "channel", "kind": kind, "default": None, **kw}


SUPPORT_NOTIFY = modules.register(modules.Module(
    key="support_notify", title="Уведомления поддержки", icon="🔔", category="support", default_enabled=False,
    description="Когда человек заходит в комнату ожидания, команда получает уведомление.",
    fields=[
        {"key": "wait_rooms", "label": "Комнаты ожидания (до 25)", "type": "channels", "kind": "voice", "default": [], "group": "Каналы и роли"},
        _ch("Канал уведомлений", "notify_channel", group="Каналы и роли"),
        {"key": "team_role", "label": "Роль команды", "type": "role", "default": None, "group": "Каналы и роли", "help": TEAM_ROLE_HELP},
        {"key": "color", "label": "Цвет эмбеда", "type": "text", "default": "#BE1FB6", "group": "Сообщения"},
        {"key": "text", "label": "Текст уведомления", "type": "longtext", "group": "Сообщения",
         "default": "{user} ждёт поддержку в {channel}.", "help": "{user} {name} {channel}"},
    ],
))
SUPPORT_SORT = modules.register(modules.Module(
    key="support_sort", title="Сортировка поддержки", icon="📶", category="support", default_enabled=False,
    description="Очередь обращений: сначала VIP, затем по времени ожидания. Показать очередь — команда /queue.",
    fields=[
        {"key": "channels", "label": "Голосовые каналы очереди (до 5)", "type": "channels", "kind": "voice", "default": [], "group": "Каналы и роли"},
        {"key": "vip_roles", "label": "VIP-роли (до 5)", "type": "roles", "default": [], "group": "Каналы и роли"},
    ],
    tables=[{"id": "queue", "title": "Сейчас в очереди", "columns": [
        {"key": "pos", "label": "#"}, {"key": "user", "label": "Участник"}, {"key": "vip", "label": "VIP"}, {"key": "wait", "label": "Ждёт"}]}],
))
SUPPORT_HOURS = modules.register(modules.Module(
    key="support_hours", title="Часы поддержки", icon="⏱", category="support", default_enabled=False,
    description="Считает, сколько времени сотрудники провели в разговорах с пользователями, и показывает статистику эмбедом.",
    fields=[
        {"key": "support_channels", "label": "Каналы поддержки (голосовые)", "type": "channels", "kind": "voice", "default": [], "group": "Общее"},
        _ch("Канал отображения", "display_channel", group="Общее"),
        {"key": "format", "label": "Формат отображения", "type": "select", "default": "total", "group": "Оформление",
         "options": [{"value": "total", "label": "Общий промежуток времени"}, {"value": "weekday", "label": "По дням недели"}]},
        {"key": "days", "label": "За сколько дней считать", "type": "number", "default": 14, "min": 1, "max": 90, "group": "Оформление"},
        {"key": "color", "label": "Цвет эмбеда", "type": "text", "default": "#BE1FB6", "group": "Оформление"},
    ],
    tables=[{"id": "hours", "title": "Часы за период", "columns": [{"key": "user", "label": "Сотрудник"}, {"key": "hours", "label": "Часов"}]}],
))
SUPPORT_CALL = modules.register(modules.Module(
    key="support_call", title="Вызов в поддержку", icon="📣", category="support", default_enabled=False,
    description="Сотрудник командой /call вызывает пользователя в голосовой канал поддержки (приглашение придёт в ЛС).",
    fields=[
        {"key": "team_role", "label": "Роль команды", "type": "role", "default": None},
        _ch("Канал поддержки", "support_channel", "voice"),
        {"key": "message", "label": "Текст приглашения", "type": "longtext", "default": "Вас ждёт поддержка! Зайдите в голосовой канал {channel}.",
         "help": "{user} {channel}"},
        {"key": "cooldown", "label": "Пауза между вызовами одного человека, сек", "type": "number", "default": 60, "min": 0, "max": 3600},
    ],
))
VOICE_ACTIVITY = modules.register(modules.Module(
    key="voice_activity", title="Активность голосовой поддержки", icon="🎙", category="support", default_enabled=False,
    description="Баллы за активные разговоры поддержки: микрофон и звук включены, разговор не короче минимума, пара считается раз в 30 минут.",
    fields=[
        {"key": "team_role", "label": "Роль команды", "type": "role", "default": None},
        {"key": "channels", "label": "Каналы поддержки (голосовые)", "type": "channels", "kind": "voice", "default": []},
        {"key": "min_minutes", "label": "Минимальная длительность разговора, мин", "type": "number", "default": 3, "min": 1, "max": 120},
        {"key": "points", "label": "Баллов за разговор", "type": "number", "default": 1, "min": 1, "max": 1000},
    ],
    tables=[{"id": "top", "title": "Рейтинг", "columns": [
        {"key": "user", "label": "Сотрудник"}, {"key": "points", "label": "Баллы"}, {"key": "convs", "label": "Разговоров"}]}],
))
SUPPORT_ENTRIES = modules.register(modules.Module(
    key="support_entries", title="Записи поддержки", icon="📝", category="support", default_enabled=False,
    description="Сотрудники фиксируют выполненную помощь командой /entry, а бот ведёт журнал и топ-10.",
    fields=[
        _ch("Канал логов", "log_channel", group="Каналы и роли"),
        {"key": "team_role", "label": "Роль команды", "type": "role", "default": None, "group": "Каналы и роли"},
        {"key": "manager_role", "label": "Роль менеджера", "type": "role", "default": None, "group": "Каналы и роли"},
        _ch("Канал топ-10", "top_channel", group="Сообщения"),
        {"key": "color", "label": "Цвет эмбеда", "type": "text", "default": "#BE1FB6", "group": "Сообщения"},
    ],
    tables=[{"id": "top", "title": "Топ-10", "columns": [{"key": "user", "label": "Сотрудник"}, {"key": "count", "label": "Записей"}]}],
))
TEAMLIST = modules.register(modules.Module(
    key="teamlist", title="Список команды", icon="👥", category="team", default_enabled=False,
    description="Автоматически обновляемые списки участников команды по ролям — сообщение в канале правится само.",
    fields=[{"key": "lists", "label": "Списки (до 5)", "type": "rules", "max": 5, "default": [], "item_fields": [
        {"key": "name", "label": "Внутреннее имя (латиницей)", "type": "text", "default": "team"},
        {"key": "title", "label": "Заголовок", "type": "text", "default": "Команда"},
        {"key": "channel", "label": "Канал", "type": "channel", "default": None},
        {"key": "roles", "label": "Роли (по порядку важности)", "type": "roles", "default": []}]}],
))
TEAM_UPDATES = modules.register(modules.Module(
    key="team_updates", title="Обновления команды", icon="📣", category="team", default_enabled=False,
    description="Объявляет о приёме, снятии и предупреждениях сотрудников. Команды: /team_add, /team_kick, /team_warn.",
    fields=[
        _ch("Публичный канал объявлений (пингует человека)", "announce_channel", group="Каналы и роли"),
        _ch("Внутренний канал логов (без пинга)", "log_channel", group="Каналы и роли"),
        {"key": "manager_roles", "label": "Роли менеджеров", "type": "roles", "default": [], "group": "Каналы и роли"},
        {"key": "team_role", "label": "Роль команды", "type": "role", "default": None, "group": "Каналы и роли"},
        {"key": "former_role", "label": "Роль бывшего сотрудника (при /team_kick)", "type": "role", "default": None, "group": "Каналы и роли"},
        {"key": "color", "label": "Цвет эмбеда", "type": "text", "default": "#BE1FB6", "group": "Оформление"},
        {"key": "warn_role_1", "label": "Роль предупреждения 1/3", "type": "role", "default": None, "group": "Предупреждения"},
        {"key": "warn_role_2", "label": "Роль предупреждения 2/3", "type": "role", "default": None, "group": "Предупреждения"},
        {"key": "warn_role_3", "label": "Роль предупреждения 3/3", "type": "role", "default": None, "group": "Предупреждения"},
    ],
))
ABSENCES = modules.register(modules.Module(
    key="absences", title="Отсутствия", icon="🏖", category="team", default_enabled=False,
    description="Сотрудники объявляют отсутствие командой /absence: бот выдаёт роль, помечает ник и снимает всё, когда срок вышел.",
    fields=[
        _ch("Канал логов", "log_channel", group="Каналы и роли"),
        _ch("Канал списка (необязательно)", "list_channel", group="Каналы и роли"),
        {"key": "team_role", "label": "Роль команды", "type": "role", "default": None, "group": "Каналы и роли"},
        {"key": "manager_role", "label": "Роль менеджера отсутствий", "type": "role", "default": None, "group": "Каналы и роли"},
        {"key": "absence_role", "label": "Роль на время отсутствия", "type": "role", "default": None, "group": "Автоматизация"},
        {"key": "mark_nick", "label": "Помечать ник «[Отсутствует]»", "type": "bool", "default": False, "group": "Автоматизация"},
        {"key": "max_days", "label": "Максимум дней", "type": "number", "default": 30, "min": 1, "max": 365, "group": "Автоматизация"},
    ],
    tables=[{"id": "active", "title": "Сейчас отсутствуют", "columns": [
        {"key": "id", "label": "#"}, {"key": "user", "label": "Сотрудник"}, {"key": "reason", "label": "Причина"},
        {"key": "end_ts", "label": "До", "format": "time"}]}],
))
AP = modules.register(modules.Module(
    key="ap", title="Обязательная запись", icon="🎥", category="team", default_enabled=False,
    description="Обязательная запись для подозреваемых в читах: выдача роли, отправка клипа, проверка модератором и анализ.",
    fields=[
        {"key": "ap_role", "label": "Роль AP", "type": "role", "default": None, "group": "Каналы и роли"},
        {"key": "mod_roles", "label": "Роли модераторов", "type": "roles", "default": [], "group": "Каналы и роли"},
        _ch("Канал списка", "list_channel", group="Каналы и роли"),
        _ch("Канал отправки", "submit_channel", group="Каналы и роли", help="Сюда подозреваемый присылает клип (файл или ссылка)"),
        _ch("Канал проверки", "check_channel", group="Каналы и роли"),
        _ch("Канал анализов", "analysis_channel", group="Каналы и роли"),
        _ch("Канал логов", "log_channel", group="Каналы и роли"),
        {"key": "dm_assigned", "label": "ЛС при назначении записи", "type": "longtext", "group": "Личные сообщения",
         "default": "Вам назначена обязательная запись. Отправьте клип в канал {channel}."},
        {"key": "dm_approved", "label": "ЛС при принятии клипа", "type": "longtext", "group": "Личные сообщения",
         "default": "Ваш клип принят и передан на анализ."},
        {"key": "dm_rejected", "label": "ЛС при отклонении клипа", "type": "longtext", "group": "Личные сообщения",
         "default": "Клип отклонён. Отправьте новую запись."},
    ],
    tables=[{"id": "entries", "title": "Записи", "columns": [
        {"key": "id", "label": "#"}, {"key": "user", "label": "Участник"}, {"key": "status", "label": "Статус"},
        {"key": "ts", "label": "Назначена", "format": "time"}]}],
))
FRAK = modules.register(modules.Module(
    key="frak", title="Фракции", icon="🏴", category="team", default_enabled=False,
    description="Управление фракциями: список, лидеры, предупреждения и обзор в канале.",
    fields=[
        {"key": "allowed_role", "label": "Роль, которой разрешено управлять фракциями", "type": "role", "default": None, "group": "Каналы и роли"},
        _ch("Канал обзора фракций", "overview_channel", group="Каналы и роли"),
        {"key": "color", "label": "Цвет эмбеда", "type": "text", "default": "#BE1FB6", "group": "Оформление"},
        {"key": "banner", "label": "URL баннера", "type": "text", "default": "", "group": "Оформление"},
        _ch("Канал логов", "log_channel", group="Каналы логов"),
        _ch("Канал предупреждений", "warn_channel", group="Каналы логов"),
        _ch("Канал логов создания", "create_channel", group="Каналы логов"),
        _ch("Канал логов удаления", "delete_channel", group="Каналы логов"),
    ],
    tables=[{"id": "list", "title": "Фракции", "columns": [
        {"key": "id", "label": "#"}, {"key": "name", "label": "Название"}, {"key": "leader", "label": "Лидер"},
        {"key": "warns", "label": "Предупреждений"}, {"key": "ts", "label": "Создана", "format": "time"}]}],
))


# ── pure helpers (unit-tested) ──────────────────────────────────────────────

def msk_ymd(ts: float | None = None) -> str:
    t = time.gmtime((ts if ts is not None else time.time()) + MSK_OFFSET)
    return f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"


def msk_weekday(ymd: str) -> int:
    import datetime
    return datetime.date.fromisoformat(ymd).weekday()


def queue_order(entries: list[dict]) -> list[dict]:
    """entries: {user_id, joined, vip}. VIPs first, then by join time."""
    return sorted(entries, key=lambda e: (not e["vip"], e["joined"]))


def human_wait(seconds: float) -> str:
    seconds = int(max(0, seconds))
    return f"{seconds // 60} мин {seconds % 60:02d} с" if seconds >= 60 else f"{seconds} с"


def conversation_counts(total_s: float, active_s: float, min_minutes: int) -> bool:
    """A talk counts when it lasted the minimum and at least half of it was active."""
    return total_s >= min_minutes * 60 and total_s > 0 and active_s * 2 >= total_s


def team_voice_active(state: discord.VoiceState | None) -> bool:
    return bool(state and state.channel and not (state.self_mute or state.mute or state.self_deaf or state.deaf))


def listener_active(state: discord.VoiceState | None) -> bool:
    return bool(state and state.channel and not (state.self_deaf or state.deaf))


def format_team_lines(members: list[tuple[str, int]]) -> str:
    return "\n".join(f"{i}. <@{uid}>" for i, (_, uid) in enumerate(members, 1)) or "—"


# ── cog ─────────────────────────────────────────────────────────────────────

class ApView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _decide(self, interaction: discord.Interaction, approved: bool):
        cfg = modules.get_config(interaction.guild_id, "ap")
        if not set(cfg["mod_roles"]) & {r.id for r in interaction.user.roles} and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Проверять клипы могут только модераторы.", ephemeral=True)
        rows = await db.run(db.query, "SELECT * FROM ap_entries WHERE check_msg_id=? AND guild_id=?", (interaction.message.id, interaction.guild_id))
        if not rows:
            return await interaction.response.send_message("Запись не найдена.", ephemeral=True)
        entry = dict(rows[0])
        member = interaction.guild.get_member(entry["user_id"])
        await db.run(db.execute, "UPDATE ap_entries SET status=? WHERE id=?", ("approved" if approved else "pending", entry["id"]))
        e = interaction.message.embeds[0] if interaction.message.embeds else embeds.make("Клип")
        e.color = 0x3BA55D if approved else 0xED4245
        e.add_field(name="Решение", value=f"{'✅ принят' if approved else '❌ отклонён'} — {interaction.user.mention}", inline=False)
        await interaction.response.edit_message(embed=e, view=None)
        if member is not None:
            text = cfg["dm_approved" if approved else "dm_rejected"]
            try:
                await member.send(text[:2000])
            except (discord.Forbidden, discord.HTTPException):
                pass
            if approved and cfg["ap_role"]:
                role = interaction.guild.get_role(cfg["ap_role"])
                if role in member.roles:
                    try:
                        await member.remove_roles(role, reason="AP: клип принят")
                    except (discord.Forbidden, discord.HTTPException):
                        pass
        if approved and cfg["analysis_channel"]:
            ch = interaction.guild.get_channel(cfg["analysis_channel"])
            if ch is not None:
                try:
                    await ch.send(embed=embeds.make("Клип на анализ", f"<@{entry['user_id']}>\n{entry['clip'] or ''}"[:2000]))
                except (discord.Forbidden, discord.HTTPException):
                    pass

    @discord.ui.button(label="Принять", style=discord.ButtonStyle.success, custom_id="ap:ok")
    async def ok(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._decide(interaction, True)

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.danger, custom_id="ap:no")
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._decide(interaction, False)


def _has_role(member, role_id) -> bool:
    return bool(role_id) and any(r.id == role_id for r in member.roles)


def _is_manager(member, role_ids) -> bool:
    return member.guild_permissions.administrator or bool(set(role_ids) & {r.id for r in member.roles})


class SupportTeam(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.waiting: dict[tuple[int, int], dict] = {}        # (guild, user) -> {joined, channel, msg}
        self.talks: dict[tuple[int, int, int], dict] = {}     # (guild, team, user) -> {start, active, last_tick}
        self.last_pair: dict[tuple[int, int, int], float] = {}
        self.call_cd: dict[tuple[int, int], float] = {}
        self.kick_reasons: dict[tuple[int, int], str] = {}
        self._hours_acc: dict[tuple[int, int], int] = {}

    async def cog_load(self):
        self.bot.add_view(ApView())
        self.minute_loop.start()
        self.refresh_loop.start()
        install_tools(self.bot, self)

    async def cog_unload(self):
        self.minute_loop.cancel()
        self.refresh_loop.cancel()
        uninstall_tools(self)

    # ── voice: notify + queue ───────────────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        gid = member.guild.id
        key = (gid, member.id)
        notify = modules.get_config(gid, "support_notify")
        sort = modules.get_config(gid, "support_sort")
        queue_rooms = set(sort["channels"][:5]) if sort["enabled"] else set()
        wait_rooms = set(notify["wait_rooms"][:25]) if notify["enabled"] else set()
        rooms = queue_rooms | wait_rooms
        joined = after.channel and after.channel.id in rooms and (not before.channel or before.channel.id != after.channel.id)
        if joined:
            if _has_role(member, notify["team_role"]):
                return
            self.waiting[key] = {"joined": time.time(), "channel": after.channel.id, "msg": None}
            if notify["enabled"] and after.channel.id in wait_rooms:
                await self._notify(member, after.channel, notify)
        left = key in self.waiting and (not after.channel or after.channel.id != self.waiting[key]["channel"])
        if left:
            w = self.waiting.pop(key)
            await self._close_notice(member, w, accepted=bool(after.channel))

    async def _notify(self, member, channel, cfg):
        ch = member.guild.get_channel(cfg["notify_channel"]) if cfg["notify_channel"] else None
        if ch is None:
            return
        role = member.guild.get_role(cfg["team_role"]) if cfg["team_role"] else None
        sort = modules.get_config(member.guild.id, "support_sort")
        vip = sort["enabled"] and bool(set(sort["vip_roles"][:5]) & {r.id for r in member.roles})
        text = (cfg["text"] or "{user} ждёт поддержку в {channel}.").replace("{user}", member.mention).replace("{name}", member.display_name).replace("{channel}", channel.mention)
        e = embeds.make("👑 VIP-обращение" if vip else "🔔 Новое обращение", text, color=parse_color(cfg["color"]))
        try:
            msg = await ch.send(role.mention if role else None, embed=e,
                                allowed_mentions=discord.AllowedMentions(roles=[role] if role else [], users=[member]))
            self.waiting[(member.guild.id, member.id)]["msg"] = (ch.id, msg.id)
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _close_notice(self, member, w, accepted: bool):
        if not w.get("msg"):
            return
        ch = member.guild.get_channel(w["msg"][0])
        if ch is None:
            return
        try:
            msg = await ch.fetch_message(w["msg"][1])
            e = msg.embeds[0] if msg.embeds else embeds.make("Обращение")
            e.color = 0x3BA55D if accepted else 0x99AAB5
            e.set_footer(text="Принято в работу" if accepted else "Пользователь ушёл")
            await msg.edit(embed=e)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    def queue_entries(self, guild) -> list[dict]:
        sort = modules.get_config(guild.id, "support_sort")
        vips = set(sort["vip_roles"][:5])
        out = []
        for (gid, uid), w in self.waiting.items():
            m = guild.get_member(uid)
            if gid != guild.id or m is None:
                continue
            out.append({"user_id": uid, "joined": w["joined"], "vip": bool(vips & {r.id for r in m.roles})})
        return queue_order(out)

    @tool("queue", "Очередь обращений в поддержку")
    async def queue(self, interaction: discord.Interaction):
        entries = self.queue_entries(interaction.guild)
        now = time.time()
        lines = [f"**{i}.** {'👑 ' if e['vip'] else ''}<@{e['user_id']}> — {human_wait(now - e['joined'])}" for i, e in enumerate(entries, 1)]
        await interaction.response.send_message(embed=embeds.make("Очередь поддержки", "\n".join(lines) or "Очередь пуста."),
                                                ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    # ── minute loop: support hours + voice activity ─────────────────────

    @tasks.loop(seconds=30)
    async def minute_loop(self):
        now = time.time()
        for guild in self.bot.guilds:
            hours = modules.get_config(guild.id, "support_hours")
            va = modules.get_config(guild.id, "voice_activity")
            notify = modules.get_config(guild.id, "support_notify")
            if hours["enabled"]:
                team_role = notify["team_role"]
                for cid in hours["support_channels"]:
                    ch = guild.get_channel(cid)
                    if ch is None:
                        continue
                    team = [m for m in ch.members if _has_role(m, team_role)]
                    users = [m for m in ch.members if not m.bot and not _has_role(m, team_role)]
                    if team and users:
                        for m in team:
                            self._tick_hours(guild.id, m.id)
            if va["enabled"]:
                self._voice_activity_tick(guild, va, now)

    def _tick_hours(self, gid: int, uid: int):
        self._hours_acc[(gid, uid)] = self._hours_acc.get((gid, uid), 0) + 30
        if self._hours_acc[(gid, uid)] >= 60:
            self._hours_acc[(gid, uid)] -= 60
            db.execute("INSERT INTO support_hours (guild_id, user_id, ymd, minutes) VALUES (?,?,?,1) "
                       "ON CONFLICT(guild_id, user_id, ymd) DO UPDATE SET minutes=minutes+1", (gid, uid, msk_ymd()))

    def _voice_activity_tick(self, guild, cfg, now):
        seen = set()
        for cid in cfg["channels"]:
            ch = guild.get_channel(cid)
            if ch is None:
                continue
            team = [m for m in ch.members if _has_role(m, cfg["team_role"])]
            users = [m for m in ch.members if not m.bot and not _has_role(m, cfg["team_role"])]
            for t in team:
                for u in users:
                    k = (guild.id, t.id, u.id)
                    seen.add(k)
                    talk = self.talks.setdefault(k, {"start": now, "active": 0.0, "last": now})
                    dt = now - talk["last"]
                    talk["last"] = now
                    if team_voice_active(t.voice) and listener_active(u.voice):
                        talk["active"] += dt
        for k in [k for k in self.talks if k[0] == guild.id and k not in seen]:
            talk = self.talks.pop(k)
            total = talk["last"] - talk["start"]
            if conversation_counts(total, talk["active"], cfg["min_minutes"]) and now - self.last_pair.get(k, 0) >= 1800:
                self.last_pair[k] = now
                db.execute("INSERT INTO voice_points (guild_id, user_id, points, convs) VALUES (?,?,?,1) "
                           "ON CONFLICT(guild_id, user_id) DO UPDATE SET points=points+?, convs=convs+1",
                           (guild.id, k[1], cfg["points"], cfg["points"]))

    # ── slow refresh loop: support hours embed, teamlists, absences ─────

    @tasks.loop(minutes=5)
    async def refresh_loop(self):
        now = int(time.time())
        for guild in self.bot.guilds:
            try:
                await self._refresh_hours(guild)
                await self._refresh_teamlists(guild)
                await self._end_absences(guild, now)
            except Exception as e:  # one broken guild must not stop the others
                print(f"WARNING: support_team refresh failed for {guild.id}: {e}")

    @minute_loop.before_loop
    @refresh_loop.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @minute_loop.error
    async def _minute_err(self, error):
        print(f"WARNING: support_team minute_loop crashed, restarting: {error}")
        if not self.minute_loop.is_running():
            self.minute_loop.start()

    @refresh_loop.error
    async def _refresh_err(self, error):
        print(f"WARNING: support_team refresh_loop crashed, restarting: {error}")
        if not self.refresh_loop.is_running():
            self.refresh_loop.start()

    async def _upsert_message(self, guild, name: str, channel_id: int, embed: discord.Embed):
        ch = guild.get_channel(channel_id)
        if ch is None:
            return
        rows = await db.run(db.query, "SELECT * FROM teamlist_msgs WHERE guild_id=? AND name=?", (guild.id, name))
        if rows and rows[0]["channel_id"] == channel_id and rows[0]["message_id"]:
            try:
                await (await ch.fetch_message(rows[0]["message_id"])).edit(embed=embed)
                return
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        try:
            msg = await ch.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return
        await db.run(db.execute, "INSERT OR REPLACE INTO teamlist_msgs (guild_id, name, channel_id, message_id) VALUES (?,?,?,?)",
                     (guild.id, name, channel_id, msg.id))

    async def _refresh_hours(self, guild):
        cfg = modules.get_config(guild.id, "support_hours")
        if not cfg["enabled"] or not cfg["display_channel"]:
            return
        e = embeds.make("⏱ Часы поддержки", color=parse_color(cfg["color"]))
        rows = await db.run(hours_rows, guild.id, cfg["days"])
        if not rows:
            e.description = "Данные ещё собираются."
        elif cfg["format"] == "weekday":
            names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
            per = await db.run(weekday_minutes, guild.id, cfg["days"])
            e.description = "\n".join(f"**{names[i]}** — {per[i] / 60:.1f} ч" for i in range(7))
        else:
            e.description = "\n".join(f"**{i}.** <@{r['user_id']}> — {r['minutes'] / 60:.1f} ч" for i, r in enumerate(rows[:15], 1))
        await self._upsert_message(guild, "support_hours", cfg["display_channel"], e)

    async def _refresh_teamlists(self, guild):
        cfg = modules.get_config(guild.id, "teamlist")
        if not cfg["enabled"]:
            return
        for lst in cfg["lists"][:5]:
            if not lst.get("channel") or not lst.get("roles"):
                continue
            e = embeds.make(lst.get("title") or "Команда", color=COLOR_TEAM)
            seen = set()
            for rid in lst["roles"]:
                role = guild.get_role(rid)
                if role is None:
                    continue
                people = [m for m in role.members if m.id not in seen and not m.bot]
                seen.update(m.id for m in people)
                e.add_field(name=f"{role.name} ({len(people)})", value="\n".join(m.mention for m in people)[:1000] or "—", inline=False)
            await self._upsert_message(guild, f"teamlist:{lst.get('name') or 'team'}", lst["channel"], e)

    async def _end_absences(self, guild, now):
        cfg = modules.get_config(guild.id, "absences")
        if not cfg["enabled"]:
            return
        for r in await db.run(db.query, "SELECT * FROM absences WHERE guild_id=? AND active=1 AND end_ts<=?", (guild.id, now)):
            await finish_absence(guild, cfg, dict(r), "срок истёк")

    # ── slash commands ──────────────────────────────────────────────────

    @tool("call", "Вызвать пользователя в поддержку")
    async def call(self, interaction: discord.Interaction, user: discord.Member):
        cfg = modules.get_config(interaction.guild_id, "support_call")
        if not cfg["enabled"]:
            return await interaction.response.send_message("Модуль выключен.", ephemeral=True)
        if not _has_role(interaction.user, cfg["team_role"]) and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Вызывать могут только сотрудники.", ephemeral=True)
        ch = interaction.guild.get_channel(cfg["support_channel"]) if cfg["support_channel"] else None
        if ch is None:
            return await interaction.response.send_message("Канал поддержки не настроен.", ephemeral=True)
        key = (interaction.guild_id, user.id)
        if time.time() - self.call_cd.get(key, 0) < cfg["cooldown"]:
            return await interaction.response.send_message("Этого человека недавно уже вызывали.", ephemeral=True)
        self.call_cd[key] = time.time()
        text = cfg["message"].replace("{user}", user.mention).replace("{channel}", ch.mention)
        try:
            await user.send(embed=embeds.make("📣 Вас вызывают в поддержку", text))
        except (discord.Forbidden, discord.HTTPException):
            return await interaction.response.send_message("ЛС закрыты — напишите человеку сами.", ephemeral=True)
        await interaction.response.send_message(f"Вызов отправлен {user.display_name}.", ephemeral=True)

    @tool("entry", "Записать выполненную помощь")
    @app_commands.describe(user="Кому помогли", note="Что сделали")
    async def entry(self, interaction: discord.Interaction, user: discord.Member, note: app_commands.Range[str, 1, 500]):
        cfg = modules.get_config(interaction.guild_id, "support_entries")
        if not cfg["enabled"]:
            return await interaction.response.send_message("Модуль выключен.", ephemeral=True)
        if not _has_role(interaction.user, cfg["team_role"]) and not _is_manager(interaction.user, [cfg["manager_role"]] if cfg["manager_role"] else []):
            return await interaction.response.send_message("Записи ведут только сотрудники.", ephemeral=True)
        eid = (await db.run(db.execute, "INSERT INTO support_entries (guild_id, team_id, user_id, note, ts) VALUES (?,?,?,?,?)",
                            (interaction.guild_id, interaction.user.id, user.id, note, int(time.time())))).lastrowid
        ch = interaction.guild.get_channel(cfg["log_channel"]) if cfg["log_channel"] else None
        if ch is not None:
            try:
                await ch.send(embed=embeds.make(f"Запись #{eid}", f"{interaction.user.mention} → {user.mention}\n{note}", color=parse_color(cfg["color"])),
                              allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException):
                pass
        await interaction.response.send_message(f"Запись #{eid} сохранена.", ephemeral=True)

    @tool("absence", "Объявить своё отсутствие")
    @app_commands.describe(days="На сколько дней", reason="Причина")
    async def absence(self, interaction: discord.Interaction, days: app_commands.Range[int, 1, 365], reason: app_commands.Range[str, 1, 300]):
        cfg = modules.get_config(interaction.guild_id, "absences")
        if not cfg["enabled"]:
            return await interaction.response.send_message("Модуль выключен.", ephemeral=True)
        if cfg["team_role"] and not _has_role(interaction.user, cfg["team_role"]):
            return await interaction.response.send_message("Отсутствие могут объявлять только сотрудники.", ephemeral=True)
        if days > cfg["max_days"]:
            return await interaction.response.send_message(f"Не больше {cfg['max_days']} дн.", ephemeral=True)
        if await db.run(db.query, "SELECT 1 FROM absences WHERE guild_id=? AND user_id=? AND active=1", (interaction.guild_id, interaction.user.id)):
            return await interaction.response.send_message("У вас уже есть активное отсутствие.", ephemeral=True)
        await interaction.response.send_message(await start_absence(interaction.guild, cfg, interaction.user, days, reason), ephemeral=True)

    @tool("team_add", "Принять сотрудника в команду")
    async def team_add(self, interaction: discord.Interaction, member: discord.Member):
        cfg = await self._team_gate(interaction)
        if cfg is None:
            return
        role = interaction.guild.get_role(cfg["team_role"]) if cfg["team_role"] else None
        if role is None:
            return await interaction.response.send_message("Роль команды не настроена.", ephemeral=True)
        try:
            await member.add_roles(role, reason=f"/team_add — {interaction.user}")
            if cfg["former_role"]:
                fr = interaction.guild.get_role(cfg["former_role"])
                if fr in member.roles:
                    await member.remove_roles(fr)
        except (discord.Forbidden, discord.HTTPException):
            return await interaction.response.send_message("Не хватает прав бота на выдачу роли.", ephemeral=True)
        await interaction.response.send_message(f"{member.mention} принят(а) в команду.", ephemeral=True)

    @tool("team_kick", "Снять сотрудника с команды")
    async def team_kick(self, interaction: discord.Interaction, member: discord.Member, reason: app_commands.Range[str, 1, 300] = "не указана"):
        cfg = await self._team_gate(interaction)
        if cfg is None:
            return
        role = interaction.guild.get_role(cfg["team_role"]) if cfg["team_role"] else None
        if role is None or role not in member.roles:
            return await interaction.response.send_message("Этот человек не в команде.", ephemeral=True)
        self.kick_reasons[(interaction.guild_id, member.id)] = reason
        try:
            await member.remove_roles(role, reason=f"/team_kick — {interaction.user}: {reason}")
            if cfg["former_role"]:
                fr = interaction.guild.get_role(cfg["former_role"])
                if fr:
                    await member.add_roles(fr)
        except (discord.Forbidden, discord.HTTPException):
            return await interaction.response.send_message("Не хватает прав бота на снятие роли.", ephemeral=True)
        await interaction.response.send_message(f"{member.mention} снят(а) с команды.", ephemeral=True)

    @tool("team_warn", "Выдать предупреждение сотруднику")
    @app_commands.describe(level="Номер предупреждения", reason="Причина")
    async def team_warn(self, interaction: discord.Interaction, member: discord.Member, level: app_commands.Range[int, 1, 3],
                        reason: app_commands.Range[str, 1, 300]):
        cfg = await self._team_gate(interaction)
        if cfg is None:
            return
        roles = [interaction.guild.get_role(cfg[f"warn_role_{i}"]) if cfg[f"warn_role_{i}"] else None for i in (1, 2, 3)]
        if not roles[level - 1]:
            return await interaction.response.send_message("Роль этого предупреждения не настроена.", ephemeral=True)
        try:
            await member.remove_roles(*[r for r in roles if r and r in member.roles])
            await member.add_roles(roles[level - 1], reason=f"Предупреждение {level}/3: {reason}")
        except (discord.Forbidden, discord.HTTPException):
            return await interaction.response.send_message("Не хватает прав бота на роли.", ephemeral=True)
        await announce(interaction.guild, cfg, f"⚠️ Предупреждение {level}/3", f"{member.mention}\n**Причина:** {reason}", member, 0xFEE75C)
        await interaction.response.send_message("Предупреждение выдано.", ephemeral=True)

    async def _team_gate(self, interaction):
        cfg = modules.get_config(interaction.guild_id, "team_updates")
        if not cfg["enabled"]:
            await interaction.response.send_message("Модуль выключен.", ephemeral=True)
            return None
        if not _is_manager(interaction.user, cfg["manager_roles"]):
            await interaction.response.send_message("Только менеджеры.", ephemeral=True)
            return None
        return cfg

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles == after.roles:
            return
        cfg = modules.get_config(after.guild.id, "team_updates")
        if not cfg["enabled"] or not cfg["team_role"]:
            return
        had, has = _has_role(before, cfg["team_role"]), _has_role(after, cfg["team_role"])
        if has and not had:
            await announce(after.guild, cfg, "✅ Новый сотрудник", f"{after.mention} принят(а) в команду.", after, 0x3BA55D)
        elif had and not has:
            reason = self.kick_reasons.pop((after.guild.id, after.id), None)
            await announce(after.guild, cfg, "🚪 Сотрудник покинул команду", f"{after.mention}" + (f"\n**Причина:** {reason}" if reason else ""), after, 0xED4245)

    # ── ap: clips ───────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        cfg = modules.get_config(message.guild.id, "ap")
        if not cfg["enabled"] or not cfg["submit_channel"] or message.channel.id != cfg["submit_channel"]:
            return
        if not _has_role(message.author, cfg["ap_role"]):
            return
        clip = " ".join([a.url for a in message.attachments] + [message.content]).strip()
        if not clip:
            return
        check = message.guild.get_channel(cfg["check_channel"]) if cfg["check_channel"] else None
        if check is None:
            return
        rows = await db.run(db.query, "SELECT id FROM ap_entries WHERE guild_id=? AND user_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
                            (message.guild.id, message.author.id))
        if not rows:
            return
        e = embeds.make("Клип на проверку", f"{message.author.mention}\n{clip}"[:3000])
        try:
            sent = await check.send(embed=e, view=ApView(), allowed_mentions=discord.AllowedMentions.none())
            await message.add_reaction("📨")
        except (discord.Forbidden, discord.HTTPException):
            return
        await db.run(db.execute, "UPDATE ap_entries SET clip=?, check_msg_id=? WHERE id=?", (clip[:1500], sent.id, rows[0]["id"]))


COLOR_TEAM = 0xBE1FB6


# ── shared operations ───────────────────────────────────────────────────────

def hours_rows(guild_id: int, days: int) -> list[dict]:
    since = msk_ymd(time.time() - days * 86400)
    return [dict(r) for r in db.query(
        "SELECT user_id, SUM(minutes) AS minutes FROM support_hours WHERE guild_id=? AND ymd>=? GROUP BY user_id ORDER BY minutes DESC",
        (guild_id, since))]


def weekday_minutes(guild_id: int, days: int) -> list[int]:
    since = msk_ymd(time.time() - days * 86400)
    out = [0] * 7
    for r in db.query("SELECT ymd, SUM(minutes) AS m FROM support_hours WHERE guild_id=? AND ymd>=? GROUP BY ymd", (guild_id, since)):
        out[msk_weekday(r["ymd"])] += r["m"]
    return out


async def announce(guild, cfg, title, text, member, color):
    e = embeds.make(title, text, color=color)
    e.set_thumbnail(url=member.display_avatar.url)
    pub = guild.get_channel(cfg["announce_channel"]) if cfg["announce_channel"] else None
    if pub is not None:
        try:
            await pub.send(embed=e, content=member.mention, allowed_mentions=discord.AllowedMentions(users=[member]))
        except (discord.Forbidden, discord.HTTPException):
            pass
    log = guild.get_channel(cfg["log_channel"]) if cfg["log_channel"] else None
    if log is not None:
        try:
            await log.send(embed=e)
        except (discord.Forbidden, discord.HTTPException):
            pass


async def start_absence(guild, cfg, member, days: int, reason: str) -> str:
    now = int(time.time())
    old_nick = member.nick
    if cfg["absence_role"]:
        role = guild.get_role(cfg["absence_role"])
        if role is not None:
            try:
                await member.add_roles(role, reason="Отсутствие")
            except (discord.Forbidden, discord.HTTPException):
                pass
    if cfg["mark_nick"]:
        try:
            await member.edit(nick=f"[Отсутствует] {member.display_name}"[:32], reason="Отсутствие")
        except (discord.Forbidden, discord.HTTPException):
            pass
    aid = (await db.run(db.execute, "INSERT INTO absences (guild_id, user_id, reason, start_ts, end_ts, old_nick) VALUES (?,?,?,?,?,?)",
                        (guild.id, member.id, reason, now, now + days * 86400, old_nick))).lastrowid
    log = guild.get_channel(cfg["log_channel"]) if cfg["log_channel"] else None
    if log is not None:
        try:
            await log.send(embed=embeds.make(f"Отсутствие #{aid}", f"{member.mention} до <t:{now + days * 86400}:D>\n**Причина:** {reason}",
                                             color=0xFEE75C), allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            pass
    return f"Отсутствие #{aid} оформлено до <t:{now + days * 86400}:D>"


async def finish_absence(guild, cfg, row: dict, why: str):
    await db.run(db.execute, "UPDATE absences SET active=0 WHERE id=?", (row["id"],))
    member = guild.get_member(row["user_id"])
    if member is not None:
        if cfg["absence_role"]:
            role = guild.get_role(cfg["absence_role"])
            if role in member.roles:
                try:
                    await member.remove_roles(role, reason="Отсутствие закончилось")
                except (discord.Forbidden, discord.HTTPException):
                    pass
        if cfg["mark_nick"]:
            try:
                await member.edit(nick=row["old_nick"], reason="Отсутствие закончилось")
            except (discord.Forbidden, discord.HTTPException):
                pass
    log = guild.get_channel(cfg["log_channel"]) if cfg["log_channel"] else None
    if log is not None:
        try:
            await log.send(embed=embeds.make(f"Отсутствие #{row['id']} завершено", f"<@{row['user_id']}> — {why}", color=0x3BA55D),
                           allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            pass


# ── panel actions ───────────────────────────────────────────────────────────

async def _va_reset(ctx, p):
    await db.run(db.execute, "DELETE FROM voice_points WHERE guild_id=?", (ctx.guild.id,))
    return "Рейтинг обнулён"


VOICE_ACTIVITY.actions.append(modules.Action("reset", "Обнулить рейтинг", _va_reset, danger=True, confirm="Все баллы сотрудников будут удалены."))


async def _top10_post(ctx, p):
    cfg = modules.get_config(ctx.guild.id, "support_entries")
    ch = ctx.guild.get_channel(cfg["top_channel"]) if cfg["top_channel"] else None
    if ch is None:
        raise modules.ValidationError("Сначала выберите канал топ-10 и сохраните")
    rows = await db.run(entries_top, ctx.guild.id)
    e = embeds.make("🏆 Топ-10 поддержки", "\n".join(f"**{i}.** <@{r['team_id']}> — {r['n']}" for i, r in enumerate(rows, 1)) or "Записей пока нет.",
                    color=parse_color(cfg["color"]))
    await ch.send(embed=e, allowed_mentions=discord.AllowedMentions.none())
    return f"Топ-10 отправлен в #{ch.name}"


def entries_top(guild_id: int) -> list[dict]:
    return [dict(r) for r in db.query("SELECT team_id, COUNT(*) AS n FROM support_entries WHERE guild_id=? GROUP BY team_id ORDER BY n DESC LIMIT 10", (guild_id,))]


SUPPORT_ENTRIES.actions.append(modules.Action("post_top", "Опубликовать топ-10", _top10_post))


async def _absence_end(ctx, p):
    rows = await db.run(db.query, "SELECT * FROM absences WHERE id=? AND guild_id=? AND active=1", (int(p["id"]), ctx.guild.id))
    if not rows:
        raise modules.ValidationError("Активного отсутствия с таким номером нет")
    await finish_absence(ctx.guild, modules.get_config(ctx.guild.id, "absences"), dict(rows[0]), "завершено вручную")
    return "Отсутствие завершено"


async def _absence_add(ctx, p):
    member = ctx.guild.get_member(int(p["user"]))
    if member is None:
        raise modules.ValidationError("Участник не найден на сервере")
    return await start_absence(ctx.guild, modules.get_config(ctx.guild.id, "absences"), member, int(p["days"]), p["reason"])


ABSENCES.actions.extend([
    modules.Action("add", "Оформить отсутствие", _absence_add, params=[
        {"key": "user", "label": "Сотрудник", "type": "user"}, {"key": "days", "label": "Дней", "type": "number", "min": 1, "max": 365},
        {"key": "reason", "label": "Причина", "type": "text"}]),
    modules.Action("end", "Завершить отсутствие", _absence_end, params=[{"key": "id", "label": "Номер", "type": "number", "min": 1}]),
])


async def _ap_add(ctx, p):
    cfg = modules.get_config(ctx.guild.id, "ap")
    member = ctx.guild.get_member(int(p["user"]))
    if member is None:
        raise modules.ValidationError("Участник не найден на сервере")
    if not cfg["ap_role"] or ctx.guild.get_role(cfg["ap_role"]) is None:
        raise modules.ValidationError("Сначала выберите роль AP и сохраните")
    await member.add_roles(ctx.guild.get_role(cfg["ap_role"]), reason="Обязательная запись")
    await db.run(db.execute, "INSERT INTO ap_entries (guild_id, user_id, moderator_id, ts) VALUES (?,?,?,?)",
                 (ctx.guild.id, member.id, ctx.user_id, int(time.time())))
    sub = ctx.guild.get_channel(cfg["submit_channel"]) if cfg["submit_channel"] else None
    try:
        await member.send(cfg["dm_assigned"].replace("{channel}", f"#{sub.name}" if sub else "отправки")[:2000])
    except (discord.Forbidden, discord.HTTPException):
        pass
    return f"{member.display_name}: запись назначена"


async def _ap_clear(ctx, p):
    cfg = modules.get_config(ctx.guild.id, "ap")
    cur = await db.run(db.execute, "UPDATE ap_entries SET status='closed' WHERE id=? AND guild_id=?", (int(p["id"]), ctx.guild.id))
    if not cur.rowcount:
        raise modules.ValidationError("Записи с таким номером нет")
    row = (await db.run(db.query, "SELECT user_id FROM ap_entries WHERE id=?", (int(p["id"]),)))[0]
    member = ctx.guild.get_member(row["user_id"])
    role = ctx.guild.get_role(cfg["ap_role"]) if cfg["ap_role"] else None
    if member and role and role in member.roles:
        await member.remove_roles(role, reason="AP закрыта")
    return "Запись закрыта"


AP.actions.extend([
    modules.Action("add", "Назначить запись", _ap_add, params=[{"key": "user", "label": "Участник", "type": "user"}]),
    modules.Action("close", "Закрыть запись", _ap_clear, params=[{"key": "id", "label": "Номер записи", "type": "number", "min": 1}]),
])


async def _frak_log(ctx, key, title, text):
    cfg = modules.get_config(ctx.guild.id, "frak")
    for k in (key, "log_channel"):
        ch = ctx.guild.get_channel(cfg[k]) if cfg[k] else None
        if ch is not None:
            try:
                await ch.send(embed=embeds.make(title, text, color=parse_color(cfg["color"])), allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException):
                pass
            return


async def _frak_create(ctx, p):
    leader = int(p["leader"]) if p.get("leader") else None
    fid = (await db.run(db.execute, "INSERT INTO factions (guild_id, name, leader_id, role_id, ts) VALUES (?,?,?,?,?)",
                        (ctx.guild.id, p["name"], leader, p.get("role"), int(time.time())))).lastrowid
    await _frak_log(ctx, "create_channel", "Фракция создана", f"**{p['name']}** (#{fid})" + (f"\nЛидер: <@{leader}>" if leader else ""))
    return f"Фракция #{fid} создана"


async def _frak_delete(ctx, p):
    rows = await db.run(db.query, "SELECT * FROM factions WHERE id=? AND guild_id=?", (int(p["id"]), ctx.guild.id))
    if not rows:
        raise modules.ValidationError("Фракции с таким номером нет")
    await db.run(db.execute, "DELETE FROM factions WHERE id=?", (rows[0]["id"],))
    await _frak_log(ctx, "delete_channel", "Фракция удалена", f"**{rows[0]['name']}** (#{rows[0]['id']})")
    return "Фракция удалена"


async def _frak_warn(ctx, p):
    rows = await db.run(db.query, "SELECT * FROM factions WHERE id=? AND guild_id=?", (int(p["id"]), ctx.guild.id))
    if not rows:
        raise modules.ValidationError("Фракции с таким номером нет")
    await db.run(db.execute, "UPDATE factions SET warns=warns+1 WHERE id=?", (rows[0]["id"],))
    await _frak_log(ctx, "warn_channel", "Предупреждение фракции", f"**{rows[0]['name']}**: {p['reason']}")
    return "Предупреждение выдано"


async def _frak_overview(ctx, p):
    cfg = modules.get_config(ctx.guild.id, "frak")
    ch = ctx.guild.get_channel(cfg["overview_channel"]) if cfg["overview_channel"] else None
    if ch is None:
        raise modules.ValidationError("Сначала выберите канал обзора и сохраните")
    rows = await db.run(db.query, "SELECT * FROM factions WHERE guild_id=? ORDER BY name", (ctx.guild.id,))
    lines = [f"**{r['name']}**" + (f" · лидер <@{r['leader_id']}>" if r["leader_id"] else "") + (f" · ⚠️ {r['warns']}" if r["warns"] else "") for r in rows]
    e = embeds.make("Фракции", "\n".join(lines) or "Фракций пока нет.", color=parse_color(cfg["color"]))
    if (cfg["banner"] or "").startswith("https://"):
        e.set_image(url=cfg["banner"])
    await ch.send(embed=e, allowed_mentions=discord.AllowedMentions.none())
    return f"Обзор отправлен в #{ch.name}"


FRAK.actions.extend([
    modules.Action("create", "Создать фракцию", _frak_create, params=[
        {"key": "name", "label": "Название", "type": "text"}, {"key": "leader", "label": "Лидер", "type": "user", "required": False},
        {"key": "role", "label": "Роль фракции", "type": "role", "required": False}]),
    modules.Action("warn", "Выдать предупреждение", _frak_warn, params=[
        {"key": "id", "label": "Номер фракции", "type": "number", "min": 1}, {"key": "reason", "label": "Причина", "type": "text"}]),
    modules.Action("delete", "Удалить фракцию", _frak_delete, danger=True, params=[{"key": "id", "label": "Номер фракции", "type": "number", "min": 1}]),
    modules.Action("overview", "Опубликовать обзор", _frak_overview),
])


# ── tables ──────────────────────────────────────────────────────────────────

def _name(guild, uid):
    m = guild.get_member(uid)
    return m.display_name if m else str(uid)


modules.register_table("support_hours", "hours", lambda g: [
    {"user": _name(g, r["user_id"]), "hours": round(r["minutes"] / 60, 1)}
    for r in hours_rows(g.id, modules.get_config(g.id, "support_hours")["days"])])
modules.register_table("voice_activity", "top", lambda g: [
    {"user": _name(g, r["user_id"]), "points": r["points"], "convs": r["convs"]}
    for r in db.query("SELECT * FROM voice_points WHERE guild_id=? ORDER BY points DESC LIMIT 20", (g.id,))])
modules.register_table("support_entries", "top", lambda g: [{"user": _name(g, r["team_id"]), "count": r["n"]} for r in entries_top(g.id)])
modules.register_table("absences", "active", lambda g: [
    {"id": r["id"], "user": _name(g, r["user_id"]), "reason": r["reason"], "end_ts": r["end_ts"]}
    for r in db.query("SELECT * FROM absences WHERE guild_id=? AND active=1 ORDER BY end_ts", (g.id,))])
modules.register_table("ap", "entries", lambda g: [
    {"id": r["id"], "user": _name(g, r["user_id"]), "status": {"pending": "ждёт клип", "approved": "принято", "closed": "закрыто"}.get(r["status"], r["status"]),
     "ts": r["ts"]} for r in db.query("SELECT * FROM ap_entries WHERE guild_id=? ORDER BY id DESC LIMIT 100", (g.id,))])
modules.register_table("frak", "list", lambda g: [
    {"id": r["id"], "name": r["name"], "leader": _name(g, r["leader_id"]) if r["leader_id"] else "—", "warns": r["warns"], "ts": r["ts"]}
    for r in db.query("SELECT * FROM factions WHERE guild_id=? ORDER BY name", (g.id,))])


def _queue_table(guild):
    cog = _BOT.get_cog("SupportTeam") if _BOT else None
    now = time.time()
    return [{"pos": i, "user": _name(guild, e["user_id"]), "vip": "да" if e["vip"] else "", "wait": human_wait(now - e["joined"])}
            for i, e in enumerate(cog.queue_entries(guild) if cog else [], 1)]


modules.register_table("support_sort", "queue", _queue_table)


_BOT = None


async def setup(bot):
    global _BOT
    _BOT = bot
    await bot.add_cog(SupportTeam(bot))
