import discord
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from discord import app_commands, ui
from discord.ext import commands, tasks
import os
import io
import json
import asyncio
import re
import aiohttp
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))

def now_msk() -> datetime:
    """Текущее время по МСК."""
    return datetime.now(MSK).replace(tzinfo=None)

load_dotenv()

# ─────────────────────────────────────────────
# JSON-файлы хранилища
# ─────────────────────────────────────────────
DATA_FILE     = "data.json"
OBSHAK_FILE   = "obshak.json"
POINTS_FILE   = "points.json"
CHIPS_FILE    = "chips.json"
ROULETTE_FILE = "roulette.json"

# ─────────────────────────────────────────────
# КОНФИГ — вставь свои URL и тексты
# ─────────────────────────────────────────────

# Гифка при одобрении заявки (в ЛС)
APPROVE_GIF_URL = "https://media0.giphy.com/media/v1.Y2lkPTc5MGI3NjExZ3VyczN2em04d3JxNTB1eWlvaWJnczl4dTdpeTZjY2g2MTFwN3NveiZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/3ndAvMC5LFPNMCzq7m/giphy.gif"

# Фото в АФК-панели (embed)
AFK_IMAGE_URL = "https://i.imgur.com/ZxGcZjw.gif"

FOOTER_ICON = "https://i.imgur.com/KPfU7sB.png"

def _footer(gid: int) -> str:
    return (guild_branding.get(gid) or {}).get("footer_icon") or FOOTER_ICON

def _approve_gif(gid: int) -> str:
    return (guild_branding.get(gid) or {}).get("approve_gif") or APPROVE_GIF_URL

def _afk_img(gid: int) -> str:
    return (guild_branding.get(gid) or {}).get("afk_image") or AFK_IMAGE_URL

DEFAULT_TICKET_TITLE = "📋 Вступление в MORIARTY"
DEFAULT_TICKET_DESC  = (
    "**От нас вы получите :**\n"
    "• Дружный коллектив и весёлое общение, где ты найдешь новых друзей и общие интересы\n"
    "• Поддержка, защита и помощь от семьи\n"
    "• Возможность выполнения любых контрактов\n"
    "• Блат во фракциях, где состоят члены семьи\n"
    "• Семейный особняк и автопарк\n"
    "• Свои лидерские сроки в разных фракциях\n\n"
    "**От вас мы ожидаем :**\n"
    "• Готовность сменить фамилию\n"
    "• Желание играть и развиваться\n"
    "• Возраст 16+ (возможны исключения)\n"
    "• Наличие 5-ого игрового уровня"
)
DEFAULT_TICKET_IMAGE = "https://i.imgur.com/umswh4i.gif"

# ─────────────────────────────────────────────
# ХРАНИЛИЩЕ
# ─────────────────────────────────────────────


# { message_id: { "title": str, "max": int, "slots": {slot_num: user_id|None},
#                 "image_url": str|None, "note": str|None, "channel_id": int,
#                 "thread_id": int|None, "thread_msg_id": int|None } }
event_lists: dict = {}

# { guild_id: { "message_id": int, "channel_id": int } }
afk_panels: dict = {}

# 📣 РОЛЬ ДЛЯ ТЕГА В РЕАКИ
# { guild_id: int (role_id) }
event_roles: dict = {}

# { guild_id: { user_id: { "reason": str, "return_time": str, "since": datetime } } }
afk_list: dict = {}

# { guild_id: { "message_id": int, "channel_id": int } }
inactive_panels: dict = {}

# { guild_id: { user_id: { "reason": str, "return_date": str, "since": datetime } } }
inactive_list: dict = {}

# 💰 БАЛЛЫ И ШТРАФЫ
# { guild_id: { user_id: int } }
points_db: dict = {}

# 🎰 ФИШКИ (только для рулетки)
# { guild_id: { user_id: int } }
chips_db: dict = {}

# { guild_id: { user_id: { "warns": int, "reason": str, "moderator": int } } }
warns_db: dict = {}

# { guild_id: { 1: role_id, 2: role_id, 3: role_id } }
warn_roles: dict = {}

# 🛒 ПАНЕЛЬ МАГАЗИНА
# { guild_id: { "channel_id": int, "message_id": int } }
shop_panels: dict = {}

# 🛒 ЛОГИ МАГАЗИНА { guild_id: channel_id }
shop_log_channels: dict = {}

# 🛒 РОЛЬ ВЫДАЧИ ТОВАРОВ { guild_id: role_id }
shop_manager_roles: dict = {}

# 🎫 ПАНЕЛЬ ТИКЕТОВ
# { guild_id: { "panel_channel_id": int, "review_channel_id": int, "message_id": int } }
ticket_panels: dict = {}

# 🎫 ТЕКСТ ПАНЕЛИ ТИКЕТОВ { guild_id: { "title": str, "desc": str, "image": str } }
ticket_texts: dict = {}

# 🎫 СЧЁТЧИК ТИКЕТОВ { guild_id: int }
ticket_counters: dict = {}

# 📋 КАНАЛ ЛОГОВ ОТКАЗОВ { guild_id: channel_id }
reject_log_channels: dict = {}

# 🎨 БРЕНДИНГ { guild_id: { "footer_icon": str, "approve_gif": str, "afk_image": str } }
guild_branding: dict = {}

# 🎫 РОЛЬ ТИКЕТ-МЕНЕДЖЕРА (может одобрять/отклонять) { guild_id: role_id }
ticket_manager_roles: dict = {}

# 🎫 РОЛИ С ДОСТУПОМ К ТИКЕТУ (видят канал) { guild_id: [role_id, ...] }
ticket_viewer_roles: dict = {}

# 🎫 РОЛЬ ДЛЯ ТЕГА В ТИКЕТЕ { guild_id: role_id }
ticket_ping_role: dict = {}

# 🏎 РОЛЬ МП { guild_id: role_id }
mp_roles: dict = {}

# 🔫 РОЛЬ ВЗП { guild_id: role_id }
vzp_roles: dict = {}

# 🔫 РОЛЬ ВЗП-2 { guild_id: role_id }
vzp_roles2: dict = {}

# 🏎 РОЛЬ МП-2 { guild_id: role_id }
mp_roles2: dict = {}

# 📣 РОЛЬ ДЛЯ ТЕГА В РЕАКИ-2 { guild_id: role_id }
list_roles2: dict = {}

# 🎯 РОЛИ ДОСТУПА К КОМАНДАМ СБОРОВ { guild_id: { "vzp": [role_id,...], "mp": [...], "list": [...] } }
event_command_roles: dict = {}

# 🔒 ПРИВАТНЫЕ КОМНАТЫ
# { guild_id: { "create_channel_id": int, "category_id": int, "panel_channel_id": int } }
private_vc_settings: dict = {}

# { vc_channel_id: { "owner_id": int, "guild_id": int, "panel_msg_id": int|None, "panel_channel_id": int|None } }
private_vcs: dict = {}

# 🛒 ТОВАРЫ МАГАЗИНА per-guild
# { guild_id: { item_id: { "name": str, "price": int, "emoji": str, "description": str,
#               "action": "remove_warn"|"give_role"|"notify", "role_id": int|None } } }
guild_shop_items: dict = {}

# 🔑 РОЛЬ АДМИНИСТРАТОРА { guild_id: role_id }
admin_roles: dict = {}

# 🔑 ДОПОЛНИТЕЛЬНЫЕ РОЛИ АДМИНИСТРАТОРА { guild_id: [role_id, ...] }
extra_admin_roles: dict = {}

# 📄 КОНТРАКТЫ
# { guild_id: { "channel_id": int, "message_id": int, "text": str, "image_url": str|None } }
contract_settings: dict = {}

# { guild_id: role_id }
contract_roles: dict = {}

# { message_id: { "guild_id": int, "creator_id": int, "duration": str, "start": str,
#                 "channel_id": int, "participants": [user_id, ...] } }
active_contracts: dict = {}

# 📝 ФИДБЕКИ
# { guild_id: { "panel_channel_id": int, "panel_message_id": int,
#               "log_channel_id": int|None, "ping_role_id": int|None,
#               "text": str, "image_url": str|None } }
feedback_settings: dict = {}

# 🎙 НАЧИСЛЕНИЕ ЗА ГОЛОСОВЫЕ КАНАЛЫ
# { guild_id: { "categories": [cat_id, ...], "excluded_channels": [ch_id, ...], "amount": int } }
voice_reward_settings: dict = {}

# 🪪 ЛИЧНЫЙ КАБИНЕТ
# { guild_id: { "channel_id": int, "message_id": int, "text": str|None, "image_url": str|None } }
cabinet_panels: dict = {}

# { guild_id: str }  — пригласительная ссылка
cabinet_invite_links: dict = {}

# 📊 СТАТИСТИКА УЧАСТНИКОВ
# { guild_id: { user_id: int } }
message_counts: dict = {}

# { guild_id: { user_id: int } }  — накопленные минуты в войсе
voice_minutes: dict = {}

# { guild_id: { user_id: datetime } }  — время входа в канал (in-memory, не сохраняется)
voice_join_times: dict = {}

# ⚔️ ВЗП МОНИТОРИНГ
# { guild_id: { "familyId", "familyName", "serverId", "alertChannelId",
#               "resultsChannelId", "mentionRoles", "mentionUsers",
#               "pollInterval", "monitoringEnabled" } }
vzp_monitor_config: dict = {}

# { guild_id: { event_id: "notified" | "completed" } }
vzp_processed_events: dict = {}

# { guild_id: float } — timestamp последней проверки
vzp_last_check: dict = {}

# 💰 ОБЩАК
# { guild_id: { "channel_id": int, "message_id": int, "text": str|None, "image_url": str|None } }
obshak_panels: dict = {}

# { guild_id: channel_id }
obshak_log_channels: dict = {}

# { guild_id: role_id }  — роль для тега в логах общака
obshak_ping_roles: dict = {}

# { guild_id: channel_id } — голосовой канал для обзвонов
interview_channels: dict = {}

# { ticket_text_channel_id: voice_channel_id } — войс для обзвона по заявке
ticket_voice_channels: dict = {}

# { guild_id: [ { "user_id": int, "amount": int, "date": str (ISO) } ] }
obshak_deposits: dict = {}

# 🎰 ПОДКРУТКА КАЗИНО { guild_id: { role_id: float } }
# float — множитель шанса победы: 1.0 = честно, <1 = хуже, >1 = лучше
casino_role_luck: dict = {}

# ─────────────────────────────────────────────
# ФАЙЛЫ ОТДЕЛЬНЫХ БАЗ ДАННЫХ
# ─────────────────────────────────────────────


# ─────────────────────────────────────────────
# 📋 СОСТАВ СЕМЬИ
# ─────────────────────────────────────────────
# { guild_id: { "member_role_id": int, "academy_role_id": int,
#               "channel_id": int, "message_id": int } }
roster_settings: dict = {}


# ─────────────────────────────────────────────
# ПРОВЕРКИ ПРАВ
# ─────────────────────────────────────────────
def is_admin(interaction: discord.Interaction) -> bool:
    """Для slash-команд. Если роль не настроена — требует Discord-администратора."""
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    admin_role_id = admin_roles.get(interaction.guild_id)
    extra_ids = extra_admin_roles.get(interaction.guild_id, [])
    if admin_role_id or extra_ids:
        return any(role.id == admin_role_id or role.id in extra_ids for role in member.roles)
    return member.guild_permissions.administrator

def is_admin_ctx(ctx) -> bool:
    """Для prefix-команд. Если роль не настроена — требует Discord-администратора."""
    admin_role_id = admin_roles.get(ctx.guild.id)
    extra_ids = extra_admin_roles.get(ctx.guild.id, [])
    if admin_role_id or extra_ids:
        return any(r.id == admin_role_id or r.id in extra_ids for r in ctx.author.roles)
    return ctx.author.guild_permissions.administrator

def can_run_event(ctx, event_type: str) -> bool:
    """Проверка доступа к командам сборов (!vzp, !mp, !list). Админ всегда может."""
    if is_admin_ctx(ctx):
        return True
    allowed = event_command_roles.get(ctx.guild.id, {}).get(event_type, [])
    return any(r.id in allowed for r in ctx.author.roles)

def can_manage_event_message(interaction: discord.Interaction, data: dict) -> bool:
    """Проверка доступа к управлению уже созданным сбором (например удаление фото)."""
    if is_admin(interaction):
        return True
    event_type = data.get("cmd")
    if not event_type:
        return False
    allowed = event_command_roles.get(interaction.guild_id, {}).get(event_type, [])
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    return any(r.id in allowed for r in member.roles)

def is_ticket_manager(interaction: discord.Interaction) -> bool:
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    if is_admin(interaction):
        return True
    tm_role_id = ticket_manager_roles.get(interaction.guild_id)
    return tm_role_id is not None and any(role.id == tm_role_id for role in member.roles)
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Юр-ассистент Murrieta (модуль laws_module.py)
try:
    from laws_module import setup_laws
    setup_laws(bot)
except Exception as _laws_err:
    print(f"[laws_module] не подключён: {_laws_err}")


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def declension(n: int) -> str:
    mod10, mod100 = n % 10, n % 100
    if 11 <= mod100 <= 19:
        return "человек"
    if mod10 == 1:
        return "человек"
    if 2 <= mod10 <= 4:
        return "человека"
    return "человек"


def format_amount(amount: int) -> str:
    """50000 → 50.000 (русский формат)"""
    return f"{amount:,}".replace(",", ".")



def _format_reserve_block(reserve: list | None) -> str:
    reserve = reserve or []
    if not reserve:
        return f"\n\n**🪑 Резерв (0):**\n*пусто*"
    lines = [f"`R{str(i).zfill(2)}.` <@{uid}>" for i, uid in enumerate(reserve, 1)]
    return f"\n\n**🪑 Резерв ({len(reserve)}):**\n" + "\n".join(lines)


def build_event_embed(
    guild_id: int,
    title: str,
    max_count: int,
    slots: dict,
    image_url: str = None,
    note: str = None,
    join_mode: bool = False,
    event_time: str = None,
    closed: bool = False,
    reserve: list | None = None,
) -> discord.Embed:
    filled = sum(1 for v in slots.values() if v is not None)
    if closed:
        color = discord.Color.from_rgb(153, 170, 181)
    else:
        color = discord.Color.red() if filled >= max_count else discord.Color.green()

    lines = []
    for i in range(1, max_count + 1):
        uid = slots.get(i)
        lines.append(f"`{str(i).zfill(2)}.` {'<@' + str(uid) + '>' if uid else '*свободно*'}")

    text = "\n".join(lines)
    prefix = ""
    if event_time:
        prefix += f"🕐 **Время:** `{event_time}`\n"
    if closed:
        prefix += "🔒 **СПИСОК ЗАКРЫТ**\n"
    if prefix:
        prefix += "\n"
    if join_mode:
        description = f"{prefix}Нажми ✅ чтобы записаться · 🪑 резерв если места заняты\n\n**Участники ({filled}/{max_count}):**\n{text}"
    else:
        description = f"{prefix}Нажми кнопку слота · 🪑 **Резерв** — запасной список\n\n**Слоты ({filled}/{max_count}):**\n{text}"
    description += _format_reserve_block(reserve)
    if note:
        description += f"\n\n📌 **Заметка:** {note}"

    embed = discord.Embed(
        title=f"📋 Сбор: {title}",
        description=description,
        color=color,
        timestamp=datetime.now(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
    if image_url:
        embed.set_image(url=image_url)
    return embed


def build_thread_list(title: str, max_count: int, slots: dict, reserve: list | None = None) -> str:
    filled = sum(1 for v in slots.values() if v is not None)
    lines  = [f"**📋 Список: {title} ({filled}/{max_count})**\n"]
    for i in range(1, max_count + 1):
        uid = slots.get(i)
        lines.append(f"`{str(i).zfill(2)}.` {'<@' + str(uid) + '>' if uid else 'свободно'}")
    reserve = reserve or []
    lines.append(f"\n**🪑 Резерв ({len(reserve)}):**")
    if reserve:
        for i, uid in enumerate(reserve, 1):
            lines.append(f"`R{str(i).zfill(2)}.` <@{uid}>")
    else:
        lines.append("*пусто*")
    return "\n".join(lines)



async def update_thread_list(message_id: int):
    data = event_lists.get(message_id)
    if not data or not data.get("thread_msg_id"):
        return
    try:
        thread = bot.get_channel(data["thread_id"])
        if not thread:
            return
        msg = await thread.fetch_message(data["thread_msg_id"])
        await msg.edit(
            content=build_thread_list(data["title"], data["max"], data["slots"], data.get("reserve", [])),
            view=ThreadListView(message_id),
        )
    except Exception:
        pass


def build_inactive_embed(guild_id: int) -> discord.Embed:
    entries = list(inactive_list.get(guild_id, {}).items())
    count   = len(entries)

    if entries:
        lines = "\n\n".join(
            f"**{i+1})** <@{uid}> Причина: {d['reason']}\nВернусь: `{d['return_date']}`"
            for i, (uid, d) in enumerate(entries)
        )
    else:
        lines = "*Список пуст — никто не в инактиве*"

    embed = discord.Embed(
        title="📅 Люди, находящиеся в инактиве:",
        description=f"• Всего в инактиве **{count}** {declension(count)}\n\n{lines}",
        color=discord.Color.orange(),
        timestamp=datetime.now(),
    )
    if _afk_img(guild_id):
        embed.set_image(url=_afk_img(guild_id))
    embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
    return embed


def build_afk_embed(guild_id: int) -> discord.Embed:
    entries = list(afk_list.get(guild_id, {}).items())
    count   = len(entries)

    if entries:
        lines = "\n\n".join(
            f"**{i+1})** <@{uid}> Причина: {d['reason']}\nВернусь в: `{d['return_time']}`"
            for i, (uid, d) in enumerate(entries)
        )
    else:
        lines = "*Список пуст — никто не в АФК*"

    embed = discord.Embed(
        title="⏳ Люди, находящиеся в АФК:",
        description=f"• Всего в АФК **{count}** {declension(count)}\n\n{lines}",
        color=discord.Color.blurple(),
        timestamp=datetime.now(),
    )
    if _afk_img(guild_id):
        embed.set_image(url=_afk_img(guild_id))
    embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
    return embed


def get_points(guild_id: int, user_id: int) -> int:
    return points_db.get(guild_id, {}).get(user_id, 0)


def set_points(guild_id: int, user_id: int, amount: int):
    if guild_id not in points_db:
        points_db[guild_id] = {}
    points_db[guild_id][user_id] = amount
    save_data()
    save_points()


def add_points(guild_id: int, user_id: int, amount: int):
    current = get_points(guild_id, user_id)
    set_points(guild_id, user_id, current + amount)


def get_chips(guild_id: int, user_id: int) -> int:
    return chips_db.get(guild_id, {}).get(user_id, 0)


def set_chips(guild_id: int, user_id: int, amount: int):
    if guild_id not in chips_db:
        chips_db[guild_id] = {}
    chips_db[guild_id][user_id] = max(0, amount)
    save_chips()


def add_chips(guild_id: int, user_id: int, amount: int):
    set_chips(guild_id, user_id, get_chips(guild_id, user_id) + amount)


def get_warns(guild_id: int, user_id: int) -> dict:
    return warns_db.get(guild_id, {}).get(user_id, None)


def set_warn(guild_id: int, user_id: int, count: int, reason: str, moderator_id: int):
    if guild_id not in warns_db:
        warns_db[guild_id] = {}
    warns_db[guild_id][user_id] = {
        "warns": count,
        "reason": reason,
        "moderator": moderator_id,
        "timestamp": datetime.now(),
    }
    save_data()


def remove_warn(guild_id: int, user_id: int) -> bool:
    if guild_id in warns_db and user_id in warns_db[guild_id]:
        del warns_db[guild_id][user_id]
        save_data()
        return True
    return False


def build_points_embed(guild_id: int, user_id: int) -> discord.Embed:
    points = get_points(guild_id, user_id)
    chips  = get_chips(guild_id, user_id)
    warn_data = get_warns(guild_id, user_id)
    warns = warn_data["warns"] if warn_data else 0

    embed = discord.Embed(
        title="💰 Ваш баланс",
        color=discord.Color.gold(),
        timestamp=datetime.now(),
    )
    embed.add_field(name="Алмазы", value=f"**{points}** 💎", inline=True)
    embed.add_field(name="Фишки", value=f"**{chips}** 🎰", inline=True)
    embed.add_field(name="Warns", value=f"**{warns}** ⚠️", inline=True)
    embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
    return embed


def build_shop_embed(guild_id: int) -> discord.Embed:
    items = guild_shop_items.get(guild_id, {})
    embed = discord.Embed(
        title="🛒 Магазин",
        description=(
            "Трать заработанные баллы на полезные товары.\n"
            "Свой баланс смотри командой `!баланс`\n\u200B"
        ),
        color=discord.Color.gold(),
        timestamp=datetime.now(),
    )
    if not items:
        embed.add_field(name="Пусто", value="*Товары ещё не добавлены*", inline=False)
    for item_id, item in items.items():
        action_label = {"remove_warn": "Снимает варн", "give_role": "Выдаёт роль", "notify": "Ручная выдача"}.get(item["action"], "")
        embed.add_field(
            name=f"{item['emoji']} {item['name']}",
            value=f"Цена: **{item['price']}** 💎\n{item.get('description', '')}\n*{action_label}*",
            inline=True,
        )
    embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
    return embed


def save_data():
    """Сохраняет все данные в data.json."""
    try:
        warns_serial = {}
        for g, users in warns_db.items():
            warns_serial[str(g)] = {}
            for u, info in users.items():
                warns_serial[str(g)][str(u)] = {
                    k: (v.isoformat() if isinstance(v, datetime) else v)
                    for k, v in info.items()
                }

        afk_list_serial = {}
        for g, users in afk_list.items():
            afk_list_serial[str(g)] = {}
            for u, info in users.items():
                afk_list_serial[str(g)][str(u)] = {
                    k: (v.isoformat() if isinstance(v, datetime) else v)
                    for k, v in info.items()
                }

        inactive_list_serial = {}
        for g, users in inactive_list.items():
            inactive_list_serial[str(g)] = {}
            for u, info in users.items():
                inactive_list_serial[str(g)][str(u)] = {
                    k: (v.isoformat() if isinstance(v, datetime) else v)
                    for k, v in info.items()
                }

        event_lists_serial = {}
        for mid, ev in event_lists.items():
            event_lists_serial[str(mid)] = {
                **{k: v for k, v in ev.items() if k != "slots"},
                "slots": {str(s): uid for s, uid in ev.get("slots", {}).items()},
            }

        data = {
            "_id": "main",
            "warns":                warns_serial,
            "event_roles":          {str(g): v for g, v in event_roles.items()},
            "ticket_panels":        {str(g): v for g, v in ticket_panels.items()},
            "ticket_counters":      {str(g): v for g, v in ticket_counters.items()},
            "reject_log_channels":  {str(g): v for g, v in reject_log_channels.items()},
            "guild_branding":       {str(g): v for g, v in guild_branding.items()},
            "ticket_manager_roles": {str(g): v for g, v in ticket_manager_roles.items()},
            "mp_roles":             {str(g): v for g, v in mp_roles.items()},
            "vzp_roles":            {str(g): v for g, v in vzp_roles.items()},
            "warn_roles":           {str(g): {str(k): v for k, v in wr.items()} for g, wr in warn_roles.items()},
            "admin_roles":          {str(g): v for g, v in admin_roles.items()},
            "extra_admin_roles":    {str(g): v for g, v in extra_admin_roles.items()},
            "ticket_viewer_roles":  {str(g): v for g, v in ticket_viewer_roles.items()},
            "ticket_ping_role":     {str(g): v for g, v in ticket_ping_role.items()},
            "guild_shop_items":     {str(g): v for g, v in guild_shop_items.items()},
            "event_command_roles":  {str(g): v for g, v in event_command_roles.items()},
            "private_vc_settings":  {str(g): v for g, v in private_vc_settings.items()},
            "ticket_texts":         {str(g): v for g, v in ticket_texts.items()},
            "afk_list":             afk_list_serial,
            "afk_panels":           {str(g): v for g, v in afk_panels.items()},
            "inactive_list":        inactive_list_serial,
            "inactive_panels":      {str(g): v for g, v in inactive_panels.items()},
            "event_lists":          event_lists_serial,
            "shop_panels":          {str(g): v for g, v in shop_panels.items()},
            "shop_log_channels":    {str(g): v for g, v in shop_log_channels.items()},
            "shop_manager_roles":   {str(g): v for g, v in shop_manager_roles.items()},
            "contract_settings":    {str(g): v for g, v in contract_settings.items()},
            "contract_roles":       {str(g): v for g, v in contract_roles.items()},
            "active_contracts":     {str(mid): v for mid, v in active_contracts.items()},
            "feedback_settings":    {str(g): v for g, v in feedback_settings.items()},
            "obshak_panels":        {str(g): v for g, v in obshak_panels.items()},
            "obshak_log_channels":  {str(g): v for g, v in obshak_log_channels.items()},
            "obshak_ping_roles":    {str(g): v for g, v in obshak_ping_roles.items()},
            "roster_settings":      {str(g): v for g, v in roster_settings.items()},
            "voice_reward_settings":{str(g): v for g, v in voice_reward_settings.items()},
            "cabinet_panels":       {str(g): v for g, v in cabinet_panels.items()},
            "cabinet_invite_links": {str(g): v for g, v in cabinet_invite_links.items()},
            "message_counts":       {str(g): {str(u): v for u, v in us.items()} for g, us in message_counts.items()},
            "voice_minutes":        {str(g): {str(u): v for u, v in us.items()} for g, us in voice_minutes.items()},
            "vzp_roles2":           {str(g): v for g, v in vzp_roles2.items()},
            "mp_roles2":            {str(g): v for g, v in mp_roles2.items()},
            "list_roles2":          {str(g): v for g, v in list_roles2.items()},
            "ticket_voice_channels": {str(c): v for c, v in ticket_voice_channels.items()},
            "interview_channels":       {str(g): v for g, v in interview_channels.items()},
            "vzp_monitor_config":   {str(g): v for g, v in vzp_monitor_config.items()},
            "vzp_processed_events": {str(g): v for g, v in vzp_processed_events.items()},
            "casino_role_luck":     {str(g): {str(r): v for r, v in roles.items()} for g, roles in casino_role_luck.items()},
        }
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"WARNING: Failed to save data: {e}")


def load_data():
    """Загружает данные из data.json при старте."""
    global points_db, warns_db
    try:
        if not os.path.exists(DATA_FILE):
            print("OK: data.json not found, starting fresh")
            return
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        for g, users in data.get("warns", {}).items():
            warns_db[int(g)] = {}
            for u, info in users.items():
                warns_db[int(g)][int(u)] = {
                    k: (datetime.fromisoformat(v) if k == "timestamp" and v else v)
                    for k, v in info.items()
                }
        for g, v in data.get("event_roles", {}).items():
            event_roles[int(g)] = v
        for g, v in data.get("ticket_panels", {}).items():
            ticket_panels[int(g)] = v
        for g, v in data.get("ticket_counters", {}).items():
            ticket_counters[int(g)] = v
        for g, v in data.get("reject_log_channels", {}).items():
            reject_log_channels[int(g)] = v
        for g, v in data.get("guild_branding", {}).items():
            guild_branding[int(g)] = v
        for g, v in data.get("ticket_manager_roles", {}).items():
            ticket_manager_roles[int(g)] = v
        for g, v in data.get("mp_roles", {}).items():
            mp_roles[int(g)] = v
        for g, v in data.get("vzp_roles", {}).items():
            vzp_roles[int(g)] = v
        for g, wr in data.get("warn_roles", {}).items():
            warn_roles[int(g)] = {int(k): v for k, v in wr.items()}
        for g, v in data.get("admin_roles", {}).items():
            admin_roles[int(g)] = v
        for g, v in data.get("extra_admin_roles", {}).items():
            extra_admin_roles[int(g)] = v
        for g, v in data.get("ticket_viewer_roles", {}).items():
            ticket_viewer_roles[int(g)] = v
        for g, v in data.get("ticket_ping_role", {}).items():
            ticket_ping_role[int(g)] = v
        for g, v in data.get("guild_shop_items", {}).items():
            guild_shop_items[int(g)] = v
        for g, v in data.get("event_command_roles", {}).items():
            event_command_roles[int(g)] = v
        for g, v in data.get("private_vc_settings", {}).items():
            private_vc_settings[int(g)] = v
        for g, v in data.get("ticket_texts", {}).items():
            ticket_texts[int(g)] = v
        for g, users in data.get("afk_list", {}).items():
            afk_list[int(g)] = {}
            for u, info in users.items():
                afk_list[int(g)][int(u)] = {
                    k: (datetime.fromisoformat(v) if k == "since" and v else v)
                    for k, v in info.items()
                }
        for g, v in data.get("afk_panels", {}).items():
            afk_panels[int(g)] = v
        for g, users in data.get("inactive_list", {}).items():
            inactive_list[int(g)] = {}
            for u, info in users.items():
                inactive_list[int(g)][int(u)] = {
                    k: (datetime.fromisoformat(v) if k == "since" and v else v)
                    for k, v in info.items()
                }
        for g, v in data.get("inactive_panels", {}).items():
            inactive_panels[int(g)] = v
        for mid, ev in data.get("event_lists", {}).items():
            event_lists[int(mid)] = {
                **{k: v for k, v in ev.items() if k not in ("slots", "reserve")},
                "slots": {int(s): uid for s, uid in ev.get("slots", {}).items()},
                "reserve": [int(u) for u in ev.get("reserve", [])],
            }
        for g, v in data.get("shop_panels", {}).items():
            shop_panels[int(g)] = v
        for g, v in data.get("shop_log_channels", {}).items():
            shop_log_channels[int(g)] = v
        for g, v in data.get("shop_manager_roles", {}).items():
            shop_manager_roles[int(g)] = v
        for g, v in data.get("contract_settings", {}).items():
            contract_settings[int(g)] = v
        for g, v in data.get("contract_roles", {}).items():
            contract_roles[int(g)] = v
        for mid, v in data.get("active_contracts", {}).items():
            active_contracts[int(mid)] = v
        for g, v in data.get("feedback_settings", {}).items():
            feedback_settings[int(g)] = v
        for g, v in data.get("voice_reward_settings", {}).items():
            voice_reward_settings[int(g)] = v
        for g, v in data.get("cabinet_panels", {}).items():
            cabinet_panels[int(g)] = v
        for g, v in data.get("cabinet_invite_links", {}).items():
            cabinet_invite_links[int(g)] = v
        for g, us in data.get("message_counts", {}).items():
            message_counts[int(g)] = {int(u): v for u, v in us.items()}
        for g, us in data.get("voice_minutes", {}).items():
            voice_minutes[int(g)] = {int(u): v for u, v in us.items()}
        for g, v in data.get("obshak_panels", {}).items():
            obshak_panels[int(g)] = v
        for g, v in data.get("obshak_log_channels", {}).items():
            obshak_log_channels[int(g)] = v
        for g, v in data.get("obshak_ping_roles", {}).items():
            obshak_ping_roles[int(g)] = v
        for g, v in data.get("roster_settings", {}).items():
            roster_settings[int(g)] = v
        for g, v in data.get("vzp_roles2", {}).items():
            vzp_roles2[int(g)] = v
        for g, v in data.get("mp_roles2", {}).items():
            mp_roles2[int(g)] = v
        for g, v in data.get("list_roles2", {}).items():
            list_roles2[int(g)] = v
        for c, v in data.get("ticket_voice_channels", {}).items():
            ticket_voice_channels[int(c)] = int(v)
        for g, v in data.get("interview_channels", {}).items():
            interview_channels[int(g)] = v
        for g, v in data.get("vzp_monitor_config", {}).items():
            vzp_monitor_config[int(g)] = v
        for g, v in data.get("vzp_processed_events", {}).items():
            vzp_processed_events[int(g)] = v
        for g, roles in data.get("casino_role_luck", {}).items():
            casino_role_luck[int(g)] = {int(r): v for r, v in roles.items()}

        print("OK: Data loaded from data.json")
    except Exception as e:
        print(f"WARNING: Failed to load data: {e}")


# ─────────────────────────────────────────────
# ОТДЕЛЬНЫЕ БД: ОБЩАК
# ─────────────────────────────────────────────
def save_obshak():
    try:
        data = {"deposits": {str(g): v for g, v in obshak_deposits.items()}}
        with open(OBSHAK_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"WARNING: Failed to save obshak: {e}")


def load_obshak():
    try:
        if not os.path.exists(OBSHAK_FILE):
            return
        with open(OBSHAK_FILE, "r", encoding="utf-8") as f:
            doc = json.load(f)
        for g, v in doc.get("deposits", {}).items():
            obshak_deposits[int(g)] = v
        print("OK: Obshak loaded from obshak.json")
    except Exception as e:
        print(f"WARNING: Failed to load obshak: {e}")


# ─────────────────────────────────────────────
# ОТДЕЛЬНЫЕ БД: БАЛЛЫ
# ─────────────────────────────────────────────
def save_points():
    try:
        data = {
            "points": {str(g): {str(u): v for u, v in us.items()} for g, us in points_db.items()}
        }
        with open(POINTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"WARNING: Failed to save points: {e}")


def load_points():
    try:
        if not os.path.exists(POINTS_FILE):
            return
        with open(POINTS_FILE, "r", encoding="utf-8") as f:
            doc = json.load(f)
        for g, us in doc.get("points", {}).items():
            points_db[int(g)] = {int(u): v for u, v in us.items()}
        print("OK: Points loaded from points.json")
    except Exception as e:
        print(f"WARNING: Failed to load points: {e}")


def save_chips():
    try:
        data = {
            "chips": {str(g): {str(u): v for u, v in us.items()} for g, us in chips_db.items()}
        }
        with open(CHIPS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"WARNING: Failed to save chips: {e}")


def load_chips():
    try:
        if not os.path.exists(CHIPS_FILE):
            return
        with open(CHIPS_FILE, "r", encoding="utf-8") as f:
            doc = json.load(f)
        for g, us in doc.get("chips", {}).items():
            chips_db[int(g)] = {int(u): v for u, v in us.items()}
        print("OK: Chips loaded from chips.json")
    except Exception as e:
        print(f"WARNING: Failed to load chips: {e}")


async def refresh_afk_message(guild: discord.Guild):
    panel = afk_panels.get(guild.id)
    if not panel:
        return
    try:
        channel = guild.get_channel(panel["channel_id"])
        msg     = await channel.fetch_message(panel["message_id"])
        await msg.edit(embed=build_afk_embed(guild.id))
    except Exception:
        pass


async def refresh_inactive_message(guild: discord.Guild):
    panel = inactive_panels.get(guild.id)
    if not panel:
        return
    try:
        channel = guild.get_channel(panel["channel_id"])
        msg     = await channel.fetch_message(panel["message_id"])
        await msg.edit(embed=build_inactive_embed(guild.id))
    except Exception:
        pass


# ─────────────────────────────────────────────
# СБОР — СЛОТЫ
# ─────────────────────────────────────────────
class SlotButton(ui.Button):
    def __init__(self, slot_num: int, message_id: int, taken_by: int | None):
        super().__init__(
            label=str(slot_num),
            style=discord.ButtonStyle.danger if taken_by else discord.ButtonStyle.success,
            custom_id=f"slot_{message_id}_{slot_num}",
        )
        self.slot_num = slot_num
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        data = event_lists.get(self.message_id)
        if not data:
            return await interaction.response.send_message("❌ Сбор уже недоступен!", ephemeral=True)

        if data.get("closed"):
            return await interaction.response.send_message("🔒 Список закрыт!", ephemeral=True)

        user_id = interaction.user.id
        slots   = data["slots"]
        reserve = data.setdefault("reserve", [])

        if slots.get(self.slot_num) == user_id:
            # Покинуть слот
            slots[self.slot_num] = None
            msg_text = f"❌ Вы покинули слот **{self.slot_num}**"
        elif slots.get(self.slot_num) is not None:
            return await interaction.response.send_message(
                f"❌ Слот **{self.slot_num}** уже занят!", ephemeral=True
            )
        else:
            # Освободить предыдущий слот если есть
            for s, uid in slots.items():
                if uid == user_id:
                    slots[s] = None
                    break
            if user_id in reserve:
                reserve.remove(user_id)
            slots[self.slot_num] = user_id
            msg_text = f"✅ Вы заняли слот **{self.slot_num}**!"

        save_data()
        new_view = EventView(self.message_id)
        embed    = build_event_embed(interaction.guild_id, data["title"], data["max"], slots, data.get("image_url"), data.get("note"), event_time=data.get("event_time"), closed=data.get("closed", False), reserve=reserve)
        await interaction.response.defer()
        await interaction.message.edit(embed=embed, view=new_view)
        await update_thread_list(self.message_id)
        await interaction.followup.send(msg_text, ephemeral=True)


class ReserveButton(ui.Button):
    """Кнопка записи в резерв (запасной список)."""
    def __init__(self, message_id: int):
        super().__init__(
            label="Резерв",
            emoji="🪑",
            style=discord.ButtonStyle.secondary,
            custom_id=f"reserve_{message_id}",
        )
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        data = event_lists.get(self.message_id)
        if not data:
            return await interaction.response.send_message("❌ Сбор уже недоступен!", ephemeral=True)
        if data.get("closed"):
            return await interaction.response.send_message("🔒 Список закрыт!", ephemeral=True)

        user_id = interaction.user.id
        slots = data["slots"]
        reserve = data.setdefault("reserve", [])
        join_mode = data.get("mode") == "join"

        if user_id in reserve:
            reserve.remove(user_id)
            msg_text = "❌ Вы покинули **резерв**"
        else:
            for s, uid in list(slots.items()):
                if uid == user_id:
                    slots[s] = None
            if user_id not in reserve:
                reserve.append(user_id)
            msg_text = "✅ Вы записались в **резерв**!"

        save_data()
        embed = build_event_embed(
            interaction.guild_id, data["title"], data["max"], slots,
            data.get("image_url"), data.get("note"), join_mode=join_mode,
            event_time=data.get("event_time"), closed=data.get("closed", False),
            reserve=reserve,
        )
        view = JoinEventView(self.message_id) if join_mode else EventView(self.message_id)
        await interaction.response.defer()
        await interaction.message.edit(embed=embed, view=view)
        await update_thread_list(self.message_id)
        await interaction.followup.send(msg_text, ephemeral=True)


class DeleteImageButton(ui.Button):
    """Кнопка-корзина: удаляет прикреплённое к сбору фото из эмбеда."""
    def __init__(self, message_id: int):
        super().__init__(
            emoji="🗑️",
            style=discord.ButtonStyle.secondary,
            custom_id=f"delimg_{message_id}",
            row=4,
        )
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        data = event_lists.get(self.message_id)
        if not data:
            return await interaction.response.send_message("❌ Сбор уже недоступен!", ephemeral=True)
        if not data.get("image_url"):
            return await interaction.response.send_message("❌ Фото уже удалено.", ephemeral=True)
        if not can_manage_event_message(interaction, data):
            return await interaction.response.send_message("❌ Недостаточно прав для удаления фото!", ephemeral=True)

        data["image_url"] = None
        save_data()

        join_mode = data.get("mode") == "join"
        embed = build_event_embed(
            interaction.guild_id, data["title"], data["max"], data["slots"],
            None, data.get("note"), join_mode=join_mode,
            event_time=data.get("event_time"), closed=data.get("closed", False),
            reserve=data.get("reserve", []),
        )
        view = JoinEventView(self.message_id) if join_mode else EventView(self.message_id)
        await interaction.response.edit_message(embed=embed, view=view, attachments=[])


class EventView(ui.View):
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id
        data = event_lists.get(message_id)
        if not data:
            return
        slots     = data.get("slots", {})
        max_count = data.get("max", 0)
        has_image = bool(data.get("image_url"))

        # Макс. 24 слота-кнопки + 1 «Резерв» (лимит Discord — 25).
        # Если есть фото — оставляем ещё одно место под кнопку-корзину.
        slot_count = min(max_count, 23 if has_image else 24)
        for i in range(1, slot_count + 1):
            self.add_item(SlotButton(i, message_id, slots.get(i)))
        self.add_item(ReserveButton(message_id))
        if has_image:
            self.add_item(DeleteImageButton(message_id))


class JoinButton(ui.Button):
    """Одна кнопка ✅ для записи/выхода — используется когда слотов > 24."""
    def __init__(self, message_id: int):
        super().__init__(
            label="Записаться",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=f"join_{message_id}",
        )
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        data = event_lists.get(self.message_id)
        if not data:
            return await interaction.response.send_message("❌ Сбор недоступен!", ephemeral=True)
        
        if data.get("closed"):
            return await interaction.response.send_message("🔒 Список закрыт!", ephemeral=True)
        
        user_id = interaction.user.id
        slots = data["slots"]
        reserve = data.setdefault("reserve", [])

        # Уже записан — выйти
        for slot_num, uid in slots.items():
            if uid == user_id:
                slots[slot_num] = None
                save_data()
                embed = build_event_embed(interaction.guild_id, data["title"], data["max"], slots, data.get("image_url"), data.get("note"), join_mode=True, event_time=data.get("event_time"), closed=data.get("closed", False), reserve=reserve)
                await interaction.response.defer()
                await interaction.message.edit(embed=embed)
                await update_thread_list(self.message_id)
                await interaction.followup.send("❌ Вы покинули сбор", ephemeral=True)
                return

        # Найти свободный слот
        for i in range(1, data["max"] + 1):
            if slots.get(i) is None:
                if user_id in reserve:
                    reserve.remove(user_id)
                slots[i] = user_id
                save_data()
                embed = build_event_embed(interaction.guild_id, data["title"], data["max"], slots, data.get("image_url"), data.get("note"), join_mode=True, event_time=data.get("event_time"), closed=data.get("closed", False), reserve=reserve)
                await interaction.response.defer()
                await interaction.message.edit(embed=embed)
                await update_thread_list(self.message_id)
                await interaction.followup.send("✅ Вы записались в сбор!", ephemeral=True)
                return

        await interaction.response.send_message("❌ Все места заняты! Запишись в **🪑 Резерв**.", ephemeral=True)



class JoinEventView(ui.View):
    """View с кнопкой ✅ и резервом. Для сборов с > 24 слотами / !list."""
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id
        self.add_item(JoinButton(message_id))
        self.add_item(ReserveButton(message_id))
        data = event_lists.get(message_id)
        if data and data.get("image_url"):
            self.add_item(DeleteImageButton(message_id))


class KickButton(ui.Button):
    def __init__(self, message_id: int):
        super().__init__(
            label="Кик из слота",
            emoji="👢",
            style=discord.ButtonStyle.secondary,
            custom_id=f"kick_{message_id}",
        )
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
        data = event_lists.get(self.message_id)
        if not data:
            return await interaction.response.send_message("❌ Сбор не найден!", ephemeral=True)

        slots = data["slots"]
        reserve = data.setdefault("reserve", [])
        members_in_slots = [(slot_num, uid) for slot_num, uid in slots.items() if uid is not None]
        if not members_in_slots and not reserve:
            return await interaction.response.send_message("❌ Слоты и резерв пусты!", ephemeral=True)

        options = [
            discord.SelectOption(label=f"Слот {s}: {uid}", value=f"S{s}")
            for s, uid in members_in_slots
        ]
        options += [
            discord.SelectOption(label=f"Резерв R{i}: {uid}", value=f"R{i - 1}")
            for i, uid in enumerate(reserve, 1)
        ]
        options = options[:25]

        class KickSelect(ui.Select):
            def __init__(self_inner):
                super().__init__(placeholder="Выбери участника для кика...", options=options)

            async def callback(self_inner, inter: discord.Interaction):
                value = self_inner.values[0]
                if value.startswith("R"):
                    idx = int(value[1:])
                    kicked_uid = reserve.pop(idx) if 0 <= idx < len(reserve) else None
                    where = "резерва"
                else:
                    slot_num = int(value[1:])
                    kicked_uid = data["slots"].get(slot_num)
                    data["slots"][slot_num] = None
                    where = f"слота **{slot_num}**"
                save_data()
                join_mode = data.get("mode") == "join"
                embed = build_event_embed(inter.guild_id, data["title"], data["max"], data["slots"], data.get("image_url"), data.get("note"), join_mode=join_mode, event_time=data.get("event_time"), closed=data.get("closed", False), reserve=reserve)
                try:
                    ch = bot.get_channel(data["channel_id"])
                    orig_msg = await ch.fetch_message(self.message_id)
                    view = JoinEventView(self.message_id) if join_mode else EventView(self.message_id)
                    await orig_msg.edit(embed=embed, view=view)
                except Exception:
                    pass
                await update_thread_list(self.message_id)
                await inter.response.edit_message(content=f"✅ <@{kicked_uid}> убран из {where}", view=None, embed=None)

        kick_view = ui.View(timeout=60)
        kick_view.add_item(KickSelect())
        await interaction.response.send_message("Выбери участника для кика:", view=kick_view, ephemeral=True)


class PromoteButton(ui.Button):
    def __init__(self, message_id: int):
        super().__init__(
            label="Убрать с резерва в основу",
            emoji="⬆️",
            style=discord.ButtonStyle.primary,
            custom_id=f"promote_from_reserve_{message_id}",
        )
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
        data = event_lists.get(self.message_id)
        if not data:
            return await interaction.response.send_message("❌ Сбор не найден!", ephemeral=True)

        slots = data["slots"]
        reserve = data.setdefault("reserve", [])
        if not reserve:
            return await interaction.response.send_message("❌ Резерв пуст!", ephemeral=True)

        free_slot = next((i for i in range(1, data["max"] + 1) if slots.get(i) is None), None)
        if free_slot is None:
            return await interaction.response.send_message("❌ Нет свободных слотов в основе!", ephemeral=True)

        options = [
            discord.SelectOption(label=f"Резерв R{i}: {uid}", value=str(i - 1))
            for i, uid in enumerate(reserve, 1)
        ][:25]

        class PromoteSelect(ui.Select):
            def __init__(self_inner):
                super().__init__(placeholder="Выбери участника для переноса в основу...", options=options)

            async def callback(self_inner, inter: discord.Interaction):
                idx = int(self_inner.values[0])
                if not (0 <= idx < len(reserve)):
                    return await inter.response.edit_message(content="❌ Устарело, попробуй снова", view=None)
                target_slot = next((i for i in range(1, data["max"] + 1) if slots.get(i) is None), None)
                if target_slot is None:
                    return await inter.response.edit_message(content="❌ Нет свободных слотов в основе!", view=None)
                promoted_uid = reserve.pop(idx)
                slots[target_slot] = promoted_uid
                save_data()
                join_mode = data.get("mode") == "join"
                embed = build_event_embed(inter.guild_id, data["title"], data["max"], data["slots"], data.get("image_url"), data.get("note"), join_mode=join_mode, event_time=data.get("event_time"), closed=data.get("closed", False), reserve=reserve)
                try:
                    ch = bot.get_channel(data["channel_id"])
                    orig_msg = await ch.fetch_message(self.message_id)
                    view = JoinEventView(self.message_id) if join_mode else EventView(self.message_id)
                    await orig_msg.edit(embed=embed, view=view)
                except Exception:
                    pass
                await update_thread_list(self.message_id)
                await inter.response.edit_message(content=f"✅ <@{promoted_uid}> перенесён из резерва в слот **{target_slot}**", view=None, embed=None)

        promote_view = ui.View(timeout=60)
        promote_view.add_item(PromoteSelect())
        await interaction.response.send_message("Выбери участника для переноса в основу:", view=promote_view, ephemeral=True)


class CloseListButton(ui.Button):
    def __init__(self, message_id: int, is_closed: bool):
        super().__init__(
            label="🔓 Открыть список" if is_closed else "🔒 Закрыть список",
            style=discord.ButtonStyle.secondary if is_closed else discord.ButtonStyle.danger,
            custom_id=f"close_list_{message_id}",
        )
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
        data = event_lists.get(self.message_id)
        if not data:
            return await interaction.response.send_message("❌ Сбор не найден!", ephemeral=True)

        data["closed"] = not data.get("closed", False)
        save_data()

        join_mode = data.get("mode") == "join"
        embed = build_event_embed(interaction.guild_id, data["title"], data["max"], data["slots"], data.get("image_url"), data.get("note"), join_mode=join_mode, event_time=data.get("event_time"), closed=data["closed"], reserve=data.get("reserve", []))
        try:
            ch = bot.get_channel(data["channel_id"])
            orig_msg = await ch.fetch_message(self.message_id)
            view = JoinEventView(self.message_id) if join_mode else EventView(self.message_id)
            await orig_msg.edit(embed=embed, view=view)
        except Exception:
            pass

        new_thread_view = ThreadListView(self.message_id)
        try:
            thread = bot.get_channel(data["thread_id"])
            if thread:
                msg = await thread.fetch_message(data["thread_msg_id"])
                await msg.edit(content=build_thread_list(data["title"], data["max"], data["slots"], data.get("reserve", [])), view=new_thread_view)
        except Exception:
            pass

        status = "закрыт 🔒" if data["closed"] else "открыт 🔓"
        await interaction.response.send_message(f"✅ Список {status}", ephemeral=True)


class ThreadListView(ui.View):
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        data = event_lists.get(message_id)
        self.add_item(KickButton(message_id))
        self.add_item(PromoteButton(message_id))
        self.add_item(CloseListButton(message_id, (data or {}).get("closed", False)))


# ─────────────────────────────────────────────
# МОДАЛКИ
# ─────────────────────────────────────────────
class AfkModal(ui.Modal, title="🕐 Уход в АФК"):
    reason      = ui.TextInput(label="Причина", placeholder="На работе / Учёба / Дела...", required=True)
    return_time = ui.TextInput(label="Вернусь в (например 18:30)", placeholder="18:30", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        user_id  = interaction.user.id

        if guild_id not in afk_list:
            afk_list[guild_id] = {}

        afk_list[guild_id][user_id] = {
            "reason":      str(self.reason),
            "return_time": str(self.return_time),
            "since":       now_msk(),
        }
        save_data()

        await refresh_afk_message(interaction.guild)

        embed = discord.Embed(
            description=(
                f"🕐 Вы добавлены в АФК-список\n"
                f"**Причина:** {self.reason}\n"
                f"**Вернусь в:** `{self.return_time}`"
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
        await interaction.response.send_message(embed=embed, ephemeral=True)


class RejectModal(ui.Modal, title="❌ Причина отклонения"):
    reason = ui.TextInput(label="Укажите причину", style=discord.TextStyle.paragraph, required=True)

    def __init__(self, applicant_id: int, original_message: discord.Message, channel: discord.TextChannel):
        super().__init__()
        self.applicant_id     = applicant_id
        self.original_message = original_message
        self.channel          = channel

    async def on_submit(self, interaction: discord.Interaction):
        reason = str(self.reason)

        old_embed = self.original_message.embeds[0]
        new_embed = old_embed.copy()
        new_embed.color = discord.Color.red()
        new_embed.add_field(
            name="❌ Статус",
            value=f"Отклонено — {interaction.user.mention}\n**Причина:** {reason}",
            inline=False,
        )
        await self.original_message.edit(embed=new_embed, view=None)

        try:
            target = await interaction.client.fetch_user(self.applicant_id)
            dm_embed = discord.Embed(
                title="❌ Ваша заявка отклонена",
                description=f"**Причина:**\n> {reason}",
                color=discord.Color.red(),
                timestamp=datetime.now(),
            )
            dm_embed.add_field(name="📅 Дата", value=datetime.now().strftime("%d.%m.%Y %H:%M"))
            dm_embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
            await target.send(embed=dm_embed)
        except Exception:
            pass

        log_channel_id = reject_log_channels.get(interaction.guild_id)
        if log_channel_id:
            log_channel = interaction.guild.get_channel(log_channel_id)
            if log_channel:
                try:
                    log_embed = discord.Embed(
                        title="❌ Заявка отклонена",
                        color=discord.Color.red(),
                        timestamp=datetime.now(),
                    )
                    log_embed.add_field(name="Заявка от пользователя", value=f"<@{self.applicant_id}>", inline=False)
                    log_embed.add_field(name="Причина", value=reason, inline=False)
                    log_embed.add_field(name="Рассматривал", value=interaction.user.mention, inline=False)
                    log_embed.set_thumbnail(url=_footer(interaction.guild_id))
                    log_embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
                    await log_channel.send(embed=log_embed)
                except Exception:
                    pass

        await interaction.response.send_message(
            "✅ Заявка отклонена. Канал закроется через 10 секунд.", ephemeral=True
        )
        await asyncio.sleep(10)
        try:
            await self.channel.delete(reason="Заявка отклонена")
        except Exception:
            pass


class ApplicationModal(ui.Modal, title="📋 Подать заявку"):
    nickname = ui.TextInput(label="Ваш ник и статик в игре",  placeholder="Nick Name | 777",     required=True)
    hours_age = ui.TextInput(label="Часов в игре / Возраст",  placeholder="2500 / 18",            required=True)
    families  = ui.TextInput(label="В каких семьях был?",     style=discord.TextStyle.paragraph,  required=True)
    recoil    = ui.TextInput(label="Откат со стрельбой",      placeholder="DM, Архив, YouTube",   required=True)
    content   = ui.TextInput(label="Какой контент симпатизирует?", placeholder="РП / ВЗП",        required=True)

    def __init__(self, category_id: int):
        super().__init__()
        self.category_id = category_id

    async def on_submit(self, interaction: discord.Interaction):
        guild      = interaction.guild
        applicant  = interaction.user
        category   = guild.get_channel(self.category_id)
        admin_role_id = admin_roles.get(guild.id)
        admin_role    = guild.get_role(admin_role_id) if admin_role_id else None
        tm_role_id    = ticket_manager_roles.get(guild.id)
        tm_role       = guild.get_role(tm_role_id) if tm_role_id else None
        ping_role_id  = ticket_ping_role.get(guild.id)
        ping_role     = guild.get_role(ping_role_id) if ping_role_id else tm_role

        ticket_counters[guild.id] = ticket_counters.get(guild.id, 0) + 1
        ticket_num = ticket_counters[guild.id]
        save_data()

        ticket_perms = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            applicant: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
        }
        if admin_role:
            overwrites[admin_role] = ticket_perms
        if tm_role:
            overwrites[tm_role] = ticket_perms
        # Роли с доступом на просмотр
        for rid in ticket_viewer_roles.get(guild.id, []):
            r = guild.get_role(rid)
            if r:
                overwrites[r] = ticket_perms

        try:
            ticket_channel = await guild.create_text_channel(
                name=f"ticket-{str(ticket_num).zfill(4)}",
                category=category,
                overwrites=overwrites,
                reason=f"Заявка от {applicant}",
            )
        except Exception as e:
            return await interaction.response.send_message(
                f"❌ Не удалось создать канал: {e}", ephemeral=True
            )

        embed = discord.Embed(
            title="📋 Новая заявка",
            color=discord.Color.yellow(),
            timestamp=datetime.now(),
        )
        embed.set_thumbnail(url=applicant.display_avatar.url)
        embed.add_field(name="👤 Пользователь",           value=f"{applicant.mention} ({applicant})", inline=True)
        embed.add_field(name="🎮 Ник | Статик",            value=str(self.nickname),   inline=True)
        embed.add_field(name="\u200B",                     value="\u200B",             inline=True)
        embed.add_field(name="⏱️ Часов / Возраст",        value=str(self.hours_age),  inline=True)
        embed.add_field(name="🎯 Откат со стрельбой",     value=str(self.recoil),     inline=True)
        embed.add_field(name="🎮 Контент",                value=str(self.content),    inline=True)
        embed.add_field(name="🏠 Был в семьях",           value=str(self.families),   inline=False)
        embed.set_footer(text=f"MORIARTY • {applicant.id}", icon_url=_footer(guild.id))

        view = ApplicationReviewView(applicant.id)
        pings = ping_role.mention if ping_role else None
        await ticket_channel.send(content=pings, embed=embed, view=view)

        sent_embed = discord.Embed(
            title="📬 Заявка отправлена!",
            description=(
                "Ваша заявка принята. Ожидайте ответа.\n"
                "Результат придёт в личные сообщения."
            ),
            color=discord.Color.yellow(),
            timestamp=datetime.now(),
        )
        sent_embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
        await interaction.response.send_message(embed=sent_embed, ephemeral=True)


# ─────────────────────────────────────────────
# КНОПКИ (View)
# ─────────────────────────────────────────────
class AfkView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Отошел АФК", style=discord.ButtonStyle.secondary, emoji="🕐", custom_id="afk_away")
    async def afk_away(self, interaction: discord.Interaction, button: ui.Button):
        guild_id = interaction.guild_id
        if guild_id in afk_list and interaction.user.id in afk_list[guild_id]:
            return await interaction.response.send_message("⚠️ Вы уже в АФК-списке!", ephemeral=True)
        await interaction.response.send_modal(AfkModal())

    @ui.button(label="Вернулся из АФК", style=discord.ButtonStyle.success, emoji="✅", custom_id="afk_back")
    async def afk_back(self, interaction: discord.Interaction, button: ui.Button):
        guild_id = interaction.guild_id
        user_id  = interaction.user.id
        if guild_id not in afk_list or user_id not in afk_list[guild_id]:
            return await interaction.response.send_message("⚠️ Вас нет в АФК-списке!", ephemeral=True)

        del afk_list[guild_id][user_id]
        save_data()
        await refresh_afk_message(interaction.guild)
        await interaction.response.send_message("✅ Вы убраны из АФК-списка. С возвращением!", ephemeral=True)


class InactiveModal(ui.Modal, title="📅 Уход в инактив"):
    reason      = ui.TextInput(label="Причина", placeholder="Отпуск / Работа / Дела...", required=True)
    return_date = ui.TextInput(label="Вернусь (например 25.04.2026)", placeholder="25.04.2026", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        user_id  = interaction.user.id

        raw = str(self.return_date).strip()
        import re as _re
        m = _re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", raw)
        if not m:
            return await interaction.response.send_message(
                "⚠️ Неверный формат даты. Используй формат **ДД.ММ.ГГГГ**, например `25.04.2026`",
                ephemeral=True,
            )
        day, mon, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= mon <= 12 and 1 <= day <= 31 and year >= 2020):
            return await interaction.response.send_message(
                "⚠️ Некорректная дата. Проверь, что день и месяц указаны правильно.",
                ephemeral=True,
            )

        if guild_id not in inactive_list:
            inactive_list[guild_id] = {}

        inactive_list[guild_id][user_id] = {
            "reason":      str(self.reason),
            "return_date": raw,
            "since":       datetime.now(),
        }
        save_data()

        await refresh_inactive_message(interaction.guild)

        embed = discord.Embed(
            description=(
                f"📅 Вы добавлены в список инактива\n"
                f"**Причина:** {self.reason}\n"
                f"**Вернусь:** `{raw}`"
            ),
            color=discord.Color.orange(),
        )
        embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
        await interaction.response.send_message(embed=embed, ephemeral=True)


class InactiveView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Ухожу в инактив", style=discord.ButtonStyle.secondary, emoji="📅", custom_id="inactive_away")
    async def inactive_away(self, interaction: discord.Interaction, button: ui.Button):
        guild_id = interaction.guild_id
        if guild_id in inactive_list and interaction.user.id in inactive_list[guild_id]:
            return await interaction.response.send_message("⚠️ Вы уже в списке инактива!", ephemeral=True)
        await interaction.response.send_modal(InactiveModal())

    @ui.button(label="Вернулся из инактива", style=discord.ButtonStyle.success, emoji="✅", custom_id="inactive_back")
    async def inactive_back(self, interaction: discord.Interaction, button: ui.Button):
        guild_id = interaction.guild_id
        user_id  = interaction.user.id
        if guild_id not in inactive_list or user_id not in inactive_list[guild_id]:
            return await interaction.response.send_message("⚠️ Вас нет в списке инактива!", ephemeral=True)

        del inactive_list[guild_id][user_id]
        save_data()
        await refresh_inactive_message(interaction.guild)
        await interaction.response.send_message("✅ Вы убраны из инактива. С возвращением!", ephemeral=True)


class TicketPanelView(ui.View):
    def __init__(self, category_id: int):
        super().__init__(timeout=None)
        self.category_id = category_id

    @ui.button(label="Подать заявку", style=discord.ButtonStyle.secondary, emoji="📋", custom_id="ticket_apply")
    async def apply(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(ApplicationModal(self.category_id))


async def _ensure_ticket_call_voice(
    guild: discord.Guild,
    ticket_channel: discord.abc.GuildChannel,
    applicant_id: int,
    manager: discord.Member,
) -> discord.VoiceChannel:
    """Создаёт (или возвращает существующий) голосовой канал для обзвона по тикету."""
    existing_id = ticket_voice_channels.get(ticket_channel.id)
    if existing_id:
        vc = guild.get_channel(existing_id)
        if isinstance(vc, discord.VoiceChannel):
            return vc

    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, connect=True, manage_channels=True, move_members=True, speak=True
        ),
        manager: discord.PermissionOverwrite(
            view_channel=True, connect=True, speak=True, move_members=True
        ),
    }

    applicant = guild.get_member(applicant_id)
    if applicant is None:
        try:
            applicant = await guild.fetch_member(applicant_id)
        except Exception:
            applicant = None
    if applicant:
        overwrites[applicant] = discord.PermissionOverwrite(
            view_channel=True, connect=True, speak=True
        )

    tm_role_id = ticket_manager_roles.get(guild.id)
    if tm_role_id:
        tm_role = guild.get_role(tm_role_id)
        if tm_role:
            overwrites[tm_role] = discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True, move_members=True
            )

    for rid in ticket_viewer_roles.get(guild.id, []):
        r = guild.get_role(rid)
        if r:
            overwrites[r] = discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True
            )

    name = f"обзвон-{ticket_channel.name}"[:100]
    vc = await guild.create_voice_channel(
        name=name,
        category=getattr(ticket_channel, "category", None),
        overwrites=overwrites,
        reason=f"Обзвон по заявке {ticket_channel.name}",
    )
    ticket_voice_channels[ticket_channel.id] = vc.id
    save_data()
    return vc


async def _delete_ticket_call_voice(guild: discord.Guild, ticket_channel_id: int):
    """Удаляет голосовой канал обзвона, привязанный к тикету."""
    vc_id = ticket_voice_channels.pop(ticket_channel_id, None)
    if not vc_id:
        return
    save_data()
    vc = guild.get_channel(vc_id)
    if isinstance(vc, discord.VoiceChannel):
        try:
            await vc.delete(reason="Тикет закрыт — обзвон завершён")
        except Exception:
            pass


class ApplicationReviewView(ui.View):
    def __init__(self, applicant_id: int = 0):
        super().__init__(timeout=None)
        self.applicant_id = applicant_id

    def _get_applicant_id(self, message: discord.Message) -> int:
        if self.applicant_id:
            return self.applicant_id
        try:
            footer = message.embeds[0].footer.text  # "MORIARTY • 123..."
            return int(footer.split("• ")[-1].strip())
        except Exception:
            return 0

    @ui.button(label="✅ Одобрить", style=discord.ButtonStyle.success, custom_id="ticket_approve")
    async def approve(self, interaction: discord.Interaction, button: ui.Button):
        if not is_ticket_manager(interaction):
            return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)

        channel = interaction.channel
        await interaction.response.defer(ephemeral=True)

        applicant_id = self._get_applicant_id(interaction.message)

        old_embed = interaction.message.embeds[0]
        new_embed = old_embed.copy()
        new_embed.color = discord.Color.green()
        new_embed.add_field(
            name="✅ Статус",
            value=f"Одобрено — {interaction.user.mention} ({datetime.now().strftime('%d.%m.%Y %H:%M')})",
            inline=False,
        )
        await interaction.message.edit(embed=new_embed, view=None)

        try:
            target = await interaction.client.fetch_user(applicant_id)
            dm_embed = discord.Embed(
                title="🏆 Добро пожаловать в семью MORIARTY!",
                description=(
                    "Твоя заявка была **одобрена**!\n\n"
                    "Добро пожаловать! 🖤"
                ),
                color=0x2B2D31,
                timestamp=datetime.now(),
            )
            dm_embed.add_field(name="📅 Дата принятия", value=datetime.now().strftime("%d.%m.%Y %H:%M"), inline=True)
            dm_embed.add_field(name="👮 Одобрил", value=interaction.user.mention, inline=True)
            dm_embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
            if _approve_gif(interaction.guild_id):
                dm_embed.set_image(url=_approve_gif(interaction.guild_id))
            await target.send(embed=dm_embed)
        except Exception:
            pass

        log_channel_id = reject_log_channels.get(interaction.guild_id)
        if log_channel_id:
            log_channel = interaction.client.get_channel(log_channel_id)
            if log_channel:
                try:
                    log_embed = discord.Embed(
                        title="✅ Заявка одобрена",
                        color=discord.Color.green(),
                        timestamp=datetime.now(),
                    )
                    log_embed.add_field(name="Заявка от пользователя", value=f"<@{applicant_id}>", inline=False)
                    log_embed.add_field(name="Одобрил", value=interaction.user.mention, inline=False)
                    log_embed.set_thumbnail(url=_footer(interaction.guild_id))
                    log_embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
                    await log_channel.send(embed=log_embed)
                except Exception:
                    pass

        await interaction.followup.send("✅ Заявка одобрена.", ephemeral=True)
        close_embed = discord.Embed(
            title="🔒 Тикет закрыт",
            description=f"Заявка **одобрена** — {interaction.user.mention}\nВыберите следующее действие:",
            color=discord.Color.green(),
            timestamp=datetime.now(),
        )
        close_embed.set_footer(text=f"MORIARTY • {applicant_id}", icon_url=_footer(interaction.guild_id))
        await channel.send(embed=close_embed, view=PostCloseView())

    @ui.button(label="Пригласить на обзвон", style=discord.ButtonStyle.primary, emoji="🎤", custom_id="ticket_invite_call")
    async def invite_call(self, interaction: discord.Interaction, button: ui.Button):
        if not is_ticket_manager(interaction):
            return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)

        applicant_id = self._get_applicant_id(interaction.message)
        if not applicant_id:
            return await interaction.response.send_message("❌ Не удалось определить заявителя.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        ticket_ch = interaction.channel

        try:
            vc = await _ensure_ticket_call_voice(guild, ticket_ch, applicant_id, interaction.user)
        except Exception as e:
            return await interaction.followup.send(
                f"❌ Не удалось создать голосовой канал: {e}", ephemeral=True
            )

        link = f"https://discord.com/channels/{guild.id}/{vc.id}"
        dm_ok = False
        try:
            target = await interaction.client.fetch_user(applicant_id)
            dm_embed = discord.Embed(
                title="🎤 Приглашение на обзвон",
                description=(
                    f"Тебя пригласили на **обзвон** по заявке в **{guild.name}**.\n\n"
                    f"Зайди в голосовой канал: **{vc.name}**\n"
                    f"→ {link}"
                ),
                color=discord.Color.blue(),
                timestamp=datetime.now(),
            )
            dm_embed.add_field(name="👮 Пригласил", value=interaction.user.mention, inline=True)
            dm_embed.set_footer(text="MORIARTY", icon_url=_footer(guild.id))
            await target.send(embed=dm_embed)
            dm_ok = True
        except Exception:
            pass

        note = discord.Embed(
            title="🎤 Обзвон",
            description=(
                f"{interaction.user.mention} пригласил <@{applicant_id}> на обзвон.\n"
                f"Канал: {vc.mention}"
            ),
            color=discord.Color.blue(),
            timestamp=datetime.now(),
        )
        note.set_footer(text="MORIARTY", icon_url=_footer(guild.id))
        try:
            await ticket_ch.send(embed=note)
        except Exception:
            pass

        if dm_ok:
            await interaction.followup.send(
                f"✅ Приглашение отправлено в ЛС. Канал: {vc.mention}", ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"⚠️ Канал создан ({vc.mention}), но ЛС закрыты — напиши заявителю вручную.",
                ephemeral=True,
            )

    @ui.button(label="❌ Отклонить", style=discord.ButtonStyle.danger, custom_id="ticket_reject")
    async def reject(self, interaction: discord.Interaction, button: ui.Button):
        if not is_ticket_manager(interaction):
            return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
        applicant_id = self._get_applicant_id(interaction.message)
        await interaction.response.send_modal(RejectModal(applicant_id, interaction.message, interaction.channel))


# ─────────────────────────────────────────────
# ТИКЕТ — ПОСТ-ЗАКРЫТИЕ (Reopen / Delete)
# ─────────────────────────────────────────────
class PostCloseView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    def _get_applicant_id(self, message: discord.Message) -> int:
        try:
            return int(message.embeds[0].footer.text.split("• ")[-1].strip())
        except Exception:
            return 0

    @ui.button(label="🔓 Открыть тикет", style=discord.ButtonStyle.success, custom_id="ticket_reopen")
    async def reopen(self, interaction: discord.Interaction, button: ui.Button):
        if not is_ticket_manager(interaction):
            return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
        applicant_id = self._get_applicant_id(interaction.message)
        await interaction.response.defer(ephemeral=True)
        await interaction.message.delete()
        reopen_embed = discord.Embed(
            title="🔓 Тикет переоткрыт",
            description=f"Переоткрыт — {interaction.user.mention}",
            color=discord.Color.yellow(),
            timestamp=datetime.now(),
        )
        reopen_embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
        await interaction.channel.send(embed=reopen_embed, view=ApplicationReviewView(applicant_id))
        await interaction.followup.send("✅ Тикет переоткрыт.", ephemeral=True)

    @ui.button(label="Пригласить на обзвон", style=discord.ButtonStyle.primary, emoji="🎙", custom_id="ticket_invite_voice")
    async def invite_voice(self, interaction: discord.Interaction, button: ui.Button):
        if not is_ticket_manager(interaction):
            return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        applicant_id = self._get_applicant_id(interaction.message)
        guild = interaction.guild

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(connect=False),
            interaction.user: discord.PermissionOverwrite(connect=True, manage_channels=True),
        }
        tm_role_id = ticket_manager_roles.get(guild.id)
        if tm_role_id:
            tm_role = guild.get_role(tm_role_id)
            if tm_role:
                overwrites[tm_role] = discord.PermissionOverwrite(connect=True)

        try:
            # Используем настроенный канал обзвона, если он есть
            interview_channel_id = interview_channels.get(guild.id)
            voice_channel = guild.get_channel(interview_channel_id) if interview_channel_id else None
            if voice_channel is None:
                voice_channel = await guild.create_voice_channel(
                    name=f"Обзвон-тикет-{applicant_id}",
                    overwrites=overwrites,
                )

            ticket_voice_channels[interaction.channel.id] = voice_channel.id
            save_data()

            try:
                target = await interaction.client.fetch_user(applicant_id)
                invite = await voice_channel.create_invite(max_age=3600)
                dm_embed = discord.Embed(
                    title="🎙 Приглашение на обзвон",
                    description=f"Вас приглашают на обзвон в канале {voice_channel.mention}!\n\nСсылка: {invite.url}",
                    color=discord.Color.blue(),
                    timestamp=datetime.now(),
                )
                dm_embed.set_footer(text="MORIARTY", icon_url=_footer(guild.id))
                await target.send(embed=dm_embed)
            except Exception as e:
                await interaction.followup.send(f"❌ Не удалось отправить DM пользователю: {e}", ephemeral=True)
            else:
                await interaction.followup.send(f"✅ Голосовой канал готов, приглашение отправлено в DM <@{applicant_id}>.", ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка при создании голосового канала: {e}", ephemeral=True)

    @ui.button(label="🗑️ Удалить канал", style=discord.ButtonStyle.danger, custom_id="ticket_delete_channel")
    async def delete_channel(self, interaction: discord.Interaction, button: ui.Button):
        if not is_ticket_manager(interaction):
            return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await _delete_ticket_call_voice(interaction.guild, interaction.channel.id)
        await asyncio.sleep(3)
        try:
            await interaction.channel.delete(reason=f"Тикет удалён — {interaction.user}")
        except Exception:
            pass


# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# МАГАЗИН - КНОПКИ
# ─────────────────────────────────────────────
async def refresh_shop_message(guild: discord.Guild):
    panel = shop_panels.get(guild.id)
    if not panel:
        return
    try:
        channel = guild.get_channel(panel["channel_id"])
        msg     = await channel.fetch_message(panel["message_id"])
        view    = ShopView(guild.id)
        await msg.edit(embed=build_shop_embed(guild.id), view=view)
    except Exception:
        pass


ACTION_LABELS = {
    "remove_warn": "🗑 Снятие варна",
    "give_role":   "🎭 Выдача роли",
    "notify":      "📦 Ручная выдача",
}

async def _log_shop_purchase(
    interaction: discord.Interaction,
    item: dict,
    price: int,
    action: str,
    extra: str | None = None,
):
    """Отправляет лог покупки в shop_log_channels.
    Для action='notify' тегает shop_manager_roles — нужна ручная выдача."""
    guild_id = interaction.guild_id
    log_ch_id = shop_log_channels.get(guild_id)
    if not log_ch_id:
        return
    log_ch = interaction.guild.get_channel(log_ch_id)
    if not log_ch:
        return

    embed = discord.Embed(
        title="🛒 Покупка в магазине",
        color=discord.Color.gold(),
        timestamp=datetime.now(),
    )
    embed.add_field(name="Покупатель", value=interaction.user.mention, inline=True)
    embed.add_field(name="Товар", value=f"{item.get('emoji', '')} {item['name']}".strip(), inline=True)
    embed.add_field(name="Цена", value=f"**{price}** 💎", inline=True)
    embed.add_field(name="Тип", value=ACTION_LABELS.get(action, action), inline=True)
    if extra:
        embed.add_field(name="Выдано", value=extra, inline=True)
    embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))

    # Тег роли только для ручной выдачи
    content = None
    if action == "notify":
        mgr_role_id = shop_manager_roles.get(guild_id)
        if mgr_role_id:
            content = f"<@&{mgr_role_id}>"

    try:
        await log_ch.send(content=content, embed=embed)
    except Exception:
        pass


class ShopItemButton(ui.Button):
    def __init__(self, item_id: str, item: dict, guild_id: int):
        super().__init__(
            label=item["name"],
            emoji=item.get("emoji") or None,
            style=discord.ButtonStyle.primary,
            custom_id=f"shop_{guild_id}_{item_id}",
        )
        self.item_id  = item_id
        self.item     = item
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        user_id  = interaction.user.id
        item     = guild_shop_items.get(guild_id, {}).get(self.item_id)

        if not item:
            return await interaction.response.send_message("❌ Товар больше не доступен.", ephemeral=True)

        price  = item["price"]
        points = get_points(guild_id, user_id)

        if points < price:
            return await interaction.response.send_message(
                f"❌ Недостаточно баллов! Нужно **{price}** 💎, у вас **{points}** 💎",
                ephemeral=True,
            )

        action = item.get("action", "notify")

        if action == "remove_warn":
            warn_data = get_warns(guild_id, user_id)
            if not warn_data:
                return await interaction.response.send_message("✅ У вас нет варнов для снятия!", ephemeral=True)
            guild_warn_roles = warn_roles.get(guild_id, {})
            roles_to_remove = [interaction.guild.get_role(rid) for rid in guild_warn_roles.values()]
            try:
                await interaction.user.remove_roles(*[r for r in roles_to_remove if r], reason="Покупка: снятие варна")
            except Exception:
                pass
            remove_warn(guild_id, user_id)
            add_points(guild_id, user_id, -price)
            await _log_shop_purchase(interaction, item, price, action="remove_warn")
            embed = discord.Embed(title="✅ Варн снят!", description=f"Списано **{price}** 💎", color=discord.Color.green(), timestamp=datetime.now())
            embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        elif action == "give_role":
            role_id = item.get("role_id")
            role    = interaction.guild.get_role(role_id) if role_id else None
            if not role:
                return await interaction.response.send_message("❌ Роль не найдена. Обратитесь к администратору.", ephemeral=True)
            try:
                await interaction.user.add_roles(role, reason=f"Покупка в магазине: {item['name']}")
            except Exception:
                return await interaction.response.send_message("❌ Не удалось выдать роль.", ephemeral=True)
            add_points(guild_id, user_id, -price)
            await _log_shop_purchase(interaction, item, price, action="give_role", extra=role.mention)
            embed = discord.Embed(title=f"✅ Куплено: {item['name']}", description=f"Роль {role.mention} выдана!\nСписано **{price}** 💎", color=discord.Color.green(), timestamp=datetime.now())
            embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        else:  # notify — ручная выдача
            add_points(guild_id, user_id, -price)
            await _log_shop_purchase(interaction, item, price, action="notify")
            embed = discord.Embed(
                title=f"✅ Куплено: {item['name']}",
                description=f"Списано **{price}** 💎\nАдминистратор скоро свяжется с вами.",
                color=discord.Color.green(),
                timestamp=datetime.now(),
            )
            embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
            return await interaction.response.send_message(embed=embed, ephemeral=True)


class ShopView(ui.View):
    def __init__(self, guild_id: int = 0):
        super().__init__(timeout=None)
        items = guild_shop_items.get(guild_id, {})
        for item_id, item in items.items():
            self.add_item(ShopItemButton(item_id, item, guild_id))




# ─────────────────────────────────────────────
# СЛЭШ-КОМАНДЫ
# ─────────────────────────────────────────────
@bot.command(name="роль_реаки")
async def set_event_role(ctx, роль: discord.Role):
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    """!роль_реаки @роль — настроить роль для тега при !vzp и !list"""
    event_roles[ctx.guild.id] = роль.id
    save_data()
    embed = discord.Embed(
        title="✅ Роль настроена",
        description=f"При каждом `!vzp` и `!list` будет тегаться {роль.mention}",
        color=discord.Color.green(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed, delete_after=10)
    await ctx.message.delete()


async def _create_event_message(channel, guild, title: str, max_count: int, image_file=None, image_ref: str | None = None, content: str | None = None, force_join_mode: bool = False, event_time: str = None, cmd: str | None = None):
    """Создаёт сбор: эмбед + тред. <= 24 слотов → кнопки-цифры, > 24 → одна кнопка ✅."""
    if not (1 <= max_count <= 100):
        await channel.send("❌ Количество слотов: от 1 до 100!", delete_after=5)
        return

    join_mode = force_join_mode or max_count > 24
    slots     = {i: None for i in range(1, max_count + 1)}

    embed = build_event_embed(guild.id, title, max_count, slots, image_ref, join_mode=join_mode, event_time=event_time)

    if image_file:
        msg = await channel.send(content=content, embed=embed, file=image_file)
    else:
        msg = await channel.send(content=content, embed=embed)

    # Для последующих редактирований используем URL из вложения сообщения (стабильнее)
    if image_ref and msg.attachments:
        image_ref = msg.attachments[0].url
        embed.set_image(url=image_ref)

    event_lists[msg.id] = {
        "title": title, "max": max_count, "mode": "join" if join_mode else "buttons",
        "slots": slots, "reserve": [], "image_url": image_ref, "note": None,
        "channel_id": channel.id, "thread_id": None, "thread_msg_id": None,
        "event_time": event_time, "closed": False, "cmd": cmd,
    }

    view = JoinEventView(msg.id) if join_mode else EventView(msg.id)
    await msg.edit(embed=embed, view=view)

    # Тред с живым списком
    try:
        hint = "Нажми ✅ для записи · 🪑 Резерв — запасной список" if join_mode else "Кнопка слота · 🪑 Резерв — запасной список"
        thread = await msg.create_thread(name=f"💬 {title}", auto_archive_duration=1440)
        thread_embed = discord.Embed(
            description=f"📋 Обсуждение сбора **{title}**\n{hint}",
            color=discord.Color.blurple(),
        )
        thread_embed.set_footer(text="MORIARTY", icon_url=_footer(guild.id))
        await thread.send(embed=thread_embed)
        list_msg = await thread.send(build_thread_list(title, max_count, slots, []), view=ThreadListView(msg.id))
        event_lists[msg.id]["thread_id"]     = thread.id
        event_lists[msg.id]["thread_msg_id"] = list_msg.id
    except Exception:
        pass
    save_data()


# ─────────────────────────────────────────────
# PREFIX-КОМАНДЫ СБОРОВ
# ─────────────────────────────────────────────
@bot.command(name="vzp")
async def взп_cmd(ctx, количество: int = 10, *, название: str = "ВЗП"):
    """!vzp [количество] [название] — сбор с фото (от лица бота)"""
    if not can_run_event(ctx, "vzp"):
        return await ctx.message.delete()

    image_file = None
    image_ref  = None
    if ctx.message.attachments:
        att = ctx.message.attachments[0]
        try:
            img_bytes  = await att.read()
            ext        = att.filename.rsplit(".", 1)[-1].lower() if "." in att.filename else "png"
            safe_name  = f"event_image.{ext}"
            image_file = discord.File(io.BytesIO(img_bytes), filename=safe_name)
            image_ref  = f"attachment://{safe_name}"
        except Exception:
            pass

    try:
        await ctx.message.delete()
    except Exception:
        pass

    # Парсинг времени из начала названия
    event_time = None
    m = re.match(r'^(\d{1,2}:\d{2})\s*(.*)', название)
    if m:
        event_time = m.group(1)
        название = m.group(2).strip() or "ВЗП"

    # Тег: роль ВЗП + роль МП + вторые роли
    mentions = []
    for rid in [vzp_roles.get(ctx.guild.id), vzp_roles2.get(ctx.guild.id), mp_roles.get(ctx.guild.id), mp_roles2.get(ctx.guild.id)]:
        if rid:
            r = ctx.guild.get_role(rid)
            if r:
                mentions.append(r.mention)
    content = " ".join(mentions) if mentions else None

    await _create_event_message(ctx.channel, ctx.guild, название, количество, image_file, image_ref, content=content, event_time=event_time, cmd="vzp")


@bot.command(name="mp")
async def мп_cmd(ctx, количество: int = 10, *, название: str = "МП"):
    """!mp [количество] [название] — сбор МП"""
    if not can_run_event(ctx, "mp"):
        return await ctx.message.delete()

    image_file = None
    image_ref  = None
    if ctx.message.attachments:
        att = ctx.message.attachments[0]
        try:
            img_bytes  = await att.read()
            ext        = att.filename.rsplit(".", 1)[-1].lower() if "." in att.filename else "png"
            safe_name  = f"event_image.{ext}"
            image_file = discord.File(io.BytesIO(img_bytes), filename=safe_name)
            image_ref  = f"attachment://{safe_name}"
        except Exception:
            pass

    try:
        await ctx.message.delete()
    except Exception:
        pass

    # Парсинг времени из начала названия
    event_time = None
    m = re.match(r'^(\d{1,2}:\d{2})\s*(.*)', название)
    if m:
        event_time = m.group(1)
        название = m.group(2).strip() or "МП"

    mentions = []
    for rid in [mp_roles.get(ctx.guild.id), mp_roles2.get(ctx.guild.id)]:
        if rid:
            r = ctx.guild.get_role(rid)
            if r:
                mentions.append(r.mention)
    content = " ".join(mentions) if mentions else None

    await _create_event_message(ctx.channel, ctx.guild, название, количество, image_file, image_ref, content=content, event_time=event_time, cmd="mp")


@bot.command(name="роль_взп")
async def set_vzp_role(ctx, роль: discord.Role):
    """!роль_взп @роль — настроить роль ВЗП для тега в !vzp"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    vzp_roles[ctx.guild.id] = роль.id
    save_data()
    embed = discord.Embed(
        title="✅ Роль ВЗП настроена",
        description=f"В `!vzp` будет тегаться {роль.mention}",
        color=discord.Color.green(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed, delete_after=10)
    await ctx.message.delete()


@bot.command(name="роль_мп")
async def set_mp_role(ctx, роль: discord.Role):
    """!роль_мп @роль — настроить роль МП для тега в !vzp и !mp"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    mp_roles[ctx.guild.id] = роль.id
    save_data()
    embed = discord.Embed(
        title="✅ Роль МП настроена",
        description=f"В `!vzp` и `!mp` будет тегаться {роль.mention}",
        color=discord.Color.green(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed, delete_after=10)
    await ctx.message.delete()


@bot.command(name="роль_взп2")
async def set_vzp_role2(ctx, роль: discord.Role):
    """!роль_взп2 @роль — вторая роль для тега в !vzp"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    vzp_roles2[ctx.guild.id] = роль.id
    save_data()
    embed = discord.Embed(
        title="✅ Роль ВЗП-2 настроена",
        description=f"В `!vzp` дополнительно будет тегаться {роль.mention}",
        color=discord.Color.green(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed, delete_after=10)
    await ctx.message.delete()


@bot.command(name="роль_мп2")
async def set_mp_role2(ctx, роль: discord.Role):
    """!роль_мп2 @роль — вторая роль для тега в !vzp и !mp"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    mp_roles2[ctx.guild.id] = роль.id
    save_data()
    embed = discord.Embed(
        title="✅ Роль МП-2 настроена",
        description=f"В `!vzp` и `!mp` дополнительно будет тегаться {роль.mention}",
        color=discord.Color.green(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed, delete_after=10)
    await ctx.message.delete()


@bot.command(name="роль_реаки2")
async def set_list_role2(ctx, роль: discord.Role):
    """!роль_реаки2 @роль — вторая роль для тега в !list"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    list_roles2[ctx.guild.id] = роль.id
    save_data()
    embed = discord.Embed(
        title="✅ Роль Реаки-2 настроена",
        description=f"В `!list` дополнительно будет тегаться {роль.mention}",
        color=discord.Color.green(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed, delete_after=10)
    await ctx.message.delete()


@tree.command(name="доступ_сбора", description="Добавить роль с доступом к команде сбора")
@app_commands.describe(
    тип="Тип сбора: vzp, mp или list",
    роль="Роль, которая получит доступ к команде"
)
@app_commands.choices(тип=[
    app_commands.Choice(name="vzp", value="vzp"),
    app_commands.Choice(name="mp", value="mp"),
    app_commands.Choice(name="list", value="list"),
])
async def slash_event_access_add(interaction: discord.Interaction, тип: str, роль: discord.Role):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid = interaction.guild_id
    if gid not in event_command_roles:
        event_command_roles[gid] = {}
    if тип not in event_command_roles[gid]:
        event_command_roles[gid][тип] = []
    if роль.id not in event_command_roles[gid][тип]:
        event_command_roles[gid][тип].append(роль.id)
    save_data()
    roles_list = ", ".join(f"<@&{rid}>" for rid in event_command_roles[gid][тип])
    await interaction.response.send_message(
        f"✅ {роль.mention} теперь может использовать `!{тип}`\nВсе роли с доступом: {roles_list}",
        ephemeral=True,
    )


@tree.command(name="убрать_доступ_сбора", description="Убрать роль из доступа к команде сбора")
@app_commands.describe(
    тип="Тип сбора: vzp, mp или list",
    роль="Роль, которую убрать"
)
@app_commands.choices(тип=[
    app_commands.Choice(name="vzp", value="vzp"),
    app_commands.Choice(name="mp", value="mp"),
    app_commands.Choice(name="list", value="list"),
])
async def slash_event_access_remove(interaction: discord.Interaction, тип: str, роль: discord.Role):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid = interaction.guild_id
    allowed = event_command_roles.get(gid, {}).get(тип, [])
    if роль.id not in allowed:
        return await interaction.response.send_message(f"❌ {роль.mention} и так не в списке для `!{тип}`.", ephemeral=True)
    allowed.remove(роль.id)
    save_data()
    await interaction.response.send_message(f"✅ {роль.mention} убрана из доступа к `!{тип}`.", ephemeral=True)


@bot.command(name="list")
async def реаки_cmd(ctx, количество: int = 10, *, название: str = "Реакции"):
    """!list [количество] [название] — сбор на мероприятие (от лица бота)"""
    if not can_run_event(ctx, "list"):
        return await ctx.message.delete()

    image_file = None
    image_ref  = None
    if ctx.message.attachments:
        att = ctx.message.attachments[0]
        try:
            img_bytes  = await att.read()
            ext        = att.filename.rsplit(".", 1)[-1].lower() if "." in att.filename else "png"
            safe_name  = f"event_image.{ext}"
            image_file = discord.File(io.BytesIO(img_bytes), filename=safe_name)
            image_ref  = f"attachment://{safe_name}"
        except Exception:
            pass

    try:
        await ctx.message.delete()
    except Exception:
        pass

    # Парсинг времени из начала названия
    event_time = None
    m = re.match(r'^(\d{1,2}:\d{2})\s*(.*)', название)
    if m:
        event_time = m.group(1)
        название = m.group(2).strip() or "Реакции"

    mentions = []
    for rid in [event_roles.get(ctx.guild.id), list_roles2.get(ctx.guild.id)]:
        if rid:
            r = ctx.guild.get_role(rid)
            if r:
                mentions.append(r.mention)
    content = " ".join(mentions) if mentions else None

    await _create_event_message(ctx.channel, ctx.guild, название, количество, image_file, image_ref, content=content, force_join_mode=True, event_time=event_time, cmd="list")


@bot.command(name="spisok")
async def spisok_cmd(ctx):
    """!spisok — ответом (reply) на своё сообщение собирает список тех, кто поставил реакцию"""
    if not can_run_event(ctx, "list"):
        return await ctx.message.delete()

    ref = ctx.message.reference
    if not ref or not ref.message_id:
        return await ctx.send("❌ Используй эту команду **ответом** (reply) на сообщение с реакциями!", delete_after=8)

    try:
        target = ref.cached_message or await ctx.channel.fetch_message(ref.message_id)
    except Exception:
        return await ctx.send("❌ Не удалось найти исходное сообщение.", delete_after=8)

    try:
        await ctx.message.delete()
    except Exception:
        pass

    users = set()
    for reaction in target.reactions:
        async for user in reaction.users():
            if not user.bot:
                users.add(user.id)

    if not users:
        return await ctx.send("❌ На это сообщение ещё никто не отреагировал.", delete_after=8)

    sorted_ids = sorted(users)
    lines = [f"`{str(i).zfill(2)}.` <@{uid}>" for i, uid in enumerate(sorted_ids, 1)]
    text = "📋 **Список отреагировавших**\n" + "\n".join(lines) + f"\n\n**Всего: {len(sorted_ids)}**"
    await ctx.send(text)


# ─────────────────────────────────────────────
# СЛЭШ-ВЕРСИИ СБОРОВ: /vzp  /mp  /list
# ─────────────────────────────────────────────
def _can_run_event_slash(interaction, event_type):
    if is_admin(interaction):
        return True
    allowed = event_command_roles.get(interaction.guild_id, {}).get(event_type, [])
    return any(r.id in allowed for r in interaction.user.roles)


@tree.command(name="vzp", description="Создать сбор ВЗП")
@app_commands.describe(
    количество="Максимум участников (по умолчанию 10)",
    название="Название сбора (по умолчанию ВЗП)",
    время="Время сбора (например 20:00)",
    картинка="Прикрепить изображение к сбору",
)
async def slash_vzp(
    interaction: discord.Interaction,
    количество: app_commands.Range[int, 1] = 10,
    название: str = "ВЗП",
    время: str = None,
    картинка: discord.Attachment = None,
):
    if not _can_run_event_slash(interaction, "vzp"):
        return await interaction.response.send_message("❌ Нет доступа к этой команде.", ephemeral=True)
    await interaction.response.defer()
    image_file, image_ref = None, None
    if картинка:
        try:
            img_bytes = await картинка.read()
            ext = картинка.filename.rsplit(".", 1)[-1].lower() if "." in картинка.filename else "png"
            safe_name = f"event_image.{ext}"
            image_file = discord.File(io.BytesIO(img_bytes), filename=safe_name)
            image_ref = f"attachment://{safe_name}"
        except Exception:
            pass
    mentions = []
    for rid in [vzp_roles.get(interaction.guild_id), vzp_roles2.get(interaction.guild_id), mp_roles.get(interaction.guild_id), mp_roles2.get(interaction.guild_id)]:
        if rid:
            r = interaction.guild.get_role(rid)
            if r:
                mentions.append(r.mention)
    content = " ".join(mentions) if mentions else None
    await interaction.delete_original_response()
    await _create_event_message(interaction.channel, interaction.guild, название, количество, image_file, image_ref, content=content, event_time=время, cmd="vzp")


@tree.command(name="mp", description="Создать сбор МП")
@app_commands.describe(
    количество="Максимум участников (по умолчанию 10)",
    название="Название сбора (по умолчанию МП)",
    время="Время сбора (например 20:00)",
    картинка="Прикрепить изображение к сбору",
)
async def slash_mp(
    interaction: discord.Interaction,
    количество: app_commands.Range[int, 1] = 10,
    название: str = "МП",
    время: str = None,
    картинка: discord.Attachment = None,
):
    if not _can_run_event_slash(interaction, "mp"):
        return await interaction.response.send_message("❌ Нет доступа к этой команде.", ephemeral=True)
    await interaction.response.defer()
    image_file, image_ref = None, None
    if картинка:
        try:
            img_bytes = await картинка.read()
            ext = картинка.filename.rsplit(".", 1)[-1].lower() if "." in картинка.filename else "png"
            safe_name = f"event_image.{ext}"
            image_file = discord.File(io.BytesIO(img_bytes), filename=safe_name)
            image_ref = f"attachment://{safe_name}"
        except Exception:
            pass
    mentions = []
    for rid in [mp_roles.get(interaction.guild_id), mp_roles2.get(interaction.guild_id)]:
        if rid:
            r = interaction.guild.get_role(rid)
            if r:
                mentions.append(r.mention)
    content = " ".join(mentions) if mentions else None
    await interaction.delete_original_response()
    await _create_event_message(interaction.channel, interaction.guild, название, количество, image_file, image_ref, content=content, event_time=время, cmd="mp")


@tree.command(name="list", description="Создать сбор на мероприятие (реакции)")
@app_commands.describe(
    количество="Максимум участников (по умолчанию 10)",
    название="Название сбора (по умолчанию Реакции)",
    время="Время сбора (например 20:00)",
    картинка="Прикрепить изображение к сбору",
)
async def slash_list(
    interaction: discord.Interaction,
    количество: app_commands.Range[int, 1] = 10,
    название: str = "Реакции",
    время: str = None,
    картинка: discord.Attachment = None,
):
    if not _can_run_event_slash(interaction, "list"):
        return await interaction.response.send_message("❌ Нет доступа к этой команде.", ephemeral=True)
    await interaction.response.defer()
    image_file, image_ref = None, None
    if картинка:
        try:
            img_bytes = await картинка.read()
            ext = картинка.filename.rsplit(".", 1)[-1].lower() if "." in картинка.filename else "png"
            safe_name = f"event_image.{ext}"
            image_file = discord.File(io.BytesIO(img_bytes), filename=safe_name)
            image_ref = f"attachment://{safe_name}"
        except Exception:
            pass
    mentions = []
    for rid in [event_roles.get(interaction.guild_id), list_roles2.get(interaction.guild_id)]:
        if rid:
            r = interaction.guild.get_role(rid)
            if r:
                mentions.append(r.mention)
    content = " ".join(mentions) if mentions else None
    await interaction.delete_original_response()
    await _create_event_message(interaction.channel, interaction.guild, название, количество, image_file, image_ref, content=content, force_join_mode=True, event_time=время, cmd="list")


@bot.command(name="афк")
async def create_afk(ctx):
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    """!афк — создать панель АФК в этом канале"""
    guild_id = ctx.guild.id
    if guild_id not in afk_list:
        afk_list[guild_id] = {}

    view  = AfkView()
    embed = build_afk_embed(guild_id)
    msg   = await ctx.send(embed=embed, view=view)
    afk_panels[guild_id] = {"message_id": msg.id, "channel_id": ctx.channel.id}
    save_data()
    await ctx.message.delete()


@bot.command(name="инактив")
async def create_inactive(ctx):
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    """!инактив — создать панель инактива в этом канале"""
    guild_id = ctx.guild.id
    if guild_id not in inactive_list:
        inactive_list[guild_id] = {}

    view  = InactiveView()
    embed = build_inactive_embed(guild_id)
    msg   = await ctx.send(embed=embed, view=view)
    inactive_panels[guild_id] = {"message_id": msg.id, "channel_id": ctx.channel.id}
    save_data()
    await ctx.message.delete()


@tree.command(name="афк_снять", description="Убрать пользователя из АФК-списка (админ)")
@app_commands.describe(пользователь="Кого убрать из списка АФК")
async def slash_afk_remove(interaction: discord.Interaction, пользователь: discord.Member):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    guild_id = interaction.guild_id
    if guild_id not in afk_list or пользователь.id not in afk_list[guild_id]:
        return await interaction.response.send_message(f"⚠️ {пользователь.mention} не в АФК-списке.", ephemeral=True)
    del afk_list[guild_id][пользователь.id]
    save_data()
    await refresh_afk_message(interaction.guild)
    await interaction.response.send_message(f"✅ {пользователь.mention} убран(а) из АФК-списка.", ephemeral=True)


@tree.command(name="афк_очистить", description="Очистить весь АФК-список (админ)")
async def slash_afk_clear(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    guild_id = interaction.guild_id
    count = len(afk_list.get(guild_id, {}))
    afk_list[guild_id] = {}
    save_data()
    await refresh_afk_message(interaction.guild)
    await interaction.response.send_message(f"✅ АФК-список очищен ({count} {declension(count)} убрано).", ephemeral=True)


@tree.command(name="инактив_снять", description="Убрать пользователя из списка инактива (админ)")
@app_commands.describe(пользователь="Кого убрать из списка инактива")
async def slash_inactive_remove(interaction: discord.Interaction, пользователь: discord.Member):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    guild_id = interaction.guild_id
    if guild_id not in inactive_list or пользователь.id not in inactive_list[guild_id]:
        return await interaction.response.send_message(f"⚠️ {пользователь.mention} не в списке инактива.", ephemeral=True)
    del inactive_list[guild_id][пользователь.id]
    save_data()
    await refresh_inactive_message(interaction.guild)
    await interaction.response.send_message(f"✅ {пользователь.mention} убран(а) из инактива.", ephemeral=True)


@tree.command(name="инактив_очистить", description="Очистить весь список инактива (админ)")
async def slash_inactive_clear(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    guild_id = interaction.guild_id
    count = len(inactive_list.get(guild_id, {}))
    inactive_list[guild_id] = {}
    save_data()
    await refresh_inactive_message(interaction.guild)
    await interaction.response.send_message(f"✅ Список инактива очищен ({count} {declension(count)} убрано).", ephemeral=True)


@tree.command(name="тикет", description="Создать панель заявок")
@app_commands.describe(
    канал_панели="Канал, куда отправить кнопку заявки",
    категория="Категория, где будут создаваться каналы-тикеты",
)
async def slash_ticket(interaction: discord.Interaction, канал_панели: discord.TextChannel, категория: discord.CategoryChannel):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    tt = ticket_texts.get(interaction.guild_id, {})
    embed = discord.Embed(
        title=tt.get("title", DEFAULT_TICKET_TITLE),
        description=tt.get("desc", DEFAULT_TICKET_DESC),
        color=discord.Color.red(),
    )
    img = tt.get("image", DEFAULT_TICKET_IMAGE)
    if img:
        embed.set_image(url=img)
    embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))

    view = TicketPanelView(категория.id)
    msg  = await канал_панели.send(embed=embed, view=view)

    ticket_panels[interaction.guild_id] = {
        "panel_channel_id": канал_панели.id,
        "category_id":      категория.id,
        "message_id":       msg.id,
    }
    save_data()

    await interaction.response.send_message(
        f"✅ Панель отправлена в {канал_панели.mention}. Тикеты будут создаваться в **{категория.name}**",
        ephemeral=True,
    )


@tree.command(name="тикет_менеджер", description="Назначить роль тикет-менеджера")
@app_commands.describe(роль="Роль, которая тегается в тикетах и может их рассматривать")
async def slash_ticket_manager(interaction: discord.Interaction, роль: discord.Role):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    ticket_manager_roles[interaction.guild_id] = роль.id
    save_data()
    await interaction.response.send_message(
        f"✅ Роль тикет-менеджера установлена: {роль.mention}",
        ephemeral=True,
    )


@tree.command(name="тикет_доступ", description="Добавить роль с доступом к тикетам (видит канал)")
@app_commands.describe(роль="Роль, которая будет видеть канал тикета")
async def slash_ticket_viewer_add(interaction: discord.Interaction, роль: discord.Role):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid = interaction.guild_id
    if gid not in ticket_viewer_roles:
        ticket_viewer_roles[gid] = []
    if роль.id not in ticket_viewer_roles[gid]:
        ticket_viewer_roles[gid].append(роль.id)
    save_data()
    roles_list = ", ".join(f"<@&{rid}>" for rid in ticket_viewer_roles[gid])
    await interaction.response.send_message(
        f"✅ {роль.mention} добавлена к тикетам.\nВсе роли с доступом: {roles_list}",
        ephemeral=True,
    )


@tree.command(name="тикет_доступ_убрать", description="Убрать роль из доступа к тикетам")
@app_commands.describe(роль="Роль, которую нужно убрать")
async def slash_ticket_viewer_remove(interaction: discord.Interaction, роль: discord.Role):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid = interaction.guild_id
    viewers = ticket_viewer_roles.get(gid, [])
    if роль.id not in viewers:
        return await interaction.response.send_message(f"❌ {роль.mention} и так не в списке доступа.", ephemeral=True)
    viewers.remove(роль.id)
    ticket_viewer_roles[gid] = viewers
    save_data()
    await interaction.response.send_message(f"✅ {роль.mention} убрана из доступа к тикетам.", ephemeral=True)


@tree.command(name="канал_обзвона", description="Установить голосовой канал для приглашений на обзвон")
@app_commands.describe(канал="Голосовой канал для обзвонов")
async def slash_interview_channel(interaction: discord.Interaction, канал: discord.VoiceChannel):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    interview_channels[interaction.guild_id] = канал.id
    save_data()
    await interaction.response.send_message(f"✅ Канал для обзвонов установлен: {канал.mention}", ephemeral=True)
@app_commands.describe(роль="Роль для тега (если не задана — тегается тикет-менеджер)")
async def slash_ticket_ping(interaction: discord.Interaction, роль: discord.Role):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    ticket_ping_role[interaction.guild_id] = роль.id
    save_data()
    await interaction.response.send_message(
        f"✅ В тикетах будет тегаться: {роль.mention}",
        ephemeral=True,
    )


class TicketTextModal(ui.Modal, title="✏️ Текст панели заявок"):
    title_input = ui.TextInput(
        label="Заголовок",
        default="📋 Вступление в MORIARTY",
        max_length=256,
        required=True,
    )
    desc_input = ui.TextInput(
        label="Описание",
        style=discord.TextStyle.paragraph,
        max_length=4000,
        required=True,
    )
    image_input = ui.TextInput(
        label="Ссылка на картинку (оставь пустым — без картинки)",
        required=False,
        placeholder="https://...",
    )

    def __init__(self, guild_id: int):
        super().__init__()
        tt = ticket_texts.get(guild_id, {})
        self.title_input.default = tt.get("title", DEFAULT_TICKET_TITLE)
        self.desc_input.default  = tt.get("desc",  DEFAULT_TICKET_DESC)
        self.image_input.default = tt.get("image", DEFAULT_TICKET_IMAGE)
        self._guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        ticket_texts[self._guild_id] = {
            "title": str(self.title_input),
            "desc":  str(self.desc_input),
            "image": str(self.image_input).strip(),
        }
        save_data()

        # Обновить существующую панель если есть
        panel = ticket_panels.get(self._guild_id, {})
        if panel.get("message_id") and panel.get("panel_channel_id"):
            try:
                ch  = interaction.guild.get_channel(panel["panel_channel_id"])
                msg = await ch.fetch_message(panel["message_id"])
                tt  = ticket_texts[self._guild_id]
                embed = discord.Embed(title=tt["title"], description=tt["desc"], color=discord.Color.red())
                if tt["image"]:
                    embed.set_image(url=tt["image"])
                embed.set_footer(text="MORIARTY", icon_url=_footer(self._guild_id))
                await msg.edit(embed=embed)
            except Exception:
                pass

        await interaction.response.send_message("✅ Текст панели обновлён!", ephemeral=True)


@tree.command(name="тикет_текст", description="Изменить заголовок и текст панели заявок")
async def slash_ticket_text(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    await interaction.response.send_modal(TicketTextModal(interaction.guild_id))


@tree.command(name="лог_отказов", description="Настроить канал для логов одобрений и отказов по заявкам")
@app_commands.describe(канал="Канал, куда будут дублироваться отказы")
async def slash_reject_log(interaction: discord.Interaction, канал: discord.TextChannel):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    reject_log_channels[interaction.guild_id] = канал.id
    save_data()
    await interaction.response.send_message(
        f"✅ Отказы по заявкам теперь дублируются в {канал.mention}",
        ephemeral=True,
    )


# ─────────────────────────────────────────────
# 💰 БАЛЛЫ, WARN, МАГАЗИН — PREFIX-КОМАНДЫ
# ─────────────────────────────────────────────
@bot.command(name="баланс")
async def balance(ctx, пользователь: discord.Member = None):
    """!баланс [@пользователь] — показать баланс"""
    target = пользователь or ctx.author
    embed  = build_points_embed(ctx.guild.id, target.id)
    embed.set_author(name=target.display_name, icon_url=target.display_avatar.url)
    await ctx.send(embed=embed)


@bot.command(name="обмен")
async def exchange_cmd(ctx, количество: int = None):
    """!обмен <количество> — обменять алмазы на фишки 1:1"""
    if not количество or количество < 1:
        embed = discord.Embed(
            title="💱 Обмен алмазов на фишки",
            description=(
                "**Формат:** `!обмен <количество>`\n\n"
                "💎 Алмазы → 🎰 Фишки по курсу **1:1**\n"
                "Фишки используются только в рулетке.\n\n"
                "**Пример:** `!обмен 500`"
            ),
            color=discord.Color.gold(),
        )
        embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
        return await ctx.send(embed=embed)

    gid = ctx.guild.id
    uid = ctx.author.id
    diamonds = get_points(gid, uid)

    if diamonds < количество:
        return await ctx.send(
            f"❌ Недостаточно алмазов! Нужно **{_fmt(количество)}** 💎, у вас **{_fmt(diamonds)}** 💎",
            delete_after=8,
        )

    add_points(gid, uid, -количество)
    add_chips(gid, uid, количество)

    embed = discord.Embed(
        title="💱 Обмен выполнен!",
        description=(
            f"**{_fmt(количество)}** 💎 → **{_fmt(количество)}** 🎰\n\n"
            f"Алмазы: **{_fmt(get_points(gid, uid))}** 💎\n"
            f"Фишки: **{_fmt(get_chips(gid, uid))}** 🎰"
        ),
        color=discord.Color.green(),
        timestamp=datetime.now(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(gid))
    await ctx.send(embed=embed)


@tree.command(name="магазин", description="Развернуть панель магазина в этом канале")
async def slash_shop(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid  = interaction.guild_id
    embed = build_shop_embed(gid)
    view  = ShopView(gid)
    msg   = await interaction.channel.send(embed=embed, view=view)
    shop_panels[gid] = {"channel_id": interaction.channel_id, "message_id": msg.id}
    save_data()
    await interaction.response.send_message("✅ Панель магазина развёрнута!", ephemeral=True)


class AddItemModal(ui.Modal, title="🛒 Добавить товар"):
    name        = ui.TextInput(label="Название товара", placeholder="Снять варн", required=True)
    price       = ui.TextInput(label="Цена (баллы)", placeholder="500", required=True)
    emoji       = ui.TextInput(label="Эмодзи", placeholder="⚠️", required=False, max_length=8)
    description = ui.TextInput(label="Описание", placeholder="Снимает один варн", required=False)
    action      = ui.TextInput(
        label="Действие: remove_warn / give_role / notify",
        placeholder="notify",
        required=True,
        max_length=50,
    )

    async def on_submit(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        try:
            price_val = int(str(self.price).strip())
        except ValueError:
            return await interaction.response.send_message("❌ Цена должна быть числом.", ephemeral=True)

        action_val = str(self.action).strip().lower()
        if action_val not in ("remove_warn", "give_role", "notify"):
            return await interaction.response.send_message(
                "❌ Действие должно быть: `remove_warn`, `give_role` или `notify`", ephemeral=True
            )

        if gid not in guild_shop_items:
            guild_shop_items[gid] = {}

        import time
        item_id = str(int(time.time()))
        guild_shop_items[gid][item_id] = {
            "name":        str(self.name).strip(),
            "price":       price_val,
            "emoji":       str(self.emoji).strip() or "🛒",
            "description": str(self.description).strip(),
            "action":      action_val,
            "role_id":     None,
        }
        save_data()
        await refresh_shop_message(interaction.guild)
        await interaction.response.send_message(
            f"✅ Товар **{self.name}** добавлен!\n"
            f"Если действие `give_role` — используй `/товар_роль {item_id} @роль` для привязки роли.",
            ephemeral=True,
        )


@tree.command(name="добавить_товар", description="Добавить товар в магазин")
async def slash_add_item(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    await interaction.response.send_modal(AddItemModal())


@tree.command(name="убрать_товар", description="Удалить товар из магазина")
@app_commands.describe(товар_id="ID товара (виден в /список_товаров)")
async def slash_remove_item(interaction: discord.Interaction, товар_id: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid   = interaction.guild_id
    items = guild_shop_items.get(gid, {})
    if товар_id not in items:
        return await interaction.response.send_message("❌ Товар не найден.", ephemeral=True)
    name = items[товар_id]["name"]
    del guild_shop_items[gid][товар_id]
    save_data()
    await refresh_shop_message(interaction.guild)
    await interaction.response.send_message(f"✅ Товар **{name}** удалён.", ephemeral=True)


@tree.command(name="список_товаров", description="Список всех товаров в магазине (с ID)")
async def slash_list_items(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid   = interaction.guild_id
    items = guild_shop_items.get(gid, {})
    if not items:
        return await interaction.response.send_message("Магазин пуст.", ephemeral=True)
    lines = [f"`{iid}` — {i['emoji']} **{i['name']}** | {i['price']} 💎 | `{i['action']}`" for iid, i in items.items()]
    embed = discord.Embed(title="🛒 Товары магазина", description="\n".join(lines), color=discord.Color.gold())
    embed.set_footer(text="Используй ID для /убрать_товар или /товар_роль")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="товар_роль", description="Привязать роль к товару с действием give_role")
@app_commands.describe(товар_id="ID товара", роль="Роль которая выдаётся при покупке")
async def slash_item_role(interaction: discord.Interaction, товар_id: str, роль: discord.Role):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid   = interaction.guild_id
    items = guild_shop_items.get(gid, {})
    if товар_id not in items:
        return await interaction.response.send_message("❌ Товар не найден.", ephemeral=True)
    if items[товар_id]["action"] != "give_role":
        return await interaction.response.send_message("❌ Действие товара не `give_role`.", ephemeral=True)
    guild_shop_items[gid][товар_id]["role_id"] = роль.id
    save_data()
    await interaction.response.send_message(f"✅ Роль {роль.mention} привязана к товару **{items[товар_id]['name']}**.", ephemeral=True)


@tree.command(name="лог_магазина", description="Канал, куда пишутся все покупки из магазина")
@app_commands.describe(канал="Текстовый канал для логов покупок")
async def slash_shop_log(interaction: discord.Interaction, канал: discord.TextChannel):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    shop_log_channels[interaction.guild_id] = канал.id
    save_data()
    await interaction.response.send_message(f"✅ Логи покупок будут отправляться в {канал.mention}.", ephemeral=True)


@tree.command(name="роль_магазина", description="Роль, которая тегается при покупках с ручной выдачей (notify)")
@app_commands.describe(роль="Роль ответственного за выдачу товаров")
async def slash_shop_manager_role(interaction: discord.Interaction, роль: discord.Role):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    shop_manager_roles[interaction.guild_id] = роль.id
    save_data()
    await interaction.response.send_message(f"✅ Роль {роль.mention} будет тегаться при покупках с ручной выдачей.", ephemeral=True)


@bot.command(name="дать")
async def give_points_cmd(ctx, пользователь: discord.Member, количество: int):
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    """!дать @пользователь количество — выдать баллы"""
    if количество < 1:
        return await ctx.send("❌ Количество должно быть больше 0!", delete_after=5)
    add_points(ctx.guild.id, пользователь.id, количество)
    new_balance = get_points(ctx.guild.id, пользователь.id)
    embed = discord.Embed(
        title="💰 Баллы начислены!",
        description=f"{пользователь.mention} получил **{количество}** 💎\nНовый баланс: **{new_balance}** 💎",
        color=discord.Color.green(),
        timestamp=datetime.now(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed)


@bot.command(name="снять")
async def remove_points_cmd(ctx, пользователь: discord.Member, количество: int):
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    """!снять @пользователь количество — снять баллы"""
    if количество < 1:
        return await ctx.send("❌ Количество должно быть больше 0!", delete_after=5)
    current     = get_points(ctx.guild.id, пользователь.id)
    new_balance = max(0, current - количество)
    set_points(ctx.guild.id, пользователь.id, new_balance)
    embed = discord.Embed(
        title="💰 Баллы сняты!",
        description=f"У {пользователь.mention} снято **{количество}** 💎\nНовый баланс: **{new_balance}** 💎",
        color=discord.Color.orange(),
        timestamp=datetime.now(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed)


@bot.command(name="warn")
async def warn_user(ctx, пользователь: discord.Member, количество: int, *, причина: str):
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    """!warn @пользователь <1-3> причина — выдать варн"""
    if количество not in (1, 2, 3):
        return await ctx.send("❌ Укажи количество варнов: 1, 2 или 3. Пример: `!warn @user 2 причина`", delete_after=6)

    set_warn(ctx.guild.id, пользователь.id, количество, причина, ctx.author.id)

    # Убрать все старые варн-роли и назначить новую
    guild_warn_roles = warn_roles.get(ctx.guild.id, {})
    roles_to_remove = [ctx.guild.get_role(rid) for rid in guild_warn_roles.values() if ctx.guild.get_role(rid)]
    new_role = ctx.guild.get_role(guild_warn_roles.get(количество))
    try:
        await пользователь.remove_roles(*[r for r in roles_to_remove if r], reason="Обновление варн-роли")
        if new_role:
            await пользователь.add_roles(new_role, reason=f"Warn {количество}/3")
    except Exception:
        pass

    embed = discord.Embed(
        title="⚠️ WARN",
        description=f"{пользователь.mention} получил warn!",
        color=discord.Color.red(),
        timestamp=datetime.now(),
    )
    embed.add_field(name="Причина", value=причина, inline=False)
    embed.add_field(name="Модератор", value=ctx.author.mention, inline=True)
    embed.add_field(name="Варны", value=f"**{количество}/3**", inline=True)
    if new_role:
        embed.add_field(name="Роль", value=new_role.mention, inline=True)
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed)

    try:
        dm_embed = discord.Embed(
            title="⚠️ Вы получили warn",
            description=f"**Причина:** {причина}\n**Варны:** {количество}/3",
            color=discord.Color.red(),
            timestamp=datetime.now(),
        )
        dm_embed.add_field(name="Модератор", value=ctx.author.mention)
        dm_embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
        await пользователь.send(embed=dm_embed)
    except Exception:
        pass

    try:
        await ctx.message.delete()
    except Exception:
        pass


@bot.command(name="unwarn")
async def admin_remove_warn(ctx, пользователь: discord.Member):
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    """!снять_варн @пользователь — снять варн"""
    if remove_warn(ctx.guild.id, пользователь.id):
        # Убрать все варн-роли
        guild_warn_roles = warn_roles.get(ctx.guild.id, {})
        roles_to_remove = [ctx.guild.get_role(rid) for rid in guild_warn_roles.values() if ctx.guild.get_role(rid)]
        try:
            await пользователь.remove_roles(*[r for r in roles_to_remove if r], reason="Снятие варна")
        except Exception:
            pass
        embed = discord.Embed(
            title="✅ Warn снят",
            description=f"У {пользователь.mention} снят warn",
            color=discord.Color.green(),
            timestamp=datetime.now(),
        )
        embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
        await ctx.send(embed=embed)
    else:
        await ctx.send("❌ У пользователя нет warn'ов!", delete_after=5)

    try:
        await ctx.message.delete()
    except Exception:
        pass


@bot.command(name="роль_варн")
async def set_warn_role(ctx, номер: int, роль: discord.Role):
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    """!роль_варн <1-3> @роль — привязать роль к уровню варна"""
    if номер not in (1, 2, 3):
        return await ctx.send("❌ Укажи номер 1, 2 или 3. Пример: `!роль_варн 1 @Варн1/3`", delete_after=6)
    if ctx.guild.id not in warn_roles:
        warn_roles[ctx.guild.id] = {}
    warn_roles[ctx.guild.id][номер] = роль.id
    save_data()
    embed = discord.Embed(
        title="✅ Варн-роль настроена",
        description=f"Варн **{номер}/3** → {роль.mention}",
        color=discord.Color.green(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed)
    await ctx.message.delete()


@bot.command(name="замена")
async def замена_cmd(ctx, кого: int, на_кого: int = 0):
    """!замена <айди_кого> <айди_на_кого> — заменить участника в слоте (0 = убрать). Используется в треде сбора."""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()

    # Найти сбор по thread_id
    thread_id = ctx.channel.id
    msg_id = None
    for mid, data in event_lists.items():
        if data.get("thread_id") == thread_id:
            msg_id = mid
            break

    if msg_id is None:
        return await ctx.send("❌ Эта команда используется только в треде сбора!", delete_after=6)

    data  = event_lists[msg_id]
    slots = data["slots"]

    # Найти слот кого
    target_slot = None
    for slot_num, uid in slots.items():
        if uid == кого:
            target_slot = slot_num
            break

    if target_slot is None:
        return await ctx.send(f"❌ Пользователь `{кого}` не найден ни в одном слоте!", delete_after=6)

    # Если на_кого уже занимает другой слот — освободить его
    if на_кого:
        for slot_num, uid in slots.items():
            if uid == на_кого:
                slots[slot_num] = None
                break

    slots[target_slot] = на_кого if на_кого else None

    # Обновить эмбед
    try:
        channel = bot.get_channel(data["channel_id"])
        msg = await channel.fetch_message(msg_id)
        join_mode = data.get("mode") == "join"
        embed = build_event_embed(ctx.guild.id, data["title"], data["max"], slots, data.get("image_url"), data.get("note"), join_mode=join_mode, event_time=data.get("event_time"), closed=data.get("closed", False), reserve=data.get("reserve", []))
        view = JoinEventView(msg_id) if join_mode else EventView(msg_id)
        await msg.edit(embed=embed, view=view)
    except Exception:
        pass

    await update_thread_list(msg_id)

    if на_кого:
        await ctx.send(f"✅ Слот **{target_slot}**: <@{кого}> → <@{на_кого}>", delete_after=10)
    else:
        await ctx.send(f"✅ Слот **{target_slot}**: <@{кого}> убран", delete_after=10)
    await ctx.message.delete()


@bot.command(name="роль_админ")
async def set_admin_role(ctx, роль: discord.Role):
    """!роль_админ @роль — установить роль администратора бота (требует Discord-администратора)"""
    if not ctx.author.guild_permissions.administrator:
        return await ctx.message.delete()
    admin_roles[ctx.guild.id] = роль.id
    save_data()
    embed = discord.Embed(
        title="✅ Роль администратора установлена",
        description=(
            f"Теперь все команды бота доступны для {роль.mention}.\n\n"
            f"Следующий шаг — настрой остальные роли и панели:\n"
            f"`!роль_взп` `!роль_мп` `!роль_реаки` `!роль_варн`\n"
            f"`/тикет` `/тикет_менеджер` `/магазин` `/настройки`"
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed, delete_after=30)
    await ctx.message.delete()


@bot.command(name="роль_админ_добавить")
async def add_extra_admin_role(ctx, роль: discord.Role):
    """!роль_админ_добавить @роль — добавить ещё одну роль, которая может управлять ботом наравне с основной"""
    if not ctx.author.guild_permissions.administrator:
        return await ctx.message.delete()
    roles = extra_admin_roles.setdefault(ctx.guild.id, [])
    if роль.id in roles or роль.id == admin_roles.get(ctx.guild.id):
        embed = discord.Embed(description=f"⚠️ {роль.mention} уже управляет ботом.", color=discord.Color.orange())
    else:
        roles.append(роль.id)
        save_data()
        embed = discord.Embed(
            title="✅ Роль добавлена",
            description=f"{роль.mention} теперь тоже может управлять ботом наравне с основной админ-ролью.",
            color=discord.Color.green(),
        )
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed, delete_after=15)
    await ctx.message.delete()


@bot.command(name="роль_админ_убрать")
async def remove_extra_admin_role(ctx, роль: discord.Role):
    """!роль_админ_убрать @роль — убрать роль из списка дополнительных админ-ролей"""
    if not ctx.author.guild_permissions.administrator:
        return await ctx.message.delete()
    roles = extra_admin_roles.get(ctx.guild.id, [])
    if роль.id not in roles:
        embed = discord.Embed(description=f"⚠️ {роль.mention} и так не в списке дополнительных админ-ролей.", color=discord.Color.orange())
    else:
        roles.remove(роль.id)
        save_data()
        embed = discord.Embed(
            title="✅ Роль убрана",
            description=f"{роль.mention} больше не управляет ботом.",
            color=discord.Color.green(),
        )
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed, delete_after=15)
    await ctx.message.delete()


@tree.command(name="настройки", description="Показать текущую конфигурацию бота на этом сервере")
async def slash_settings(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)

    g = interaction.guild

    def role_str(role_id):
        if not role_id:
            return "⚠️ *не задана*"
        r = g.get_role(role_id)
        return r.mention if r else f"⚠️ *удалена (ID: {role_id})*"

    def channel_str(ch_id):
        if not ch_id:
            return "⚠️ *не задан*"
        c = g.get_channel(ch_id)
        return c.mention if c else f"⚠️ *удалён (ID: {ch_id})*"

    gid = g.id
    wr  = warn_roles.get(gid, {})
    tp  = ticket_panels.get(gid, {})
    extra_admins = extra_admin_roles.get(gid, [])
    extra_admins_str = ", ".join(f"<@&{rid}>" for rid in extra_admins) if extra_admins else "*нет*"

    embed = discord.Embed(
        title="⚙️ Конфигурация бота",
        color=discord.Color.blurple(),
        timestamp=datetime.now(),
    )
    embed.add_field(
        name="🔑 Роли",
        value=(
            f"Администратор: {role_str(admin_roles.get(gid))}\n"
            f"Доп. админ-роли: {extra_admins_str}\n"
            f"Тикет-менеджер: {role_str(ticket_manager_roles.get(gid))}\n"
            f"Роль ВЗП: {role_str(vzp_roles.get(gid))}\n"
            f"Роль МП: {role_str(mp_roles.get(gid))}\n"
            f"Роль list: {role_str(event_roles.get(gid))}\n"
            f"Варн 1/3: {role_str(wr.get(1))}\n"
            f"Варн 2/3: {role_str(wr.get(2))}\n"
            f"Варн 3/3: {role_str(wr.get(3))}"
        ),
        inline=False,
    )
    viewers = ticket_viewer_roles.get(gid, [])
    viewers_str = ", ".join(f"<@&{rid}>" for rid in viewers) if viewers else "⚠️ *не заданы*"
    embed.add_field(
        name="📋 Тикеты",
        value=(
            f"Канал панели: {channel_str(tp.get('panel_channel_id'))}\n"
            f"Тикет-менеджер: {role_str(ticket_manager_roles.get(gid))}\n"
            f"Роль для тега: {role_str(ticket_ping_role.get(gid))}\n"
            f"Роли с доступом: {viewers_str}\n"
            f"Лог отказов: {channel_str(reject_log_channels.get(gid))}\n"
            f"Счётчик тикетов: **{ticket_counters.get(gid, 0)}**"
        ),
        inline=False,
    )
    afk_p = afk_panels.get(gid, {})
    inact_p = inactive_panels.get(gid, {})
    embed.add_field(
        name="📊 Панели",
        value=(
            f"АФК: {channel_str(afk_p.get('channel_id'))}\n"
            f"Инактив: {channel_str(inact_p.get('channel_id'))}"
        ),
        inline=False,
    )
    ecr = event_command_roles.get(gid, {})
    def roles_list_str(type_key):
        ids = ecr.get(type_key, [])
        return ", ".join(f"<@&{rid}>" for rid in ids) if ids else "*только админ*"
    embed.add_field(
        name="🎯 Доступ к сборам",
        value=(
            f"`!vzp`: {roles_list_str('vzp')}\n"
            f"`!mp`: {roles_list_str('mp')}\n"
            f"`!list`: {roles_list_str('list')}"
        ),
        inline=False,
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(gid))
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─────────────────────────────────────────────
# ПАНЕЛЬ НАСТРОЙКИ — /панель_настройки
# ─────────────────────────────────────────────

def _rs(guild: discord.Guild, role_id):
    if not role_id:
        return "⚠️ *не задана*"
    r = guild.get_role(role_id)
    return r.mention if r else "⚠️ *удалена*"

def _cs(guild: discord.Guild, ch_id):
    if not ch_id:
        return "⚠️ *не задан*"
    c = guild.get_channel(ch_id)
    return c.mention if c else "⚠️ *удалён*"

def _roles_list(guild: discord.Guild, ids: list) -> str:
    return "\n".join(f"• <@&{r}>" for r in ids) if ids else "*нет*"

def _channels_list(guild: discord.Guild, ids: list) -> str:
    parts = []
    for cid in ids:
        ch = guild.get_channel(cid)
        parts.append(f"• {ch.mention if ch else f'ID {cid}'}")
    return "\n".join(parts) if parts else "*нет*"


def build_cfg_main_embed(guild: discord.Guild) -> discord.Embed:
    gid = guild.id
    wr  = warn_roles.get(gid, {})
    vs  = voice_reward_settings.get(gid, {})
    fs  = feedback_settings.get(gid) or {}
    cp  = cabinet_panels.get(gid, {})
    op  = obshak_panels.get(gid, {})
    ecr = event_command_roles.get(gid, {})
    viewers = ticket_viewer_roles.get(gid, [])

    def _ecr(t):
        ids = ecr.get(t, [])
        return ", ".join(f"<@&{r}>" for r in ids) if ids else "*только админ*"

    e = discord.Embed(
        title="⚙️ Панель настройки MORIARTY",
        description="Выбери категорию в меню ниже для изменения настроек.",
        color=0x2B2D31,
        timestamp=datetime.now(),
    )
    e.add_field(name="📋 Заявки", value=(
        f"Менеджер: {_rs(guild, ticket_manager_roles.get(gid))}\n"
        f"Пинг: {_rs(guild, ticket_ping_role.get(gid))}\n"
        f"Лог: {_cs(guild, reject_log_channels.get(gid))}\n"
        f"Доступ ({len(viewers)}р.): {', '.join(f'<@&{r}>' for r in viewers) or '*нет*'}"
    ), inline=True)
    e.add_field(name="🔑 Роли", value=(
        f"МП: {_rs(guild, mp_roles.get(gid))}\n"
        f"ВЗП: {_rs(guild, vzp_roles.get(gid))}\n"
        f"Реаки: {_rs(guild, event_roles.get(gid))}\n"
        f"Магазин: {_rs(guild, shop_manager_roles.get(gid))}"
    ), inline=True)
    e.add_field(name="⚠️ Варн", value=(
        f"1/3: {_rs(guild, wr.get(1))}\n"
        f"2/3: {_rs(guild, wr.get(2))}\n"
        f"3/3: {_rs(guild, wr.get(3))}"
    ), inline=True)
    e.add_field(name="📢 Логи", value=(
        f"Заявки: {_cs(guild, reject_log_channels.get(gid))}\n"
        f"Магазин: {_cs(guild, shop_log_channels.get(gid))}\n"
        f"Общак: {_cs(guild, obshak_log_channels.get(gid))}\n"
        f"Feedback: {_cs(guild, fs.get('log_channel_id'))}"
    ), inline=True)
    e.add_field(name="🎯 Сборы", value=(
        f"ВЗП: {_ecr('vzp')}\n"
        f"МП: {_ecr('mp')}\n"
        f"Реаки: {_ecr('list')}"
    ), inline=True)
    e.add_field(name="🔊 Войс / 🖼 Контент", value=(
        f"💎/мин: **{vs.get('amount', 10)}**\n"
        f"Ссылка кабинета: {'✅' if cabinet_invite_links.get(gid) else '⚠️ нет'}\n"
        f"Fb-роль: {_rs(guild, fs.get('ping_role_id'))}"
    ), inline=True)
    e.set_footer(text="MORIARTY • Настройки сервера", icon_url=_footer(guild.id))
    return e


def build_cfg_category_embed(guild: discord.Guild, category: str) -> discord.Embed:
    gid = guild.id
    e = discord.Embed(color=0x2B2D31, timestamp=datetime.now())
    e.set_footer(text="MORIARTY • Настройки сервера", icon_url=_footer(guild.id))

    if category == "tickets":
        viewers = ticket_viewer_roles.get(gid, [])
        e.title = "📋 Заявки"
        e.description = (
            f"**Тикет-менеджер:** {_rs(guild, ticket_manager_roles.get(gid))}\n"
            f"**Пинг-роль:** {_rs(guild, ticket_ping_role.get(gid))}\n"
            f"**Лог канал:** {_cs(guild, reject_log_channels.get(gid))}\n"
            f"**Роли доступа:**\n{_roles_list(guild, viewers)}"
        )
    elif category == "ticket_access":
        viewers = ticket_viewer_roles.get(gid, [])
        e.title = "👥 Роли доступа к тикетам"
        e.description = f"Текущие роли:\n{_roles_list(guild, viewers)}\n\nДобавь или убери роль ниже."
    elif category == "roles":
        e.title = "🔑 Роли системы"
        e.description = (
            f"**МП:** {_rs(guild, mp_roles.get(gid))}\n"
            f"**ВЗП:** {_rs(guild, vzp_roles.get(gid))}\n"
            f"**Реаки:** {_rs(guild, event_roles.get(gid))}\n"
            f"**Магазин (менеджер):** {_rs(guild, shop_manager_roles.get(gid))}"
        )
    elif category == "warns":
        wr = warn_roles.get(gid, {})
        e.title = "⚠️ Варн-роли"
        e.description = (
            f"**1/3:** {_rs(guild, wr.get(1))}\n"
            f"**2/3:** {_rs(guild, wr.get(2))}\n"
            f"**3/3:** {_rs(guild, wr.get(3))}"
        )
    elif category == "logs":
        fs = feedback_settings.get(gid) or {}
        e.title = "📢 Каналы и логи"
        e.description = (
            f"**Лог заявок:** {_cs(guild, reject_log_channels.get(gid))}\n"
            f"**Лог магазина:** {_cs(guild, shop_log_channels.get(gid))}\n"
            f"**Лог общака:** {_cs(guild, obshak_log_channels.get(gid))}\n"
            f"**Feedback канал:** {_cs(guild, fs.get('log_channel_id'))}\n"
            f"**Feedback пинг-роль:** {_rs(guild, fs.get('ping_role_id'))}"
        )
    elif category == "fb_role":
        fs = feedback_settings.get(gid) or {}
        e.title = "🔔 Feedback пинг-роль"
        e.description = f"Текущая: {_rs(guild, fs.get('ping_role_id'))}"
    elif category == "events":
        ecr = event_command_roles.get(gid, {})
        def _ecr(t):
            ids = ecr.get(t, [])
            return "\n".join(f"  • <@&{r}>" for r in ids) if ids else "  *только админ*"
        e.title = "🎯 Доступ к командам сбора"
        e.description = (
            f"**!vzp:**\n{_ecr('vzp')}\n\n"
            f"**!mp:**\n{_ecr('mp')}\n\n"
            f"**!list:**\n{_ecr('list')}"
        )
    elif category in ("event_взп", "event_мп", "event_реаки"):
        etype = category.split("_", 1)[1]
        ecr = event_command_roles.get(gid, {})
        ids = ecr.get(etype, [])
        e.title = f"🎯 Доступ к !{etype}"
        e.description = f"Роли:\n{_roles_list(guild, ids)}\n\nДобавь или убери роль ниже."
    elif category == "voice":
        vs = voice_reward_settings.get(gid, {})
        e.title = "🔊 Голосовые каналы"
        e.description = (
            f"**💎 в минуту:** {vs.get('amount', 10)}\n\n"
            f"**Категории для начисления:**\n{_channels_list(guild, vs.get('categories', []))}\n\n"
            f"**Исключённые каналы:**\n{_channels_list(guild, vs.get('excluded_channels', []))}"
        )
    elif category == "content":
        fs = feedback_settings.get(gid) or {}
        cp = cabinet_panels.get(gid, {})
        op = obshak_panels.get(gid, {})
        link = cabinet_invite_links.get(gid)
        br = guild_branding.get(gid) or {}
        e.title = "🖼 Контент — тексты, фото, ссылки"
        e.description = (
            f"**Личный кабинет**\n"
            f"Текст: {'✅' if cp.get('text') else '⚠️ нет'}  "
            f"Фото: {'✅' if cp.get('image_url') else '⚠️ нет'}  "
            f"Ссылка: {'✅' if link else '⚠️ нет'}\n\n"
            f"**Общак**\n"
            f"Текст: {'✅' if op.get('text') else '⚠️ нет'}  "
            f"Фото: {'✅' if op.get('image_url') else '⚠️ нет'}\n\n"
            f"**Feedback**\n"
            f"Текст: {'✅' if fs.get('text') else '⚠️ нет'}  "
            f"Фото: {'✅' if fs.get('image_url') else '⚠️ нет'}\n\n"
            f"**Брендинг**\n"
            f"Футер иконка: {'✅' if br.get('footer_icon') else '⚠️ по умолчанию'}  "
            f"GIF одобрения: {'✅' if br.get('approve_gif') else '⚠️ по умолчанию'}  "
            f"АФК фото: {'✅' if br.get('afk_image') else '⚠️ по умолчанию'}"
        )
    return e


# ── Главное меню ─────────────────────────────────────────────────────────────

class CfgCategorySelect(ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="📋 Заявки",        value="tickets", description="Менеджер, пинг, лог, доступ, текст"),
            discord.SelectOption(label="🔑 Роли системы",  value="roles",   description="МП, ВЗП, Реаки, Магазин"),
            discord.SelectOption(label="⚠️ Варн-роли",     value="warns",   description="Роли за 1, 2, 3 предупреждения"),
            discord.SelectOption(label="📢 Каналы / Логи", value="logs",    description="Логи и feedback канал/роль"),
            discord.SelectOption(label="🎯 Сборы",          value="events",  description="Доступ к !vzp !mp !list"),
            discord.SelectOption(label="🔊 Голосовые",      value="voice",   description="Баллы, категории, исключения"),
            discord.SelectOption(label="🖼 Контент",        value="content", description="Тексты, фото, ссылки панелей"),
        ]
        super().__init__(placeholder="Выбери категорию настроек…", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        cat   = self.values[0]
        embed = build_cfg_category_embed(interaction.guild, cat)
        view  = _cfg_make_view(interaction.guild, cat)
        await interaction.response.edit_message(embed=embed, view=view)


class CfgMainView(ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(CfgCategorySelect())


# ── Универсальные модали ──────────────────────────────────────────────────────

class VoiceAmountModal(ui.Modal, title="🔊 Баллы за войс в минуту"):
    amount = ui.TextInput(label="Сколько 💎 начислять в минуту", placeholder="10", required=True)

    def __init__(self, orig_message: discord.Message):
        super().__init__()
        self._orig_message = orig_message

    async def on_submit(self, interaction: discord.Interaction):
        try:
            val = int(str(self.amount))
            if val < 1:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message("❌ Введи целое число больше 0.", ephemeral=True)
        s = _get_voice_settings(interaction.guild_id)
        s["amount"] = val
        save_data()
        await interaction.response.send_message(f"✅ Начисление: **{val}** 💎 в минуту.", ephemeral=True)
        embed = build_cfg_category_embed(interaction.guild, "voice")
        await self._orig_message.edit(embed=embed, view=_cfg_make_view(interaction.guild, "voice"))


class _CfgTextModal(ui.Modal):
    """Универсальный модал для изменения текстового значения."""
    def __init__(self, title: str, label: str, default: str,
                 apply_fn, cat_key: str, orig_message: discord.Message,
                 style=discord.TextStyle.short, refresh_fn=None):
        super().__init__(title=title)
        self._apply      = apply_fn
        self._cat_key    = cat_key
        self._orig_msg   = orig_message
        self._refresh_fn = refresh_fn
        self.field = ui.TextInput(label=label, default=default or "", style=style, required=True)
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction):
        val = str(self.field)
        self._apply(interaction.guild_id, val)
        save_data()
        if self._refresh_fn:
            await self._refresh_fn(interaction.guild)
        await interaction.response.send_message("✅ Сохранено!", ephemeral=True)
        embed = build_cfg_category_embed(interaction.guild, self._cat_key)
        await self._orig_msg.edit(embed=embed, view=_cfg_make_view(interaction.guild, self._cat_key))


# ── Базовые пикеры ────────────────────────────────────────────────────────────

class _CfgRolePicker(ui.RoleSelect):
    def __init__(self, apply_fn, cat_key: str, row: int, placeholder: str):
        super().__init__(placeholder=placeholder, row=row)
        self._apply   = apply_fn
        self._cat_key = cat_key

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        self._apply(interaction.guild_id, role.id)
        save_data()
        await interaction.response.send_message(f"✅ Сохранено: {role.mention}", ephemeral=True)
        embed = build_cfg_category_embed(interaction.guild, self._cat_key)
        await interaction.message.edit(embed=embed)


class _CfgChannelPicker(ui.ChannelSelect):
    def __init__(self, apply_fn, cat_key: str, row: int, placeholder: str,
                 channel_types=None):
        super().__init__(
            placeholder=placeholder,
            channel_types=channel_types or [discord.ChannelType.text],
            row=row,
        )
        self._apply   = apply_fn
        self._cat_key = cat_key

    async def callback(self, interaction: discord.Interaction):
        ch = self.values[0]
        self._apply(interaction.guild_id, ch.id)
        save_data()
        await interaction.response.send_message(f"✅ Сохранено: {ch.mention}", ephemeral=True)
        embed = build_cfg_category_embed(interaction.guild, self._cat_key)
        await interaction.message.edit(embed=embed)


class _CfgRoleAddPicker(ui.RoleSelect):
    """Добавить роль в список."""
    def __init__(self, list_fn, cat_key: str, row: int, placeholder: str):
        super().__init__(placeholder=placeholder, row=row)
        self._list_fn = list_fn
        self._cat_key = cat_key

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        lst  = self._list_fn(interaction.guild_id)
        if role.id not in lst:
            lst.append(role.id)
        save_data()
        await interaction.response.send_message(f"✅ Добавлено: {role.mention}", ephemeral=True)
        embed = build_cfg_category_embed(interaction.guild, self._cat_key)
        await interaction.message.edit(embed=embed, view=_cfg_make_view(interaction.guild, self._cat_key))


class _CfgRoleRemoveSelect(ui.Select):
    """Убрать роль из списка."""
    def __init__(self, guild: discord.Guild, role_ids: list, list_fn, cat_key: str, row: int, placeholder: str):
        options = [
            discord.SelectOption(label=(guild.get_role(rid).name if guild.get_role(rid) else f"ID {rid}"), value=str(rid))
            for rid in role_ids
        ] or [discord.SelectOption(label="(список пуст)", value="__empty__")]
        super().__init__(placeholder=placeholder, options=options, disabled=not role_ids, row=row)
        self._list_fn = list_fn
        self._cat_key = cat_key

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "__empty__":
            return await interaction.response.defer()
        rid = int(self.values[0])
        lst = self._list_fn(interaction.guild_id)
        if rid in lst:
            lst.remove(rid)
        save_data()
        await interaction.response.send_message("✅ Убрано.", ephemeral=True)
        embed = build_cfg_category_embed(interaction.guild, self._cat_key)
        await interaction.message.edit(embed=embed, view=_cfg_make_view(interaction.guild, self._cat_key))


class _CfgChannelAddPicker(ui.ChannelSelect):
    """Добавить канал/категорию в список."""
    def __init__(self, list_fn, cat_key: str, row: int, placeholder: str, channel_types=None):
        super().__init__(
            placeholder=placeholder,
            channel_types=channel_types or [discord.ChannelType.category],
            row=row,
        )
        self._list_fn = list_fn
        self._cat_key = cat_key

    async def callback(self, interaction: discord.Interaction):
        ch  = self.values[0]
        lst = self._list_fn(interaction.guild_id)
        if ch.id not in lst:
            lst.append(ch.id)
        save_data()
        await interaction.response.send_message(f"✅ Добавлено: {ch.name}", ephemeral=True)
        embed = build_cfg_category_embed(interaction.guild, self._cat_key)
        await interaction.message.edit(embed=embed, view=_cfg_make_view(interaction.guild, self._cat_key))


class _CfgChannelRemoveSelect(ui.Select):
    """Убрать канал из списка."""
    def __init__(self, guild: discord.Guild, ch_ids: list, list_fn, cat_key: str, row: int, placeholder: str):
        options = [
            discord.SelectOption(label=(guild.get_channel(cid).name if guild.get_channel(cid) else f"ID {cid}"), value=str(cid))
            for cid in ch_ids
        ] or [discord.SelectOption(label="(список пуст)", value="__empty__")]
        super().__init__(placeholder=placeholder, options=options, disabled=not ch_ids, row=row)
        self._list_fn = list_fn
        self._cat_key = cat_key

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "__empty__":
            return await interaction.response.defer()
        cid = int(self.values[0])
        lst = self._list_fn(interaction.guild_id)
        if cid in lst:
            lst.remove(cid)
        save_data()
        await interaction.response.send_message("✅ Убрано.", ephemeral=True)
        embed = build_cfg_category_embed(interaction.guild, self._cat_key)
        await interaction.message.edit(embed=embed, view=_cfg_make_view(interaction.guild, self._cat_key))


def _cfg_btn(label: str, style=discord.ButtonStyle.secondary, row: int = 0):
    """Создать кнопку с колбэком через замыкание."""
    btn = ui.Button(label=label, style=style, row=row)
    return btn


# ── View-классы по категориям ─────────────────────────────────────────────────

class _CfgTicketsView(ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=300)

        back = _cfg_btn("◀ Назад", row=0)
        async def _back(inter): await inter.response.edit_message(embed=build_cfg_main_embed(inter.guild), view=CfgMainView())
        back.callback = _back
        self.add_item(back)

        btn_text = _cfg_btn("✏️ Текст панели", row=0)
        async def _text(inter): await inter.response.send_modal(TicketTextModal(inter.guild_id))
        btn_text.callback = _text
        self.add_item(btn_text)

        btn_access = _cfg_btn("👥 Роли доступа →", row=0)
        async def _access(inter):
            await inter.response.edit_message(
                embed=build_cfg_category_embed(inter.guild, "ticket_access"),
                view=_CfgTicketAccessView(inter.guild),
            )
        btn_access.callback = _access
        self.add_item(btn_access)

        self.add_item(_CfgRolePicker(lambda gid, rid: ticket_manager_roles.__setitem__(gid, rid), "tickets", 1, "🛡 Тикет-менеджер — выбери роль"))
        self.add_item(_CfgRolePicker(lambda gid, rid: ticket_ping_role.__setitem__(gid, rid), "tickets", 2, "🔔 Пинг-роль — выбери роль"))
        self.add_item(_CfgChannelPicker(lambda gid, cid: reject_log_channels.__setitem__(gid, cid), "tickets", 3, "📢 Лог канал — выбери канал"))


class _CfgTicketAccessView(ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=300)
        gid = guild.id

        back = _cfg_btn("◀ Назад к заявкам", row=0)
        async def _back(inter):
            await inter.response.edit_message(
                embed=build_cfg_category_embed(inter.guild, "tickets"),
                view=_CfgTicketsView(inter.guild),
            )
        back.callback = _back
        self.add_item(back)

        def get_list(gid_): return ticket_viewer_roles.setdefault(gid_, [])
        self.add_item(_CfgRoleAddPicker(get_list, "ticket_access", 1, "➕ Добавить роль доступа"))
        self.add_item(_CfgRoleRemoveSelect(guild, ticket_viewer_roles.get(gid, []), get_list, "ticket_access", 2, "➖ Убрать роль доступа"))


class _CfgRolesView(ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        back = _cfg_btn("◀ Назад", row=0)
        async def _back(inter): await inter.response.edit_message(embed=build_cfg_main_embed(inter.guild), view=CfgMainView())
        back.callback = _back
        self.add_item(back)
        self.add_item(_CfgRolePicker(lambda gid, rid: mp_roles.__setitem__(gid, rid), "roles", 1, "🏎 МП — выбери роль"))
        self.add_item(_CfgRolePicker(lambda gid, rid: vzp_roles.__setitem__(gid, rid), "roles", 2, "⚔️ ВЗП — выбери роль"))
        self.add_item(_CfgRolePicker(lambda gid, rid: event_roles.__setitem__(gid, rid), "roles", 3, "🎯 Реаки — выбери роль"))
        self.add_item(_CfgRolePicker(lambda gid, rid: shop_manager_roles.__setitem__(gid, rid), "roles", 4, "🛍 Магазин — выбери роль"))


class _CfgWarnsView(ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        back = _cfg_btn("◀ Назад", row=0)
        async def _back(inter): await inter.response.edit_message(embed=build_cfg_main_embed(inter.guild), view=CfgMainView())
        back.callback = _back
        self.add_item(back)
        self.add_item(_CfgRolePicker(lambda gid, rid: warn_roles.setdefault(gid, {}).__setitem__(1, rid), "warns", 1, "⚠️ Варн 1/3 — выбери роль"))
        self.add_item(_CfgRolePicker(lambda gid, rid: warn_roles.setdefault(gid, {}).__setitem__(2, rid), "warns", 2, "⚠️⚠️ Варн 2/3 — выбери роль"))
        self.add_item(_CfgRolePicker(lambda gid, rid: warn_roles.setdefault(gid, {}).__setitem__(3, rid), "warns", 3, "🚨 Варн 3/3 — выбери роль"))


class _CfgLogsView(ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=300)
        gid = guild.id

        back = _cfg_btn("◀ Назад", row=0)
        async def _back(inter): await inter.response.edit_message(embed=build_cfg_main_embed(inter.guild), view=CfgMainView())
        back.callback = _back
        self.add_item(back)

        btn_fb_role = _cfg_btn("🔔 Feedback роль →", row=0)
        async def _fb(inter):
            await inter.response.edit_message(
                embed=build_cfg_category_embed(inter.guild, "fb_role"),
                view=_CfgFbRoleView(inter.guild),
            )
        btn_fb_role.callback = _fb
        self.add_item(btn_fb_role)

        self.add_item(_CfgChannelPicker(lambda gid, cid: reject_log_channels.__setitem__(gid, cid), "logs", 1, "📋 Лог заявок — выбери канал"))
        self.add_item(_CfgChannelPicker(lambda gid, cid: shop_log_channels.__setitem__(gid, cid), "logs", 2, "🛍 Лог магазина — выбери канал"))
        self.add_item(_CfgChannelPicker(lambda gid, cid: obshak_log_channels.__setitem__(gid, cid), "logs", 3, "💰 Лог общака — выбери канал"))
        self.add_item(_CfgChannelPicker(lambda gid, cid: feedback_settings.setdefault(gid, {}).__setitem__("log_channel_id", cid), "logs", 4, "💬 Feedback канал — выбери канал"))


class _CfgFbRoleView(ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=300)
        back = _cfg_btn("◀ Назад к логам", row=0)
        async def _back(inter):
            await inter.response.edit_message(
                embed=build_cfg_category_embed(inter.guild, "logs"),
                view=_CfgLogsView(inter.guild),
            )
        back.callback = _back
        self.add_item(back)
        self.add_item(_CfgRolePicker(
            lambda gid, rid: feedback_settings.setdefault(gid, {}).__setitem__("ping_role_id", rid),
            "fb_role", 1, "🔔 Feedback пинг-роль — выбери роль",
        ))


class _CfgEventsView(ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        back = _cfg_btn("◀ Назад", row=0)
        async def _back(inter): await inter.response.edit_message(embed=build_cfg_main_embed(inter.guild), view=CfgMainView())
        back.callback = _back
        self.add_item(back)

        for label, etype in [("⚔️ ВЗП", "vzp"), ("🏎 МП", "mp"), ("🎯 Реаки", "list")]:
            btn = _cfg_btn(label, style=discord.ButtonStyle.primary, row=1)
            async def _cb(inter, et=etype):
                await inter.response.edit_message(
                    embed=build_cfg_category_embed(inter.guild, f"event_{et}"),
                    view=_CfgEventTypeView(inter.guild, et),
                )
            btn.callback = _cb
            self.add_item(btn)


class _CfgEventTypeView(ui.View):
    def __init__(self, guild: discord.Guild, etype: str):
        super().__init__(timeout=300)
        gid = guild.id
        cat_key = f"event_{etype}"

        back = _cfg_btn("◀ Назад к сборам", row=0)
        async def _back(inter):
            await inter.response.edit_message(
                embed=build_cfg_category_embed(inter.guild, "events"),
                view=_CfgEventsView(),
            )
        back.callback = _back
        self.add_item(back)

        def get_list(gid_): return event_command_roles.setdefault(gid_, {}).setdefault(etype, [])
        self.add_item(_CfgRoleAddPicker(get_list, cat_key, 1, f"➕ Добавить роль к !{etype}"))
        current = (event_command_roles.get(gid) or {}).get(etype, [])
        self.add_item(_CfgRoleRemoveSelect(guild, current, get_list, cat_key, 2, f"➖ Убрать роль из !{etype}"))


class _CfgVoiceView(ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=300)
        gid = guild.id
        vs  = voice_reward_settings.get(gid, {})

        back = _cfg_btn("◀ Назад", row=0)
        async def _back(inter): await inter.response.edit_message(embed=build_cfg_main_embed(inter.guild), view=CfgMainView())
        back.callback = _back
        self.add_item(back)

        btn_amount = _cfg_btn("✏️ Баллы в минуту", style=discord.ButtonStyle.primary, row=0)
        async def _amount(inter): await inter.response.send_modal(VoiceAmountModal(inter.message))
        btn_amount.callback = _amount
        self.add_item(btn_amount)

        def get_cats(gid_): return _get_voice_settings(gid_)["categories"]
        def get_excl(gid_): return _get_voice_settings(gid_)["excluded_channels"]

        self.add_item(_CfgChannelAddPicker(get_cats, "voice", 1, "➕ Добавить категорию войса", [discord.ChannelType.category]))
        self.add_item(_CfgChannelRemoveSelect(guild, vs.get("categories", []), get_cats, "voice", 2, "➖ Убрать категорию"))
        self.add_item(_CfgChannelAddPicker(get_excl, "voice", 3, "➕ Исключить голосовой канал", [discord.ChannelType.voice]))
        self.add_item(_CfgChannelRemoveSelect(guild, vs.get("excluded_channels", []), get_excl, "voice", 4, "➖ Вернуть канал"))


class _CfgContentView(ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=300)
        gid = guild.id
        cp  = cabinet_panels.get(gid, {})
        op  = obshak_panels.get(gid, {})
        fs  = feedback_settings.get(gid) or {}

        back = _cfg_btn("◀ Назад", row=0)
        async def _back(inter): await inter.response.edit_message(embed=build_cfg_main_embed(inter.guild), view=CfgMainView())
        back.callback = _back
        self.add_item(back)

        def _modal_btn(label, title, field_label, default_fn, apply_fn, cat_key, row, style=discord.ButtonStyle.secondary, text_style=discord.TextStyle.short, refresh_fn=None):
            btn = _cfg_btn(label, style=style, row=row)
            async def _cb(inter):
                await inter.response.send_modal(_CfgTextModal(
                    title, field_label, default_fn(inter.guild_id),
                    apply_fn, cat_key, inter.message,
                    style=text_style, refresh_fn=refresh_fn,
                ))
            btn.callback = _cb
            return btn

        # Личный кабинет
        self.add_item(_modal_btn("✏️ Кабинет текст", "Кабинет — текст", "Текст описания",
            lambda gid: (cabinet_panels.get(gid) or {}).get("text", ""),
            lambda gid, v: cabinet_panels.setdefault(gid, {}).__setitem__("text", v),
            "content", row=1, text_style=discord.TextStyle.paragraph, refresh_fn=_refresh_cabinet_panel))
        self.add_item(_modal_btn("🖼 Кабинет фото", "Кабинет — фото", "Ссылка на изображение",
            lambda gid: (cabinet_panels.get(gid) or {}).get("image_url", ""),
            lambda gid, v: cabinet_panels.setdefault(gid, {}).__setitem__("image_url", v),
            "content", row=1, refresh_fn=_refresh_cabinet_panel))
        self.add_item(_modal_btn("🔗 Ссылка кабинета", "Пригласительная ссылка", "Ссылка-приглашение",
            lambda gid: cabinet_invite_links.get(gid, ""),
            lambda gid, v: cabinet_invite_links.__setitem__(gid, v),
            "content", row=1))

        # Общак
        self.add_item(_modal_btn("✏️ Общак текст", "Общак — текст", "Текст описания",
            lambda gid: (obshak_panels.get(gid) or {}).get("text", ""),
            lambda gid, v: obshak_panels.setdefault(gid, {}).__setitem__("text", v),
            "content", row=2, text_style=discord.TextStyle.paragraph, refresh_fn=_refresh_obshak_panel))
        self.add_item(_modal_btn("🖼 Общак фото", "Общак — фото", "Ссылка на изображение",
            lambda gid: (obshak_panels.get(gid) or {}).get("image_url", ""),
            lambda gid, v: obshak_panels.setdefault(gid, {}).__setitem__("image_url", v),
            "content", row=2, refresh_fn=_refresh_obshak_panel))

        # Feedback
        self.add_item(_modal_btn("✏️ Feedback текст", "Feedback — текст", "Текст описания",
            lambda gid: (feedback_settings.get(gid) or {}).get("text", ""),
            lambda gid, v: feedback_settings.setdefault(gid, {}).__setitem__("text", v),
            "content", row=3, text_style=discord.TextStyle.paragraph, refresh_fn=_refresh_feedback_panel))
        self.add_item(_modal_btn("🖼 Feedback фото", "Feedback — фото", "Ссылка на изображение",
            lambda gid: (feedback_settings.get(gid) or {}).get("image_url", ""),
            lambda gid, v: feedback_settings.setdefault(gid, {}).__setitem__("image_url", v),
            "content", row=3, refresh_fn=_refresh_feedback_panel))

        # Брендинг
        def _brand_apply_footer(gid, v):
            guild_branding.setdefault(gid, {})["footer_icon"] = v or None
            save_data()
        def _brand_apply_gif(gid, v):
            guild_branding.setdefault(gid, {})["approve_gif"] = v or None
            save_data()
        def _brand_apply_afk(gid, v):
            guild_branding.setdefault(gid, {})["afk_image"] = v or None
            save_data()
        self.add_item(_modal_btn("🖼 Футер иконка", "Брендинг — футер", "URL иконки футера",
            lambda gid: (guild_branding.get(gid) or {}).get("footer_icon", ""),
            lambda gid, v: _brand_apply_footer(gid, v),
            "content", row=4))
        self.add_item(_modal_btn("🎞 GIF одобрения", "Брендинг — GIF", "URL GIF при одобрении тикета",
            lambda gid: (guild_branding.get(gid) or {}).get("approve_gif", ""),
            lambda gid, v: _brand_apply_gif(gid, v),
            "content", row=4))
        self.add_item(_modal_btn("🖼 АФК изображение", "Брендинг — АФК", "URL изображения в панели АФК",
            lambda gid: (guild_branding.get(gid) or {}).get("afk_image", ""),
            lambda gid, v: _brand_apply_afk(gid, v),
            "content", row=4))


# ── Фабрика view по ключу категории ──────────────────────────────────────────

def _cfg_make_view(guild: discord.Guild, cat: str) -> ui.View:
    if cat == "tickets":        return _CfgTicketsView(guild)
    if cat == "ticket_access":  return _CfgTicketAccessView(guild)
    if cat == "roles":          return _CfgRolesView()
    if cat == "warns":          return _CfgWarnsView()
    if cat == "logs":           return _CfgLogsView(guild)
    if cat == "fb_role":        return _CfgFbRoleView(guild)
    if cat == "events":         return _CfgEventsView()
    if cat.startswith("event_"):
        return _CfgEventTypeView(guild, cat.split("_", 1)[1])
    if cat == "voice":          return _CfgVoiceView(guild)
    if cat == "content":        return _CfgContentView(guild)
    return CfgMainView()


# ── Команда ───────────────────────────────────────────────────────────────────

@tree.command(name="панель_настройки", description="Интерактивная панель настройки бота")
async def slash_settings_panel(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    embed = build_cfg_main_embed(interaction.guild)
    view  = CfgMainView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class BrandingModal(ui.Modal, title="🖼 Брендинг сервера"):
    footer_icon = ui.TextInput(
        label="URL иконки футера",
        placeholder="https://i.imgur.com/...",
        required=False,
        max_length=500,
    )
    approve_gif = ui.TextInput(
        label="URL GIF при одобрении тикета",
        placeholder="https://media.giphy.com/...",
        required=False,
        max_length=500,
    )
    afk_image = ui.TextInput(
        label="URL изображения АФК панели",
        placeholder="https://...",
        required=False,
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        guild_branding[gid] = {
            "footer_icon": str(self.footer_icon).strip() or None,
            "approve_gif": str(self.approve_gif).strip() or None,
            "afk_image":   str(self.afk_image).strip() or None,
        }
        save_data()
        br = guild_branding[gid]
        embed = discord.Embed(
            title="✅ Брендинг обновлён",
            color=discord.Color.green(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="🖼 Футер иконка",   value=br["footer_icon"] or "*по умолчанию*", inline=False)
        embed.add_field(name="🎞 GIF одобрения",  value=br["approve_gif"] or "*по умолчанию*", inline=False)
        embed.add_field(name="🖼 АФК изображение", value=br["afk_image"]  or "*по умолчанию*", inline=False)
        embed.set_footer(text="MORIARTY • Оставь поля пустыми чтобы использовать значения по умолчанию")
        await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="брендинг", description="Настроить уникальный брендинг бота для этого сервера (иконка, GIF, фото)")
async def slash_branding(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid = interaction.guild_id
    br  = guild_branding.get(gid) or {}
    modal = BrandingModal()
    modal.footer_icon.default = br.get("footer_icon") or ""
    modal.approve_gif.default = br.get("approve_gif") or ""
    modal.afk_image.default   = br.get("afk_image")   or ""
    await interaction.response.send_modal(modal)


# ─────────────────────────────────────────────
# ПРИВАТНЫЕ КОМНАТЫ
# ─────────────────────────────────────────────
def build_private_vc_embed(owner: discord.Member, vc: discord.VoiceChannel) -> discord.Embed:
    limit = str(vc.user_limit) if vc.user_limit else "∞"
    embed = discord.Embed(
        title="🔒 Управление приватной комнатой",
        description=(
            "`+` • Добавить слот             `−` • Убрать слот\n"
            "👥 • Изменить слоты            🔄 • Передать канал\n"
            "🔓 • Открыть канал             🔒 • Закрыть канал\n"
            "👤➕ • Добавить пользователя  👤➖ • Убрать пользователя\n"
            "🙈 • Скрыть канал               👁 • Показать канал\n"
            "✏️ • Переименовать              🚫 • Заблокировать\n\n"
            "*Кнопки работают только для владельца канала.*"
        ),
        color=discord.Color.dark_red(),
    )
    embed.add_field(name="👑 Владелец", value=owner.mention, inline=True)
    embed.add_field(name="🔊 Канал",    value=vc.mention,    inline=True)
    embed.add_field(name="👥 Слоты",   value=limit,          inline=True)
    embed.set_footer(text="MORIARTY", icon_url=_footer(owner.guild.id))
    return embed


async def resolve_member(guild: discord.Guild, text: str) -> discord.Member | None:
    text = text.strip().lstrip("<@!").rstrip(">")
    try:
        return guild.get_member(int(text)) or await guild.fetch_member(int(text))
    except Exception:
        return discord.utils.find(
            lambda m: m.name.lower() == text.lower() or m.display_name.lower() == text.lower(),
            guild.members,
        )


class PVCRenameModal(ui.Modal, title="✏️ Переименовать канал"):
    name = ui.TextInput(label="Новое название", max_length=100, required=True)
    async def on_submit(self, interaction: discord.Interaction):
        vc, _ = _get_owner_vc(interaction)
        if not vc:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        await vc.edit(name=str(self.name))
        await interaction.response.send_message(f"✅ Канал переименован: **{self.name}**", ephemeral=True)

class PVCSlotsModal(ui.Modal, title="👥 Изменить количество слотов"):
    slots = ui.TextInput(label="Количество слотов (0 = без лимита)", placeholder="10", required=True, max_length=3)
    async def on_submit(self, interaction: discord.Interaction):
        vc, _ = _get_owner_vc(interaction)
        if not vc:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        try:
            n = max(0, min(99, int(str(self.slots))))
        except ValueError:
            return await interaction.response.send_message("❌ Введите число.", ephemeral=True)
        await vc.edit(user_limit=n)
        label = str(n) if n else "∞"
        await interaction.response.send_message(f"✅ Слотов: **{label}**", ephemeral=True)

class PVCUserActionModal(ui.Modal):
    user_input = ui.TextInput(label="Упомяните или введите ID пользователя", required=True)
    def __init__(self, action: str):
        titles = {
            "add": "👤➕ Добавить пользователя",
            "remove": "👤➖ Убрать пользователя",
            "transfer": "🔄 Передать канал",
            "block": "🚫 Заблокировать пользователя",
        }
        super().__init__(title=titles.get(action, "Действие"))
        self.action = action

    async def on_submit(self, interaction: discord.Interaction):
        vc, data = _get_owner_vc(interaction)
        if not vc:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        member = await resolve_member(interaction.guild, str(self.user_input))
        if not member:
            return await interaction.response.send_message("❌ Пользователь не найден.", ephemeral=True)
        if member == interaction.user:
            return await interaction.response.send_message("❌ Нельзя применить к себе.", ephemeral=True)

        if self.action == "add":
            await vc.set_permissions(member, connect=True, view_channel=True)
            await interaction.response.send_message(f"✅ {member.mention} добавлен в канал.", ephemeral=True)

        elif self.action == "remove":
            await vc.set_permissions(member, overwrite=None)
            if member in vc.members:
                await member.move_to(None)
            await interaction.response.send_message(f"✅ {member.mention} убран из канала.", ephemeral=True)

        elif self.action == "transfer":
            private_vcs[vc.id]["owner_id"] = member.id
            await vc.set_permissions(interaction.user, overwrite=None)
            await vc.set_permissions(member, connect=True, manage_channels=True, move_members=True)
            # Обновить панель
            await _refresh_pvc_panel(interaction.guild, vc)
            await interaction.response.send_message(f"✅ Канал передан {member.mention}.", ephemeral=True)

        elif self.action == "block":
            await vc.set_permissions(member, connect=False, view_channel=False)
            if member in vc.members:
                await member.move_to(None)
            await interaction.response.send_message(f"✅ {member.mention} заблокирован.", ephemeral=True)


def _get_owner_vc(interaction: discord.Interaction):
    """Возвращает (VoiceChannel, data) приватного канала владельца."""
    for vc_id, data in private_vcs.items():
        if data["owner_id"] == interaction.user.id and data["guild_id"] == interaction.guild_id:
            vc = interaction.guild.get_channel(vc_id)
            if vc:
                return vc, data
    return None, None


async def _refresh_pvc_panel(guild: discord.Guild, vc: discord.VoiceChannel):
    data = private_vcs.get(vc.id)
    if not data:
        return
    owner = guild.get_member(data["owner_id"])
    if not owner:
        return
    panel_ch = guild.get_channel(data.get("panel_channel_id"))
    if not panel_ch:
        return
    try:
        msg = await panel_ch.fetch_message(data["panel_msg_id"])
        await msg.edit(embed=build_private_vc_embed(owner, vc))
    except Exception:
        pass


class PrivateVCView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(emoji="➕", style=discord.ButtonStyle.secondary, custom_id="pvc_add_slot", row=0)
    async def add_slot(self, interaction: discord.Interaction, button: ui.Button):
        vc, _ = _get_owner_vc(interaction)
        if not vc:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        new_limit = (vc.user_limit or 0) + 1
        await vc.edit(user_limit=new_limit)
        await interaction.response.send_message(f"✅ Слотов: **{new_limit}**", ephemeral=True)

    @ui.button(emoji="➖", style=discord.ButtonStyle.secondary, custom_id="pvc_remove_slot", row=0)
    async def remove_slot(self, interaction: discord.Interaction, button: ui.Button):
        vc, _ = _get_owner_vc(interaction)
        if not vc:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        new_limit = max(0, (vc.user_limit or 1) - 1)
        await vc.edit(user_limit=new_limit)
        label = str(new_limit) if new_limit else "∞"
        await interaction.response.send_message(f"✅ Слотов: **{label}**", ephemeral=True)

    @ui.button(emoji="👥", style=discord.ButtonStyle.secondary, custom_id="pvc_set_slots", row=0)
    async def set_slots(self, interaction: discord.Interaction, button: ui.Button):
        _, data = _get_owner_vc(interaction)
        if not data:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        await interaction.response.send_modal(PVCSlotsModal())

    @ui.button(emoji="🔓", style=discord.ButtonStyle.secondary, custom_id="pvc_open", row=0)
    async def open_channel(self, interaction: discord.Interaction, button: ui.Button):
        vc, _ = _get_owner_vc(interaction)
        if not vc:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        await vc.set_permissions(interaction.guild.default_role, connect=True)
        await interaction.response.send_message("✅ Канал открыт.", ephemeral=True)

    @ui.button(emoji="🔒", style=discord.ButtonStyle.secondary, custom_id="pvc_close", row=0)
    async def close_channel(self, interaction: discord.Interaction, button: ui.Button):
        vc, _ = _get_owner_vc(interaction)
        if not vc:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        await vc.set_permissions(interaction.guild.default_role, connect=False)
        await interaction.response.send_message("✅ Канал закрыт.", ephemeral=True)

    @ui.button(emoji="👤", style=discord.ButtonStyle.secondary, custom_id="pvc_add_user", row=1)
    async def add_user(self, interaction: discord.Interaction, button: ui.Button):
        _, data = _get_owner_vc(interaction)
        if not data:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        await interaction.response.send_modal(PVCUserActionModal("add"))

    @ui.button(emoji="🚷", style=discord.ButtonStyle.secondary, custom_id="pvc_remove_user", row=1)
    async def remove_user(self, interaction: discord.Interaction, button: ui.Button):
        _, data = _get_owner_vc(interaction)
        if not data:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        await interaction.response.send_modal(PVCUserActionModal("remove"))

    @ui.button(emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="pvc_transfer", row=1)
    async def transfer(self, interaction: discord.Interaction, button: ui.Button):
        _, data = _get_owner_vc(interaction)
        if not data:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        await interaction.response.send_modal(PVCUserActionModal("transfer"))

    @ui.button(emoji="🙈", style=discord.ButtonStyle.secondary, custom_id="pvc_hide", row=1)
    async def hide_channel(self, interaction: discord.Interaction, button: ui.Button):
        vc, _ = _get_owner_vc(interaction)
        if not vc:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        await vc.set_permissions(interaction.guild.default_role, view_channel=False)
        await interaction.response.send_message("✅ Канал скрыт.", ephemeral=True)

    @ui.button(emoji="👁", style=discord.ButtonStyle.secondary, custom_id="pvc_show", row=1)
    async def show_channel(self, interaction: discord.Interaction, button: ui.Button):
        vc, _ = _get_owner_vc(interaction)
        if not vc:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        await vc.set_permissions(interaction.guild.default_role, view_channel=True)
        await interaction.response.send_message("✅ Канал показан.", ephemeral=True)

    @ui.button(emoji="✏️", style=discord.ButtonStyle.secondary, custom_id="pvc_rename", row=2)
    async def rename(self, interaction: discord.Interaction, button: ui.Button):
        _, data = _get_owner_vc(interaction)
        if not data:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        await interaction.response.send_modal(PVCRenameModal())

    @ui.button(emoji="🚫", style=discord.ButtonStyle.secondary, custom_id="pvc_block", row=2)
    async def block_user(self, interaction: discord.Interaction, button: ui.Button):
        _, data = _get_owner_vc(interaction)
        if not data:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        await interaction.response.send_modal(PVCUserActionModal("block"))


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    # ── Трекинг минут в войсе ──
    if not member.bot:
        gid = member.guild.id
        uid = member.id
        now = datetime.now()

        # Вышел из канала или сменил канал
        if before.channel is not None:
            join_time = voice_join_times.get(gid, {}).get(uid)
            if join_time:
                minutes = int((now - join_time).total_seconds() // 60)
                if minutes > 0:
                    if gid not in voice_minutes:
                        voice_minutes[gid] = {}
                    voice_minutes[gid][uid] = voice_minutes[gid].get(uid, 0) + minutes
                voice_join_times.get(gid, {}).pop(uid, None)

        # Зашёл в новый канал
        if after.channel is not None:
            if gid not in voice_join_times:
                voice_join_times[gid] = {}
            voice_join_times[gid][uid] = now

    # ── Приватные комнаты ──
    settings = private_vc_settings.get(member.guild.id)
    if not settings:
        return

    create_ch_id = settings.get("create_channel_id")

    # Пользователь зашёл в канал-триггер
    if after.channel and after.channel.id == create_ch_id:
        category = member.guild.get_channel(settings.get("category_id"))
        overwrites = {
            member.guild.default_role: discord.PermissionOverwrite(connect=True, view_channel=True),
            member: discord.PermissionOverwrite(connect=True, view_channel=True, manage_channels=True, move_members=True),
            member.guild.me: discord.PermissionOverwrite(connect=True, view_channel=True, manage_channels=True, move_members=True),
        }
        try:
            vc = await member.guild.create_voice_channel(
                name=f"🔒 {member.display_name}",
                category=category,
                user_limit=10,
                overwrites=overwrites,
            )
            await member.move_to(vc)
        except Exception:
            return

        private_vcs[vc.id] = {
            "owner_id": member.id,
            "guild_id": member.guild.id,
            "panel_msg_id": None,
            "panel_channel_id": None,
        }

        panel_ch_id = settings.get("panel_channel_id")
        if panel_ch_id:
            panel_ch = member.guild.get_channel(panel_ch_id)
            if panel_ch:
                try:
                    msg = await panel_ch.send(
                        embed=build_private_vc_embed(member, vc),
                        view=PrivateVCView(),
                    )
                    private_vcs[vc.id]["panel_msg_id"]     = msg.id
                    private_vcs[vc.id]["panel_channel_id"] = panel_ch_id
                except Exception:
                    pass

    # Пользователь вышел из приватного канала — удаляем если пусто
    if before.channel and before.channel.id in private_vcs:
        vc = before.channel
        if len(vc.members) == 0:
            data = private_vcs.pop(vc.id, {})
            try:
                await vc.delete(reason="Приватный канал опустел")
            except Exception:
                pass
            if data.get("panel_msg_id") and data.get("panel_channel_id"):
                panel_ch = member.guild.get_channel(data["panel_channel_id"])
                if panel_ch:
                    try:
                        msg = await panel_ch.fetch_message(data["panel_msg_id"])
                        await msg.delete()
                    except Exception:
                        pass


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        await bot.process_commands(message)
        return
    gid = message.guild.id
    uid = message.author.id
    if gid not in message_counts:
        message_counts[gid] = {}
    message_counts[gid][uid] = message_counts[gid].get(uid, 0) + 1
    await bot.process_commands(message)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.message.delete(delay=0)
    elif isinstance(error, (commands.MemberNotFound, commands.BadArgument)):
        await ctx.send("❌ Неверный аргумент. Пример: `!warn @user причина`", delete_after=6)
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Не хватает аргумента: `{error.param.name}`", delete_after=6)



# ─────────────────────────────────────────────
# СТАРТ
# ─────────────────────────────────────────────
@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        if not interaction.response.is_done():
            await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    else:
        if not interaction.response.is_done():
            await interaction.response.send_message("❌ Ошибка выполнения команды.", ephemeral=True)


@tree.command(name="приват", description="Настроить систему приватных комнат")
@app_commands.describe(
    канал_создания="Голосовой канал — зайди сюда, чтобы получить приватную комнату",
    категория="Категория, где создаются приватные каналы",
    канал_панели="Текстовый канал, куда присылается панель управления",
)
async def slash_private_vc(
    interaction: discord.Interaction,
    канал_создания: discord.VoiceChannel,
    категория: discord.CategoryChannel,
    канал_панели: discord.TextChannel,
):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    private_vc_settings[interaction.guild_id] = {
        "create_channel_id": канал_создания.id,
        "category_id":       категория.id,
        "panel_channel_id":  канал_панели.id,
    }
    save_data()
    await interaction.response.send_message(
        f"✅ Приватные комнаты настроены!\n"
        f"Триггер: {канал_создания.mention}\n"
        f"Категория: **{категория.name}**\n"
        f"Панель: {канал_панели.mention}",
        ephemeral=True,
    )


@bot.event
async def on_ready():
    load_data()
    load_obshak()
    load_points()
    load_chips()
    load_roulette()
    bot.add_view(AfkView())
    bot.add_view(InactiveView())
    bot.add_view(PrivateVCView())
    bot.add_view(ApplicationReviewView())
    bot.add_view(PostCloseView())
    bot.add_view(ContractPanelView())
    bot.add_view(ActiveContractView(0))
    bot.add_view(FeedbackPanelView())
    bot.add_view(ObshakView())
    bot.add_view(PersonalCabinetView())
    bot.add_view(WarnListView())
    # RosterPaginationView — stateful, восстанавливается через _refresh_roster при запуске
    for guild_id in guild_shop_items:
        bot.add_view(ShopView(guild_id))
    for guild_id, panel in ticket_panels.items():
        cat_id = panel.get("category_id")
        if cat_id:
            bot.add_view(TicketPanelView(cat_id))
    for message_id in event_lists:
        bot.add_view(ThreadListView(message_id))
    await tree.sync()
    if not vzp_monitor_loop.is_running():
        vzp_monitor_loop.start()
    print(f"Bot online: {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name="MORIARTY Helper"
    ))
    if not update_stats.is_running():
        update_stats.start()
    if not voice_reward_loop.is_running():
        voice_reward_loop.start()
    if not game_activity_check_loop.is_running():
        game_activity_check_loop.start()
    if not inactive_expire_loop.is_running():
        inactive_expire_loop.start()
    # Пересоздаём панели состава чтобы кнопки снова работали после рестарта
    for gid in list(roster_settings.keys()):
        guild = bot.get_guild(gid)
        if guild:
            await _refresh_roster(guild)


# ─────────────────────────────────────────────
# СТАТИСТИКА GTA5RP
# ─────────────────────────────────────────────

RAGEMP_API = "https://cdn.rage.mp/master/"

# { guild_id: { "channel_id": int, "message_id": int } }
stats_panels: dict = {}

SERVER_ORDER = [
    "Downtown", "Strawberry", "VineWood", "Blackberry", "Insquad",
    "Sunrise", "Rainbow", "Richman", "Eclipse", "La Mesa", "Burton",
    "Rockford", "Alta", "Del Perro", "Davis", "Harmony", "Redwood",
    "Hawick", "Grapeseed", "Murrieta", "Vespucci", "Milton", "La Puerta",
]
_SERVER_ORDER_LOWER = {name.lower(): i for i, name in enumerate(SERVER_ORDER)}


async def fetch_gta5rp_stats() -> tuple[list[tuple[str, int]], int] | None:
    """Возвращает [(название, онлайн), ...] в порядке SERVER_ORDER + общий онлайн."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(RAGEMP_API, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
    except Exception:
        return None

    servers = []
    for addr, info in data.items():
        name_raw = info.get("name", "")
        if "gta5rp.com" not in name_raw.lower() and "gta5rp.com" not in addr.lower():
            continue
        m = re.search(r"GTA5RP\.COM \| (.+?) \|", name_raw, re.IGNORECASE)
        short_name = m.group(1).strip() if m else name_raw
        players = info.get("players", 0)
        servers.append((short_name, players))

    if not servers:
        return None

    servers.sort(key=lambda x: _SERVER_ORDER_LOWER.get(x[0].lower(), 9999))
    total = sum(p for _, p in servers)
    return servers, total


def build_stats_embed(servers: list[tuple[str, int]], total: int) -> discord.Embed:
    embed = discord.Embed(
        title="Статистика серверов GTA5RP",
        description="**Актуальная статистика (Обновляется каждые 30 секунд)**\n",
        color=0xf1c40f,
        timestamp=datetime.now(),
    )
    lines = "\n".join(f"**{name}** — {players} игр." for name, players in servers)
    embed.description += lines
    embed.add_field(name="🌐 Общий онлайн", value=f"**{total:,}** игроков".replace(",", " "), inline=False)
    embed.set_footer(text="rage.mp • GTA5RP.COM")
    return embed


@bot.tree.command(name="статистика", description="Показать онлайн серверов GTA5RP (авто-обновление)")
async def stats_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    result = await fetch_gta5rp_stats()
    if not result:
        return await interaction.followup.send("❌ Не удалось получить данные.", ephemeral=True)

    servers, total = result
    embed = build_stats_embed(servers, total)
    msg = await interaction.channel.send(embed=embed)

    stats_panels[interaction.guild_id] = {
        "channel_id": interaction.channel_id,
        "message_id": msg.id,
    }

    await interaction.followup.send("✅ Панель создана, будет обновляться каждые 30 сек.", ephemeral=True)


@bot.command(name="статистика")
async def stats_prefix(ctx):
    result = await fetch_gta5rp_stats()
    if not result:
        return await ctx.send("❌ Не удалось получить данные.")

    servers, total = result
    embed = build_stats_embed(servers, total)
    msg = await ctx.send(embed=embed)

    stats_panels[ctx.guild.id] = {
        "channel_id": ctx.channel.id,
        "message_id": msg.id,
    }


@tasks.loop(seconds=30)
async def update_stats():
    if not stats_panels:
        return
    result = await fetch_gta5rp_stats()
    if not result:
        return
    servers, total = result
    embed = build_stats_embed(servers, total)

    for guild_id, panel in list(stats_panels.items()):
        ch = bot.get_channel(panel["channel_id"])
        if not ch:
            continue
        try:
            msg = await ch.fetch_message(panel["message_id"])
            await msg.edit(embed=embed)
        except Exception:
            stats_panels.pop(guild_id, None)


# ─────────────────────────────────────────────
# КОНТРАКТЫ
# ─────────────────────────────────────────────

DEFAULT_CONTRACT_TEXT = (
    "**Контракт открыт!**\n"
    "Нажми кнопку ниже, чтобы взять контракт и указать время."
)


def build_contract_panel_embed(guild_id: int) -> discord.Embed:
    cfg = contract_settings.get(guild_id, {})
    text = cfg.get("text", DEFAULT_CONTRACT_TEXT)
    image_url = cfg.get("image_url")
    embed = discord.Embed(
        title="📄 Контракт",
        description=text,
        color=discord.Color.blue(),
        timestamp=datetime.now(),
    )
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
    return embed


def build_active_contract_embed(data: dict) -> discord.Embed:
    guild_id     = data.get("guild_id", 0)
    participants = data.get("participants", [])
    creator_id   = data.get("creator_id")
    duration     = data.get("duration", "—")
    start        = data.get("start", "—")

    if participants:
        parts_text = "\n".join(f"• <@{uid}>" for uid in participants)
    else:
        parts_text = "*Пока никто не принял участие*"

    embed = discord.Embed(
        title="📄 Активный контракт",
        color=discord.Color.gold(),
        timestamp=datetime.now(),
    )
    embed.add_field(name="⏱ Длительность", value=f"`{duration}`", inline=True)
    embed.add_field(name="🕐 Начало",       value=f"`{start}`",    inline=True)
    embed.add_field(name="👤 Создал",       value=f"<@{creator_id}>", inline=True)
    embed.add_field(name=f"👥 Участники ({len(participants)})", value=parts_text, inline=False)
    embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
    return embed


class ContractModal(ui.Modal, title="📄 Взять контракт"):
    duration = ui.TextInput(
        label="Длительность",
        placeholder="2:20",
        required=True,
        max_length=20,
    )
    start = ui.TextInput(
        label="Начало",
        placeholder="10:20 / сейчас / через 15 минут",
        required=True,
        max_length=50,
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        role_id  = contract_roles.get(guild_id)
        content  = f"<@&{role_id}>" if role_id else None

        contract_data = {
            "guild_id":    guild_id,
            "creator_id":  interaction.user.id,
            "duration":    str(self.duration),
            "start":       str(self.start),
            "channel_id":  interaction.channel_id,
            "participants": [],
        }

        embed = build_active_contract_embed(contract_data)
        view  = ActiveContractView(0)
        msg   = await interaction.channel.send(content=content, embed=embed, view=view)

        contract_data["message_id"] = msg.id
        active_contracts[msg.id]    = contract_data
        save_data()

        await interaction.response.send_message("✅ Контракт создан!", ephemeral=True)


class ContractPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Взять контракт", style=discord.ButtonStyle.primary, emoji="✅", custom_id="contract_take")
    async def take(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(ContractModal())


class ActiveContractView(ui.View):
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id

    @ui.button(label="Принять участие", style=discord.ButtonStyle.success, emoji="➕",
               custom_id="contract_join")
    async def join(self, interaction: discord.Interaction, button: ui.Button):
        # Ищем контракт по ID сообщения
        msg_id = interaction.message.id
        data   = active_contracts.get(msg_id)
        if not data:
            return await interaction.response.send_message("❌ Контракт недоступен!", ephemeral=True)
        if interaction.user.id in data["participants"]:
            return await interaction.response.send_message("⚠️ Вы уже участвуете!", ephemeral=True)
        data["participants"].append(interaction.user.id)
        save_data()
        await interaction.response.edit_message(embed=build_active_contract_embed(data))

    @ui.button(label="Отменить участие", style=discord.ButtonStyle.danger, emoji="➖",
               custom_id="contract_leave")
    async def leave(self, interaction: discord.Interaction, button: ui.Button):
        msg_id = interaction.message.id
        data   = active_contracts.get(msg_id)
        if not data:
            return await interaction.response.send_message("❌ Контракт недоступен!", ephemeral=True)
        if interaction.user.id not in data["participants"]:
            return await interaction.response.send_message("⚠️ Вас нет в участниках!", ephemeral=True)
        data["participants"].remove(interaction.user.id)
        save_data()
        await interaction.response.edit_message(embed=build_active_contract_embed(data))

    @ui.button(label="Контракт взят", style=discord.ButtonStyle.secondary, emoji="🔒",
               custom_id="contract_close")
    async def close(self, interaction: discord.Interaction, button: ui.Button):
        msg_id = interaction.message.id
        data   = active_contracts.get(msg_id)
        if not data:
            return await interaction.response.send_message("❌ Контракт недоступен!", ephemeral=True)

        # Закрыть может только создатель или администратор
        is_creator = interaction.user.id == data["creator_id"]
        if not is_creator and not is_admin(interaction):
            return await interaction.response.send_message(
                "❌ Закрыть контракт может только его создатель или администратор.", ephemeral=True
            )

        active_contracts.pop(msg_id, None)
        save_data()
        await interaction.message.delete()
        await interaction.response.send_message("✅ Контракт закрыт.", ephemeral=True)


@bot.command(name="контракт")
async def contract_panel_cmd(ctx):
    """!контракт — создать/обновить панель контрактов в этом канале"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()

    guild_id = ctx.guild.id
    if guild_id not in contract_settings:
        contract_settings[guild_id] = {}

    contract_settings[guild_id]["channel_id"] = ctx.channel.id

    embed = build_contract_panel_embed(guild_id)
    view  = ContractPanelView()
    msg   = await ctx.send(embed=embed, view=view)
    contract_settings[guild_id]["message_id"] = msg.id
    save_data()

    try:
        await ctx.message.delete()
    except Exception:
        pass


@bot.tree.command(name="контракт_текст", description="Изменить текст панели контрактов")
@app_commands.describe(текст="Новый текст для панели контрактов")
async def contract_text_cmd(interaction: discord.Interaction, текст: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    guild_id = interaction.guild_id
    if guild_id not in contract_settings:
        contract_settings[guild_id] = {}
    contract_settings[guild_id]["text"] = текст
    save_data()
    await _refresh_contract_panel(interaction.guild)
    await interaction.response.send_message("✅ Текст контракта обновлён!", ephemeral=True)


@bot.tree.command(name="контракт_фото", description="Изменить фото панели контрактов")
@app_commands.describe(url="Прямая ссылка на изображение")
async def contract_photo_cmd(interaction: discord.Interaction, url: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    guild_id = interaction.guild_id
    if guild_id not in contract_settings:
        contract_settings[guild_id] = {}
    contract_settings[guild_id]["image_url"] = url
    save_data()
    await _refresh_contract_panel(interaction.guild)
    await interaction.response.send_message("✅ Фото контракта обновлено!", ephemeral=True)


@bot.tree.command(name="контракт_роль", description="Роль, которая тегается при создании контракта")
@app_commands.describe(роль="Роль для упоминания")
async def contract_role_cmd(interaction: discord.Interaction, роль: discord.Role):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    contract_roles[interaction.guild_id] = роль.id
    save_data()
    await interaction.response.send_message(f"✅ Роль контракта: {роль.mention}", ephemeral=True)


async def _refresh_contract_panel(guild: discord.Guild):
    """Обновляет embed панели контрактов при изменении текста/фото."""
    cfg = contract_settings.get(guild.id)
    if not cfg or not cfg.get("message_id"):
        return
    try:
        channel = guild.get_channel(cfg["channel_id"])
        if not channel:
            return
        msg = await channel.fetch_message(cfg["message_id"])
        await msg.edit(embed=build_contract_panel_embed(guild.id))
    except Exception:
        pass


# ─────────────────────────────────────────────
# ФИДБЕКИ
# ─────────────────────────────────────────────

DEFAULT_FEEDBACK_TEXT = (
    "**💬 Предложения и обратная связь**\n\n"
    "Есть идея или замечание? Нажми кнопку ниже и заполни форму — "
    "твоё сообщение уйдёт старшему составу."
)


def build_feedback_panel_embed(guild_id: int) -> discord.Embed:
    cfg = feedback_settings.get(guild_id, {})
    text = cfg.get("text", DEFAULT_FEEDBACK_TEXT)
    image_url = cfg.get("image_url")
    embed = discord.Embed(
        title="📝 Обратная связь",
        description=text,
        color=discord.Color.blurple(),
        timestamp=datetime.now(),
    )
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
    return embed


class FeedbackModal(ui.Modal, title="📝 Оставить предложение"):
    message = ui.TextInput(
        label="Ваше предложение",
        style=discord.TextStyle.paragraph,
        placeholder="Напиши своё предложение или замечание...",
        required=True,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        cfg = feedback_settings.get(guild_id, {})
        log_channel_id = cfg.get("log_channel_id")

        if not log_channel_id:
            return await interaction.response.send_message(
                "❌ Канал для фидбеков не настроен. Обратитесь к администратору.",
                ephemeral=True,
            )

        log_channel = interaction.guild.get_channel(log_channel_id)
        if not log_channel:
            return await interaction.response.send_message(
                "❌ Канал для фидбеков не найден.", ephemeral=True
            )

        ping_role_id = cfg.get("ping_role_id")
        content = f"<@&{ping_role_id}>" if ping_role_id else None

        embed = discord.Embed(
            title="📝 Новое предложение",
            description=str(self.message),
            color=discord.Color.blurple(),
            timestamp=datetime.now(),
        )
        embed.set_author(
            name=str(interaction.user.display_name),
            icon_url=interaction.user.display_avatar.url,
        )
        embed.add_field(name="👤 От", value=interaction.user.mention, inline=True)
        embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))

        msg = await log_channel.send(content=content, embed=embed)

        # Создаём тред для обсуждения
        try:
            short = str(self.message)[:50].strip()
            thread_name = f"💬 {interaction.user.display_name}: {short}"
            await msg.create_thread(name=thread_name, auto_archive_duration=1440)
        except Exception:
            pass

        await interaction.response.send_message(
            "✅ Спасибо! Твоё предложение отправлено.", ephemeral=True
        )


class FeedbackPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Оставить предложение", style=discord.ButtonStyle.primary,
               emoji="📝", custom_id="feedback_submit")
    async def submit(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(FeedbackModal())


async def _refresh_feedback_panel(guild: discord.Guild):
    cfg = feedback_settings.get(guild.id, {})
    if not cfg.get("panel_message_id"):
        return
    try:
        channel = guild.get_channel(cfg["panel_channel_id"])
        if not channel:
            return
        msg = await channel.fetch_message(cfg["panel_message_id"])
        await msg.edit(embed=build_feedback_panel_embed(guild.id))
    except Exception:
        pass


@bot.command(name="feedback")
async def feedback_panel_cmd(ctx):
    """!feedback — создать панель обратной связи в этом канале"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()

    guild_id = ctx.guild.id
    if guild_id not in feedback_settings:
        feedback_settings[guild_id] = {}

    embed = build_feedback_panel_embed(guild_id)
    view  = FeedbackPanelView()
    msg   = await ctx.send(embed=embed, view=view)

    feedback_settings[guild_id]["panel_channel_id"]  = ctx.channel.id
    feedback_settings[guild_id]["panel_message_id"]  = msg.id
    save_data()

    try:
        await ctx.message.delete()
    except Exception:
        pass


@bot.command(name="feedback_канал")
async def feedback_channel_cmd(ctx, channel: discord.TextChannel):
    """!feedback_канал #канал — куда будут приходить фидбеки"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()

    guild_id = ctx.guild.id
    if guild_id not in feedback_settings:
        feedback_settings[guild_id] = {}

    feedback_settings[guild_id]["log_channel_id"] = channel.id
    save_data()
    await ctx.send(f"✅ Фидбеки будут приходить в {channel.mention}", delete_after=5)
    try:
        await ctx.message.delete()
    except Exception:
        pass


@bot.command(name="feedback_роль")
async def feedback_role_cmd(ctx, role: discord.Role):
    """!feedback_роль @роль — тег при новом фидбеке"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()

    guild_id = ctx.guild.id
    if guild_id not in feedback_settings:
        feedback_settings[guild_id] = {}

    feedback_settings[guild_id]["ping_role_id"] = role.id
    save_data()
    await ctx.send(f"✅ Роль для фидбеков: {role.mention}", delete_after=5)
    try:
        await ctx.message.delete()
    except Exception:
        pass


@bot.command(name="feedback_текст")
async def feedback_text_cmd(ctx, *, text: str):
    """!feedback_текст <текст> — изменить текст панели фидбеков"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()

    guild_id = ctx.guild.id
    if guild_id not in feedback_settings:
        feedback_settings[guild_id] = {}

    feedback_settings[guild_id]["text"] = text
    save_data()
    await _refresh_feedback_panel(ctx.guild)
    await ctx.send("✅ Текст панели обновлён!", delete_after=5)
    try:
        await ctx.message.delete()
    except Exception:
        pass


@bot.command(name="feedback_фото")
async def feedback_photo_cmd(ctx, url: str):
    """!feedback_фото <url> — изменить фото панели фидбеков"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()

    guild_id = ctx.guild.id
    if guild_id not in feedback_settings:
        feedback_settings[guild_id] = {}

    feedback_settings[guild_id]["image_url"] = url
    save_data()
    await _refresh_feedback_panel(ctx.guild)
    await ctx.send("✅ Фото панели обновлено!", delete_after=5)
    try:
        await ctx.message.delete()
    except Exception:
        pass


# ─────────────────────────────────────────────
# ЛИЧНЫЙ КАБИНЕТ
# ─────────────────────────────────────────────

DEFAULT_CABINET_TEXT = "Здесь ты можешь посмотреть свою статистику, баланс и оставить предложение."


def build_cabinet_embed(guild_id: int) -> discord.Embed:
    settings  = cabinet_panels.get(guild_id, {})
    text      = settings.get("text") or DEFAULT_CABINET_TEXT
    image_url = settings.get("image_url")

    embed = discord.Embed(
        title="🪪 Личный кабинет",
        description=text,
        color=0x2b2d31,
    )
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
    return embed


async def _refresh_cabinet_panel(guild: discord.Guild):
    settings = cabinet_panels.get(guild.id)
    if not settings or not settings.get("message_id"):
        return
    try:
        ch  = guild.get_channel(settings["channel_id"])
        msg = await ch.fetch_message(settings["message_id"])
        await msg.edit(embed=build_cabinet_embed(guild.id), view=PersonalCabinetView())
    except Exception:
        pass


class PersonalCabinetView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # ── Ряд 1 ──

    @ui.button(label="Баланс", emoji="💎", style=discord.ButtonStyle.secondary, custom_id="cabinet_balance", row=0)
    async def btn_balance(self, interaction: discord.Interaction, button: ui.Button):
        gid = interaction.guild_id
        uid = interaction.user.id
        pts   = get_points(gid, uid)
        chips = get_chips(gid, uid)
        embed = discord.Embed(
            title="💰 Твой баланс",
            color=0x2b2d31,
            timestamp=datetime.now(),
        )
        embed.add_field(name="Алмазы", value=f"**{pts:,}** 💎".replace(",", "."), inline=True)
        embed.add_field(name="Фишки", value=f"**{chips:,}** 🎰".replace(",", "."), inline=True)
        embed.set_footer(text="MORIARTY", icon_url=_footer(gid))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Варны", emoji="⚠️", style=discord.ButtonStyle.secondary, custom_id="cabinet_warns", row=0)
    async def btn_warns(self, interaction: discord.Interaction, button: ui.Button):
        warn_data = get_warns(interaction.guild_id, interaction.user.id)
        if not warn_data:
            embed = discord.Embed(
                title="✅ Варны",
                description="У тебя нет варнов.",
                color=discord.Color.green(),
                timestamp=datetime.now(),
            )
        else:
            embed = discord.Embed(
                title="⚠️ Варны",
                color=discord.Color.orange(),
                timestamp=datetime.now(),
            )
            embed.add_field(name="Количество", value=f"{warn_data['warns']}/3", inline=True)
            embed.add_field(name="Причина", value=warn_data.get("reason", "—"), inline=True)
        embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Фидбек", emoji="💬", style=discord.ButtonStyle.danger, custom_id="cabinet_feedback", row=0)
    async def btn_feedback(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(FeedbackModal())

    # ── Ряд 2 ──

    @ui.button(label="Статистика", style=discord.ButtonStyle.secondary, custom_id="cabinet_stats", row=1)
    async def btn_stats(self, interaction: discord.Interaction, button: ui.Button):
        member   = interaction.user
        gid, uid = interaction.guild_id, member.id

        msgs    = message_counts.get(gid, {}).get(uid, 0)
        v_mins  = voice_minutes.get(gid, {}).get(uid, 0)
        # Добавляем текущую сессию если сейчас в войсе
        join_t = voice_join_times.get(gid, {}).get(uid)
        if join_t:
            v_mins += int((datetime.now() - join_t).total_seconds() // 60)

        joined = member.joined_at.strftime("%d.%m.%Y") if member.joined_at else "—"

        embed = discord.Embed(
            title="📊 Статистика",
            color=0x2b2d31,
            timestamp=datetime.now(),
        )
        embed.add_field(name="👤 Имя", value=str(member.display_name), inline=True)
        embed.add_field(name="🆔 User ID", value=str(uid), inline=True)
        embed.add_field(name="📅 Вступил", value=joined, inline=True)
        embed.add_field(name="💬 Сообщений", value=str(msgs), inline=True)
        embed.add_field(name="🎙 Минут в войсе", value=str(v_mins), inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="MORIARTY", icon_url=_footer(gid))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Пригласить друга", style=discord.ButtonStyle.primary, custom_id="cabinet_invite", row=1)
    async def btn_invite(self, interaction: discord.Interaction, button: ui.Button):
        link = cabinet_invite_links.get(interaction.guild_id)
        if not link:
            return await interaction.response.send_message(
                "❌ Пригласительная ссылка ещё не настроена администратором.", ephemeral=True
            )
        text = (
            "**Чтобы пригласить друга, скопируй ссылку ниже**\n"
            f"```\n{link}\n```\n"
            "После вступления, друг должен заполнить тикет в канале "
            "<#1466567658601189481>\n"
            "__За каждого приглашенного человека в семью, полагается вознаграждение__"
        )
        await interaction.response.send_message(text, ephemeral=True)


# ── Команды ──

@tree.command(name="личный_кабинет", description="Создать панель личного кабинета в текущем канале")
async def slash_cabinet(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)

    gid = interaction.guild_id

    # Удалить старую панель если есть
    existing = cabinet_panels.get(gid)
    if existing and existing.get("message_id"):
        try:
            old_ch  = interaction.guild.get_channel(existing["channel_id"])
            old_msg = await old_ch.fetch_message(existing["message_id"])
            await old_msg.delete()
        except Exception:
            pass

    prev  = cabinet_panels.get(gid, {})
    embed = build_cabinet_embed(gid)
    msg   = await interaction.channel.send(embed=embed, view=PersonalCabinetView())

    cabinet_panels[gid] = {
        "channel_id": interaction.channel_id,
        "message_id": msg.id,
        "text":       prev.get("text"),
        "image_url":  prev.get("image_url"),
    }
    save_data()
    await interaction.response.send_message("✅ Личный кабинет создан.", ephemeral=True)


@tree.command(name="личный_кабинет_фото", description="Изменить фото панели личного кабинета")
@app_commands.describe(url="Ссылка на изображение")
async def slash_cabinet_photo(interaction: discord.Interaction, url: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid = interaction.guild_id
    if gid not in cabinet_panels:
        cabinet_panels[gid] = {}
    cabinet_panels[gid]["image_url"] = url
    save_data()
    await _refresh_cabinet_panel(interaction.guild)
    await interaction.response.send_message("✅ Фото кабинета обновлено!", ephemeral=True)


@tree.command(name="личный_кабинет_текст", description="Изменить текст описания личного кабинета")
@app_commands.describe(текст="Текст под заголовком")
async def slash_cabinet_text(interaction: discord.Interaction, текст: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid = interaction.guild_id
    if gid not in cabinet_panels:
        cabinet_panels[gid] = {}
    cabinet_panels[gid]["text"] = текст
    save_data()
    await _refresh_cabinet_panel(interaction.guild)
    await interaction.response.send_message("✅ Текст кабинета обновлён!", ephemeral=True)


@tree.command(name="пригласительная_ссылка", description="Установить пригласительную ссылку для кнопки в личном кабинете")
@app_commands.describe(ссылка="Ссылка-приглашение на сервер")
async def slash_cabinet_invite(interaction: discord.Interaction, ссылка: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    cabinet_invite_links[interaction.guild_id] = ссылка
    save_data()
    await interaction.response.send_message(f"✅ Пригласительная ссылка установлена: `{ссылка}`", ephemeral=True)


# ─────────────────────────────────────────────
# ГОЛОСОВЫЕ КАНАЛЫ — НАЧИСЛЕНИЕ ВАЛЮТЫ
# ─────────────────────────────────────────────

def _member_is_muted(member: discord.Member) -> bool:
    """True если участник в муте (сам или сервером) или заглушён."""
    v = member.voice
    if v is None:
        return True
    return v.self_mute or v.mute or v.self_deaf or v.deaf


@tasks.loop(minutes=1)
async def voice_reward_loop():
    for guild in bot.guilds:
        settings = voice_reward_settings.get(guild.id)
        if not settings:
            continue
        categories   = settings.get("categories", [])
        excluded     = set(settings.get("excluded_channels", []))
        amount       = settings.get("amount", 10)
        amount_game  = settings.get("amount_game", amount)
        game_name    = settings.get("game_name", "GTA5RP")

        if not categories or amount <= 0:
            continue

        for cat_id in categories:
            category = guild.get_channel(cat_id)
            if not category or not isinstance(category, discord.CategoryChannel):
                continue

            for vc in category.voice_channels:
                if vc.id in excluded:
                    continue

                members = [m for m in vc.members if not m.bot]
                if len(members) < 2:
                    continue

                if any(_member_is_muted(m) for m in members):
                    continue

                for m in members:
                    if _member_playing_game(m, game_name):
                        add_points(guild.id, m.id, amount_game)
                    elif amount > 0:
                        add_points(guild.id, m.id, amount)

        save_data()


# Счётчик тиков для проверки "в игре, не в войсе"
_game_check_ticks: dict = {}


@tasks.loop(minutes=1)
async def game_activity_check_loop():
    global _game_check_ticks
    for guild in bot.guilds:
        s = voice_reward_settings.get(guild.id)
        if not s:
            continue
        log_ch_id = s.get("game_log_channel")
        if not log_ch_id:
            continue
        interval = s.get("game_check_interval", 10)
        game_name = s.get("game_name", "GTA5RP")

        tick = _game_check_ticks.get(guild.id, 0) + 1
        _game_check_ticks[guild.id] = tick
        if tick < interval:
            continue
        _game_check_ticks[guild.id] = 0

        categories = s.get("categories", [])
        excluded   = set(s.get("excluded_channels", []))

        # Собираем участников, которые сейчас в отслеживаемых войс-каналах
        in_voice_ids: set = set()
        for cat_id in categories:
            category = guild.get_channel(cat_id)
            if not category or not isinstance(category, discord.CategoryChannel):
                continue
            for vc in category.voice_channels:
                if vc.id in excluded:
                    continue
                for m in vc.members:
                    if not m.bot:
                        in_voice_ids.add(m.id)

        # Фильтр по ролям фамы (если задан)
        watch_role_ids = set(s.get("game_watch_roles", []))

        def _passes_filter(m: discord.Member) -> bool:
            if not watch_role_ids:
                return True
            return any(r.id in watch_role_ids for r in m.roles)

        # Ищем участников в игре, но не в войсе
        in_game_not_voice = [
            m for m in guild.members
            if not m.bot and m.id not in in_voice_ids
            and _member_playing_game(m, game_name)
            and _passes_filter(m)
        ]
        if not in_game_not_voice:
            continue

        log_channel = guild.get_channel(log_ch_id)
        if not log_channel:
            continue

        count = len(in_game_not_voice)
        lines = "\n".join(f"• {m.mention}" for m in in_game_not_voice)
        embed = discord.Embed(
            title=f"🎮 Не в войсе — {count} чел.",
            description=lines,
            color=discord.Color.orange(),
            timestamp=datetime.now(),
        )
        embed.set_footer(text="MORIARTY", icon_url=_footer(guild.id))
        try:
            await log_channel.send(embed=embed)
        except Exception:
            pass


def _get_voice_settings(guild_id: int) -> dict:
    if guild_id not in voice_reward_settings:
        voice_reward_settings[guild_id] = {
            "categories": [],
            "excluded_channels": [],
            "amount": 10,
            "amount_game": 15,
            "game_name": "RAGE Multiplayer",
            "game_log_channel": None,
            "game_check_interval": 10,
            "game_watch_roles": [],
        }
    s = voice_reward_settings[guild_id]
    s.setdefault("amount_game", s.get("amount", 10) + 5)
    s.setdefault("game_name", "GTA5RP")
    s.setdefault("game_log_channel", None)
    s.setdefault("game_check_interval", 10)
    s.setdefault("game_watch_roles", [])
    return s


def _member_playing_game(member: discord.Member, game_name: str) -> bool:
    name_lower = game_name.lower()
    for act in member.activities:
        if isinstance(act, discord.Game) and name_lower in act.name.lower():
            return True
        if isinstance(act, discord.Activity) and name_lower in act.name.lower():
            return True
    return False


@tree.command(name="войс_категория", description="Добавить категорию для начисления валюты за голосовые каналы")
@app_commands.describe(категория="Категория с голосовыми каналами")
async def slash_voice_add_category(interaction: discord.Interaction, категория: discord.CategoryChannel):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    s = _get_voice_settings(interaction.guild_id)
    if категория.id in s["categories"]:
        return await interaction.response.send_message(
            f"❌ Категория **{категория.name}** уже добавлена.", ephemeral=True
        )
    s["categories"].append(категория.id)
    save_data()
    await interaction.response.send_message(
        f"✅ Категория **{категория.name}** добавлена. Голосовые каналы в ней начнут приносить 💎.", ephemeral=True
    )


@tree.command(name="войс_убрать_категорию", description="Убрать категорию из начисления валюты")
@app_commands.describe(категория="Категория для удаления")
async def slash_voice_remove_category(interaction: discord.Interaction, категория: discord.CategoryChannel):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    s = _get_voice_settings(interaction.guild_id)
    if категория.id not in s["categories"]:
        return await interaction.response.send_message(
            f"❌ Категория **{категория.name}** не найдена в списке.", ephemeral=True
        )
    s["categories"].remove(категория.id)
    save_data()
    await interaction.response.send_message(
        f"✅ Категория **{категория.name}** убрана.", ephemeral=True
    )


@tree.command(name="войс_исключить", description="Исключить конкретный голосовой канал из начисления")
@app_commands.describe(канал="Голосовой канал для исключения")
async def slash_voice_exclude(interaction: discord.Interaction, канал: discord.VoiceChannel):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    s = _get_voice_settings(interaction.guild_id)
    if канал.id in s["excluded_channels"]:
        return await interaction.response.send_message(
            f"❌ Канал {канал.mention} уже исключён.", ephemeral=True
        )
    s["excluded_channels"].append(канал.id)
    save_data()
    await interaction.response.send_message(
        f"✅ Канал {канал.mention} исключён из начисления.", ephemeral=True
    )


@tree.command(name="войс_включить", description="Вернуть исключённый голосовой канал в начисление")
@app_commands.describe(канал="Голосовой канал для возврата")
async def slash_voice_include(interaction: discord.Interaction, канал: discord.VoiceChannel):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    s = _get_voice_settings(interaction.guild_id)
    if канал.id not in s["excluded_channels"]:
        return await interaction.response.send_message(
            f"❌ Канал {канал.mention} не был исключён.", ephemeral=True
        )
    s["excluded_channels"].remove(канал.id)
    save_data()
    await interaction.response.send_message(
        f"✅ Канал {канал.mention} возвращён в начисление.", ephemeral=True
    )


@tree.command(name="войс_сумма", description="Сколько 💎 начислять каждую минуту за активность в войсе")
@app_commands.describe(сумма="Количество баллов в минуту (по умолчанию 10)")
async def slash_voice_amount(interaction: discord.Interaction, сумма: int):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    if сумма < 1:
        return await interaction.response.send_message("❌ Сумма должна быть больше 0.", ephemeral=True)
    s = _get_voice_settings(interaction.guild_id)
    s["amount"] = сумма
    save_data()
    await interaction.response.send_message(
        f"✅ Начисление: **{сумма}** 💎 в минуту за активность в войсе.", ephemeral=True
    )


@tree.command(name="войс_настройки", description="Показать настройки начисления за голосовые каналы")
async def slash_voice_settings(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    s = voice_reward_settings.get(interaction.guild_id, {})

    cats = s.get("categories", [])
    excl = s.get("excluded_channels", [])
    amt  = s.get("amount", 10)

    cats_text = (
        "\n".join(
            f"• {interaction.guild.get_channel(c).name if interaction.guild.get_channel(c) else f'[{c}]'}"
            for c in cats
        ) or "—"
    )
    excl_text = (
        "\n".join(
            f"• <#{c}>" for c in excl
        ) or "—"
    )

    embed = discord.Embed(title="🎙 Начисление за голосовые каналы", color=0x5865F2, timestamp=datetime.now())
    embed.add_field(name="💎 Баллов в минуту", value=str(amt), inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.add_field(name="📂 Категории", value=cats_text, inline=False)
    embed.add_field(name="🚫 Исключённые каналы", value=excl_text, inline=False)
    embed.add_field(
        name="📋 Правила",
        value=(
            "• 1 человек → ❌ нет начисления\n"
            "• 2+ все без мута → ✅ все получают\n"
            "• Хотя бы 1 в муте → ❌ никто не получает"
        ),
        inline=False,
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─────────────────────────────────────────────
# /активность — панель настройки
# ─────────────────────────────────────────────

def _build_activity_embed(guild: discord.Guild) -> discord.Embed:
    s = _get_voice_settings(guild.id)
    cats = s.get("categories", [])
    excl = s.get("excluded_channels", [])
    log_ch_id = s.get("game_log_channel")
    watch_role_ids = s.get("game_watch_roles", [])

    cats_text = (
        "\n".join(
            f"• {guild.get_channel(c).name if guild.get_channel(c) else f'[{c}]'}"
            for c in cats
        ) or "—"
    )
    excl_text  = "\n".join(f"• <#{c}>" for c in excl) or "—"
    log_text   = f"<#{log_ch_id}>" if log_ch_id else "не задан"
    roles_text = "\n".join(f"• <@&{r}>" for r in watch_role_ids) or "все участники"

    embed = discord.Embed(
        title="🎙 Настройки активности",
        color=0x5865F2,
        timestamp=datetime.now(),
    )
    embed.add_field(name="💎 Войс без игры", value=f"**{s.get('amount', 10)}** /мин", inline=True)
    embed.add_field(name="💎 Войс + игра", value=f"**{s.get('amount_game', 15)}** /мин", inline=True)
    embed.add_field(name="🕹 Название игры", value=s.get("game_name", "RAGE Multiplayer"), inline=True)
    embed.add_field(name="🔔 Лог «в игре, не в войсе»", value=log_text, inline=True)
    embed.add_field(name="⏱ Интервал проверки", value=f"**{s.get('game_check_interval', 10)}** мин", inline=True)
    embed.add_field(name="👥 Фильтр по ролям", value=roles_text, inline=True)
    embed.add_field(name="📂 Категории войса", value=cats_text, inline=False)
    embed.add_field(name="🚫 Исключённые каналы", value=excl_text, inline=False)
    embed.set_footer(text="MORIARTY", icon_url=_footer(guild.id))
    return embed


class ActivityRatesModal(ui.Modal, title="💎 Настройки активности"):
    amount = ui.TextInput(
        label="Алмазы/мин (войс без игры)",
        placeholder="10",
        required=True,
        max_length=6,
    )
    amount_game = ui.TextInput(
        label="Алмазы/мин (войс + игра)",
        placeholder="15",
        required=True,
        max_length=6,
    )
    game_name = ui.TextInput(
        label="Название игры (для детекции)",
        placeholder="RAGE Multiplayer",
        required=True,
        max_length=64,
    )
    game_check_interval = ui.TextInput(
        label="Интервал проверки «в игре, не в войсе» (мин)",
        placeholder="10",
        required=True,
        max_length=4,
    )

    def __init__(self, orig_msg: discord.Message):
        super().__init__()
        self._orig = orig_msg

    async def on_submit(self, interaction: discord.Interaction):
        s = _get_voice_settings(interaction.guild_id)
        try:
            s["amount"] = max(0, int(str(self.amount).strip()))
        except ValueError:
            pass
        try:
            s["amount_game"] = max(1, int(str(self.amount_game).strip()))
        except ValueError:
            pass
        s["game_name"] = str(self.game_name).strip() or "GTA5RP"
        try:
            s["game_check_interval"] = max(1, int(str(self.game_check_interval).strip()))
        except ValueError:
            pass
        save_data()
        await interaction.response.edit_message(embed=_build_activity_embed(interaction.guild), view=ActivityView(interaction.guild))


class ActivityLogChannelSelect(ui.ChannelSelect):
    def __init__(self, orig_msg: discord.Message):
        super().__init__(
            placeholder="📢 Выбрать лог-канал «в игре, не в войсе»",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            row=1,
        )
        self._orig = orig_msg

    async def callback(self, interaction: discord.Interaction):
        s = _get_voice_settings(interaction.guild_id)
        s["game_log_channel"] = self.values[0].id
        save_data()
        await interaction.response.edit_message(embed=_build_activity_embed(interaction.guild), view=ActivityView(interaction.guild))


class ActivityCategoryAddSelect(ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="➕ Добавить категорию войса",
            channel_types=[discord.ChannelType.category],
            min_values=1,
            max_values=1,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        s = _get_voice_settings(interaction.guild_id)
        cat_id = self.values[0].id
        if cat_id not in s["categories"]:
            s["categories"].append(cat_id)
            save_data()
        await interaction.response.edit_message(embed=_build_activity_embed(interaction.guild), view=ActivityView(interaction.guild))


class ActivityCategoryRemoveSelect(ui.Select):
    def __init__(self, guild: discord.Guild):
        s = _get_voice_settings(guild.id)
        options = []
        for cat_id in s.get("categories", []):
            ch = guild.get_channel(cat_id)
            name = ch.name if ch else str(cat_id)
            options.append(discord.SelectOption(label=name, value=str(cat_id), emoji="➖"))
        if not options:
            options = [discord.SelectOption(label="(нет категорий)", value="none", emoji="❌")]
        super().__init__(placeholder="➖ Убрать категорию войса", options=options, min_values=1, max_values=1, row=3)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            return await interaction.response.defer()
        s = _get_voice_settings(interaction.guild_id)
        cat_id = int(self.values[0])
        if cat_id in s["categories"]:
            s["categories"].remove(cat_id)
            save_data()
        await interaction.response.edit_message(embed=_build_activity_embed(interaction.guild), view=ActivityView(interaction.guild))


class ActivityWatchRoleSelect(ui.RoleSelect):
    def __init__(self):
        super().__init__(
            placeholder="👥 Добавить роль фильтра фамы",
            min_values=1,
            max_values=5,
            row=4,
        )

    async def callback(self, interaction: discord.Interaction):
        s = _get_voice_settings(interaction.guild_id)
        for role in self.values:
            if role.id not in s["game_watch_roles"]:
                s["game_watch_roles"].append(role.id)
        save_data()
        await interaction.response.edit_message(embed=_build_activity_embed(interaction.guild), view=ActivityView(interaction.guild))


class ActivityView(ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=300)
        self.add_item(ActivityLogChannelSelect(None))
        self.add_item(ActivityCategoryAddSelect())
        self.add_item(ActivityCategoryRemoveSelect(guild))
        self.add_item(ActivityWatchRoleSelect())

    @ui.button(label="✏️ Ставки и игра", style=discord.ButtonStyle.primary, row=0)
    async def btn_rates(self, interaction: discord.Interaction, button: ui.Button):
        s = _get_voice_settings(interaction.guild_id)
        modal = ActivityRatesModal(None)
        modal.amount.default              = str(s.get("amount", 10))
        modal.amount_game.default         = str(s.get("amount_game", 15))
        modal.game_name.default           = s.get("game_name", "RAGE Multiplayer")
        modal.game_check_interval.default = str(s.get("game_check_interval", 10))
        await interaction.response.send_modal(modal)

    @ui.button(label="🔕 Убрать лог-канал", style=discord.ButtonStyle.danger, row=0)
    async def btn_remove_log(self, interaction: discord.Interaction, button: ui.Button):
        s = _get_voice_settings(interaction.guild_id)
        s["game_log_channel"] = None
        save_data()
        await interaction.response.edit_message(embed=_build_activity_embed(interaction.guild), view=ActivityView(interaction.guild))

    @ui.button(label="🗑 Сбросить фильтр ролей", style=discord.ButtonStyle.danger, row=0)
    async def btn_clear_roles(self, interaction: discord.Interaction, button: ui.Button):
        s = _get_voice_settings(interaction.guild_id)
        s["game_watch_roles"] = []
        save_data()
        await interaction.response.edit_message(embed=_build_activity_embed(interaction.guild), view=ActivityView(interaction.guild))


@tree.command(name="активность", description="Настройки начисления алмазов за голосовую активность")
async def slash_activity_settings(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    embed = _build_activity_embed(interaction.guild)
    view  = ActivityView(interaction.guild)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ─────────────────────────────────────────────
# ОБЩАК
# ─────────────────────────────────────────────

DEFAULT_OBSHAK_TEXT = "Система пополнения общака семьи."


def build_obshak_embed(guild_id: int) -> discord.Embed:
    settings = obshak_panels.get(guild_id, {})
    text      = settings.get("text") or DEFAULT_OBSHAK_TEXT
    image_url = settings.get("image_url")

    embed = discord.Embed(
        title="💰 ОБЩАК СЕМЬИ",
        description=text,
        color=0xf1c40f,
    )
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
    return embed


async def _refresh_obshak_panel(guild: discord.Guild):
    settings = obshak_panels.get(guild.id)
    if not settings or not settings.get("message_id"):
        return
    try:
        ch  = guild.get_channel(settings["channel_id"])
        msg = await ch.fetch_message(settings["message_id"])
        await msg.edit(embed=build_obshak_embed(guild.id), view=ObshakView())
    except Exception:
        pass


class ObshakDepositModal(ui.Modal, title="Пополнение общака"):
    amount_input = ui.TextInput(
        label="Сумма пополнения ($)",
        placeholder="50000",
        min_length=1,
        max_length=12,
    )

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.amount_input.value.strip().replace(".", "").replace(",", "").replace(" ", "")
        if not raw.isdigit():
            return await interaction.response.send_message("❌ Введите числовое значение.", ephemeral=True)
        amount = int(raw)
        if amount <= 0:
            return await interaction.response.send_message("❌ Сумма должна быть больше 0.", ephemeral=True)
        if amount > 100_000_000:
            return await interaction.response.send_message(
                "❌ Максимальная сумма одного взноса — **100.000.000$**.", ephemeral=True
            )

        guild_id = interaction.guild_id
        if guild_id not in obshak_deposits:
            obshak_deposits[guild_id] = []

        obshak_deposits[guild_id].append({
            "user_id": interaction.user.id,
            "amount":  amount,
            "date":    datetime.now().isoformat(),
        })
        save_obshak()

        await _refresh_obshak_panel(interaction.guild)

        log_ch_id = obshak_log_channels.get(guild_id)
        if log_ch_id:
            log_ch = interaction.guild.get_channel(log_ch_id)
            if log_ch:
                try:
                    ping_role_id = obshak_ping_roles.get(guild_id)
                    ping_str = f"<@&{ping_role_id}>\n" if ping_role_id else ""
                    await log_ch.send(
                        f"{ping_str}[+{format_amount(amount)}$] {interaction.user.mention} пополнил баланс организации"
                    )
                except Exception:
                    pass

        await interaction.response.send_message(
            f"✅ Пополнение на **{format_amount(amount)}$** зафиксировано!",
            ephemeral=True,
        )


class ObshakView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="💳 Пополнить", style=discord.ButtonStyle.success, custom_id="obshak_deposit")
    async def deposit_btn(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(ObshakDepositModal())


@bot.command(name="общак")
async def obshak_panel_cmd(ctx):
    """!общак — создать/обновить панель общака в этом канале"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()

    guild_id = ctx.guild.id

    # Удалить старую панель если есть
    existing = obshak_panels.get(guild_id)
    if existing and existing.get("message_id"):
        try:
            old_ch  = ctx.guild.get_channel(existing["channel_id"])
            old_msg = await old_ch.fetch_message(existing["message_id"])
            await old_msg.delete()
        except Exception:
            pass

    # Сохраняем text/image_url если они уже были
    prev = obshak_panels.get(guild_id, {})
    embed = build_obshak_embed(guild_id)
    msg   = await ctx.send(embed=embed, view=ObshakView())

    obshak_panels[guild_id] = {
        "channel_id": ctx.channel.id,
        "message_id": msg.id,
        "text":       prev.get("text"),
        "image_url":  prev.get("image_url"),
    }
    save_data()
    try:
        await ctx.message.delete()
    except Exception:
        pass


@tree.command(name="общак_фото", description="Изменить фото панели общака")
@app_commands.describe(url="Ссылка на изображение")
async def slash_obshak_photo(interaction: discord.Interaction, url: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid = interaction.guild_id
    if gid not in obshak_panels:
        obshak_panels[gid] = {}
    obshak_panels[gid]["image_url"] = url
    save_data()
    await _refresh_obshak_panel(interaction.guild)
    await interaction.response.send_message("✅ Фото панели обновлено!", ephemeral=True)


@tree.command(name="общак_текст", description="Изменить текст описания панели общака")
@app_commands.describe(текст="Текст под заголовком")
async def slash_obshak_text(interaction: discord.Interaction, текст: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid = interaction.guild_id
    if gid not in obshak_panels:
        obshak_panels[gid] = {}
    obshak_panels[gid]["text"] = текст
    save_data()
    await _refresh_obshak_panel(interaction.guild)
    await interaction.response.send_message("✅ Текст панели обновлён!", ephemeral=True)


@tree.command(name="общак_логи", description="Канал для логов пополнений общака")
@app_commands.describe(канал="Текстовый канал")
async def slash_obshak_logs(interaction: discord.Interaction, канал: discord.TextChannel):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    obshak_log_channels[interaction.guild_id] = канал.id
    save_data()
    await interaction.response.send_message(
        f"✅ Логи пополнений общака будут отправляться в {канал.mention}.", ephemeral=True
    )


@tree.command(name="общак_пинг", description="Роль для тега в логах пополнений общака")
@app_commands.describe(роль="Роль которую тегать при каждом пополнении (None — убрать тег)")
async def slash_obshak_ping(interaction: discord.Interaction, роль: discord.Role = None):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    if роль:
        obshak_ping_roles[interaction.guild_id] = роль.id
        save_data()
        await interaction.response.send_message(
            f"✅ При каждом пополнении будет тегаться {роль.mention}.", ephemeral=True
        )
    else:
        obshak_ping_roles.pop(interaction.guild_id, None)
        save_data()
        await interaction.response.send_message("✅ Тег роли в логах общака убран.", ephemeral=True)


def _build_obshak_stats_embed(
    guild_id: int,
    deposits: list,
    title: str,
    period_label: str,
) -> discord.Embed:
    totals: dict[int, int] = {}
    for d in deposits:
        uid = d["user_id"]
        totals[uid] = totals.get(uid, 0) + d["amount"]

    sorted_users = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    total_sum = sum(totals.values())

    embed = discord.Embed(
        title=title,
        description=f"**Период:** {period_label}\n**Итого:** {format_amount(total_sum)}$\n\u200b",
        color=0xf1c40f,
        timestamp=datetime.now(),
    )

    if not sorted_users:
        embed.add_field(name="—", value="Пополнений нет.", inline=False)
    else:
        lines = [
            f"**{i}.** <@{uid}> — **{format_amount(amt)}$**"
            for i, (uid, amt) in enumerate(sorted_users, 1)
        ]
        # Разбиваем на chunks если текст больше 1024 символов
        chunk: list[str] = []
        chunks: list[str] = []
        for line in lines:
            if sum(len(l) + 1 for l in chunk) + len(line) > 1000:
                chunks.append("\n".join(chunk))
                chunk = [line]
            else:
                chunk.append(line)
        if chunk:
            chunks.append("\n".join(chunk))
        for idx, text in enumerate(chunks):
            embed.add_field(
                name="Участники" if idx == 0 else "\u200b",
                value=text,
                inline=False,
            )

    embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
    return embed


@tree.command(name="общак_неделя", description="Статистика пополнений общака за текущую неделю")
async def slash_obshak_week(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    now        = datetime.now()
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end   = week_start + timedelta(days=6)
    deposits   = [
        d for d in obshak_deposits.get(interaction.guild_id, [])
        if datetime.fromisoformat(d["date"]) >= week_start
    ]
    label = f"{week_start.strftime('%d.%m.%Y')} — {week_end.strftime('%d.%m.%Y')}"
    embed = _build_obshak_stats_embed(interaction.guild_id, deposits, "📊 Общак — Неделя", label)
    await interaction.response.send_message(embed=embed)


@tree.command(name="общак_месяц", description="Статистика пополнений общака за текущий месяц")
async def slash_obshak_month(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    import calendar
    now         = datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_day    = calendar.monthrange(now.year, now.month)[1]
    month_end   = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=0)
    deposits    = [
        d for d in obshak_deposits.get(interaction.guild_id, [])
        if datetime.fromisoformat(d["date"]) >= month_start
    ]
    label = f"{month_start.strftime('%d.%m.%Y')} — {month_end.strftime('%d.%m.%Y')}"
    embed = _build_obshak_stats_embed(interaction.guild_id, deposits, "📊 Общак — Месяц", label)
    await interaction.response.send_message(embed=embed)


@tree.command(name="общак_полный", description="Полная статистика пополнений общака за всё время")
async def slash_obshak_all(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    deposits = obshak_deposits.get(interaction.guild_id, [])
    embed = _build_obshak_stats_embed(
        interaction.guild_id, deposits,
        "📊 Общак — Всё время", "С начала ведения учёта",
    )
    await interaction.response.send_message(embed=embed)



# ═══════════════════════════════════════════════════════════════
# 📋 СИСТЕМА СОСТАВА СЕМЬИ (v2)
# ═══════════════════════════════════════════════════════════════

# Все фракции: ключ = короткое имя (= имя кастомного эмодзи на сервере)
def _chunk_lines(lines: list, chunk_size: int = 10) -> list[list]:
    if not lines:
        return [[]]
    return [lines[i:i+chunk_size] for i in range(0, len(lines), chunk_size)]


async def _collect_roster_lines(guild: discord.Guild):
    """Собирает (full_lines, academy_lines) по ролям."""
    cfg             = roster_settings.get(guild.id, {})
    member_role_id  = cfg.get("member_role_id")
    academy_role_id = cfg.get("academy_role_id")

    members_list = guild.members
    if len(members_list) < 2:
        try:
            members_list = [m async for m in guild.fetch_members(limit=None)]
        except Exception:
            members_list = guild.members

    full_lines    = []
    academy_lines = []

    for member in members_list:
        if member.bot:
            continue
        role_ids    = {r.id for r in member.roles}
        has_member  = member_role_id  and (member_role_id  in role_ids)
        has_academy = academy_role_id and (academy_role_id in role_ids)
        if not has_member and not has_academy:
            continue

        line = f"• {member.mention}"

        if has_academy and not has_member:
            academy_lines.append(line)
        else:
            full_lines.append(line)

    return full_lines, academy_lines


async def _refresh_roster(guild: discord.Guild):
    cfg   = roster_settings.get(guild.id, {})
    ch_id = cfg.get("channel_id")
    if not ch_id:
        return
    try:
        ch = guild.get_channel(ch_id)

        # Удаляем старое сообщение
        old_id = cfg.get("message_id")
        if old_id:
            try:
                await (await ch.fetch_message(old_id)).delete()
            except Exception:
                pass

        full_lines, academy_lines = await _collect_roster_lines(guild)
        view = RosterPaginationView(guild, full_lines, academy_lines)
        msg  = await ch.send(embed=view.current_embed(), view=view)

        cfg["message_id"] = msg.id
        save_data()
    except Exception:
        pass


# ── Пагинация состава ──

class RosterPaginationView(ui.View):
    """Одно сообщение: кнопки ◀ Назад / Вперёд ▶ + счётчик страниц."""

    PAGE = 10

    def __init__(self, guild: discord.Guild, full_lines: list, academy_lines: list, idx: int = 0):
        super().__init__(timeout=None)
        self.guild         = guild
        self.full_lines    = full_lines
        self.academy_lines = academy_lines
        self.full_pages    = _chunk_lines(full_lines,    self.PAGE)
        self.acad_pages    = _chunk_lines(academy_lines, self.PAGE)
        self.total         = len(self.full_pages) + len(self.acad_pages)
        self.idx           = max(0, min(idx, self.total - 1))

        self._add_nav()

    def _add_nav(self):
        """Удаляет старые навигационные кнопки и добавляет новые."""
        for item in list(self.children):
            if isinstance(item, ui.Button):
                self.remove_item(item)

        fp = len(self.full_pages)
        ap = len(self.acad_pages)

        # Определяем подпись счётчика
        if self.idx < fp:
            if fp > 1:
                counter = f"{self.idx + 1} / {fp}  (Участники)"
            else:
                counter = "Участники"
        else:
            ai = self.idx - fp
            if ap > 1:
                counter = f"{ai + 1} / {ap}  (Академия)"
            else:
                counter = "Академия"

        prev_btn = ui.Button(
            label="◀ Назад",
            style=discord.ButtonStyle.secondary,
            disabled=self.idx == 0,
            row=0,
        )
        page_btn = ui.Button(
            label=counter,
            style=discord.ButtonStyle.primary,
            disabled=True,
            row=0,
        )
        next_btn = ui.Button(
            label="Вперёд ▶",
            style=discord.ButtonStyle.secondary,
            disabled=self.idx >= self.total - 1,
            row=0,
        )
        prev_btn.callback = self._go_prev
        next_btn.callback = self._go_next
        self.add_item(prev_btn)
        self.add_item(page_btn)
        self.add_item(next_btn)

    def current_embed(self) -> discord.Embed:
        footer_icon = _footer(self.guild.id)
        fp = len(self.full_pages)

        if self.idx < fp:
            page  = self.full_pages[self.idx]
            title = f"🏅 Участники  [{len(self.full_lines)}]"
            color = discord.Color.dark_gold()
            empty = "*Нет участников*"
        else:
            ai    = self.idx - fp
            page  = self.acad_pages[ai]
            title = f"🎓 Академия  [{len(self.academy_lines)}]"
            color = discord.Color.blue()
            empty = "*Нет академиков*"

        embed = discord.Embed(
            title=title,
            description="\n\n".join(page) if page else empty,
            color=color,
            timestamp=datetime.now(),
        )
        embed.set_footer(
            text=f"MORIARTY • Участников: {len(self.full_lines)} | Академиков: {len(self.academy_lines)}",
            icon_url=footer_icon,
        )
        return embed

    async def _go_prev(self, interaction: discord.Interaction):
        self.idx = max(0, self.idx - 1)
        self._add_nav()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def _go_next(self, interaction: discord.Interaction):
        self.idx = min(self.total - 1, self.idx + 1)
        self._add_nav()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)


# Авто-обновление при смене роли
@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    cfg             = roster_settings.get(after.guild.id, {})
    member_role_id  = cfg.get("member_role_id")
    academy_role_id = cfg.get("academy_role_id")
    if not member_role_id and not academy_role_id:
        return
    before_ids = {r.id for r in before.roles}
    after_ids  = {r.id for r in after.roles}
    watch_ids  = set(filter(None, [member_role_id, academy_role_id]))
    if watch_ids & (before_ids ^ after_ids):
        await _refresh_roster(after.guild)


# ── Команды ──

@tree.command(name="состав_настройка", description="Настроить роли и канал для состава семьи")
@app_commands.describe(
    роль_участника="Роль полноценных участников (Moriarty)",
    роль_академии="Роль академиков (Moriarty Academy)",
    канал="Канал где будет жить состав",
)
@app_commands.default_permissions(administrator=True)
async def slash_roster_setup(
    interaction: discord.Interaction,
    роль_участника: discord.Role,
    роль_академии: discord.Role,
    канал: discord.TextChannel,
):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    gid = interaction.guild_id
    cfg = roster_settings.setdefault(gid, {})
    cfg["member_role_id"]  = роль_участника.id
    cfg["academy_role_id"] = роль_академии.id
    cfg["channel_id"]      = канал.id
    await _refresh_roster(interaction.guild)
    await interaction.followup.send(
        f"✅ Состав настроен! Панель отправлена в {канал.mention}", ephemeral=True
    )


@tree.command(name="состав", description="Показать состав семьи")
async def slash_roster(interaction: discord.Interaction):
    await interaction.response.defer()
    full_lines, academy_lines = await _collect_roster_lines(interaction.guild)
    view  = RosterPaginationView(interaction.guild, full_lines, academy_lines)
    embed = view.current_embed()
    await interaction.followup.send(embed=embed, view=view)




# ═══════════════════════════════════════════════════════════════
# ⚠️ ОБНОВЛЁННЫЙ WARNLIST — красивый живой embed
# ═══════════════════════════════════════════════════════════════

def _build_warnlist_embed(guild: discord.Guild) -> discord.Embed:
    guild_warns = warns_db.get(guild.id, {})
    active = {uid: d for uid, d in guild_warns.items() if d.get("warns", 0) > 0}

    embed = discord.Embed(
        title="⚠️ Список варнов",
        color=discord.Color.red(),
        timestamp=datetime.now(),
    )
    embed.set_footer(
        text=f"MORIARTY • Варнов выдано: {len(active)}",
        icon_url=_footer(guild.id),
    )

    if not active:
        embed.description = "✅ Ни у кого нет варнов!"
        return embed

    WARN_EMOJI = {1: "🟡", 2: "🟠", 3: "🔴"}
    lines = []
    for uid, d in sorted(active.items(), key=lambda x: x[1]["warns"], reverse=True):
        count    = d["warns"]
        emoji    = WARN_EMOJI.get(count, "⚠️")
        ts       = d.get("timestamp")
        date_str = ts.strftime("%d.%m.%Y") if isinstance(ts, datetime) else str(ts)[:10]
        lines.append(
            f"{emoji} <@{uid}> — **{count}/3**\n"
            f"└ Причина: {d['reason']} | <@{d['moderator']}> | {date_str}"
        )

    embed.description = "\n\n".join(lines)
    return embed


class WarnListView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🔄 Обновить", style=discord.ButtonStyle.secondary, custom_id="warnlist_refresh")
    async def refresh(self, interaction: discord.Interaction, button: ui.Button):
        embed = _build_warnlist_embed(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    @ui.button(label="👤 Мои варны", style=discord.ButtonStyle.primary, custom_id="warnlist_mine")
    async def my_warns(self, interaction: discord.Interaction, button: ui.Button):
        data = get_warns(interaction.guild_id, interaction.user.id)
        if not data or data.get("warns", 0) == 0:
            return await interaction.response.send_message("✅ У вас нет варнов!", ephemeral=True)
        count = data["warns"]
        WARN_EMOJI = {1: "🟡", 2: "🟠", 3: "🔴"}
        embed = discord.Embed(
            title="⚠️ Ваши варны",
            description=(
                f"{WARN_EMOJI.get(count, '⚠️')} Варнов: **{count}/3**\n"
                f"Причина: {data['reason']}\n"
                f"Модератор: <@{data['moderator']}>"
            ),
            color=discord.Color.orange(),
            timestamp=datetime.now(),
        )
        embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.command(name="warnlist")
async def warnlist(ctx):
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    embed = _build_warnlist_embed(ctx.guild)
    await ctx.send(embed=embed, view=WarnListView())
    await ctx.message.delete()


# ═══════════════════════════════════════════════════════════════
# 🎰 РУЛЕТКА
# ═══════════════════════════════════════════════════════════════
import random
from collections import defaultdict



# { "guild_id:user_id": { "games": int, "wins": int, "loses": int, "profit": int } }
roulette_stats: dict = {}

# Кулдауны { guild_id: { user_id: datetime } }
roulette_cd: dict = {}

ROULETTE_MIN   = 10
ROULETTE_CD_S  = 5   # секунд

# Числа рулетки по цветам
RED_NUMBERS   = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
BLACK_NUMBERS = {2,4,6,8,10,11,13,15,17,20,22,24,26,28,29,31,33,35}


# ───────────────── Хранилище ──────────────────
def _roulette_key(guild_id: int, user_id: int) -> str:
    return f"{guild_id}:{user_id}"


def save_roulette():
    try:
        data = {"stats": roulette_stats}
        with open(ROULETTE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"WARNING: Failed to save roulette: {e}")


def load_roulette():
    try:
        if not os.path.exists(ROULETTE_FILE):
            return
        with open(ROULETTE_FILE, "r", encoding="utf-8") as f:
            doc = json.load(f)
        roulette_stats.update(doc.get("stats", {}))
        print("OK: Roulette stats loaded from roulette.json")
    except Exception as e:
        print(f"WARNING: Failed to load roulette stats: {e}")


def _get_stats(guild_id: int, user_id: int) -> dict:
    key = _roulette_key(guild_id, user_id)
    if key not in roulette_stats:
        roulette_stats[key] = {"games": 0, "wins": 0, "loses": 0, "profit": 0}
    return roulette_stats[key]


def _update_stats(guild_id: int, user_id: int, won: bool, profit_delta: int):
    s = _get_stats(guild_id, user_id)
    s["games"]  += 1
    s["wins"]   += 1 if won else 0
    s["loses"]  += 0 if won else 1
    s["profit"] += profit_delta
    save_roulette()


# ───────────────── Логика рулетки ──────────────────
def _spin() -> int:
    return random.randint(0, 36)


def _number_color(n: int) -> str:
    if n == 0:          return "зелёное"
    if n in RED_NUMBERS:  return "красное"
    return "чёрное"


def _color_emoji(color: str) -> str:
    return {"красное": "🔴", "чёрное": "⚫", "зелёное": "🟢"}.get(color, "⚪")


def _fmt(n: int) -> str:
    """Форматирование числа с разрядами: 1000 → 1 000"""
    return f"{n:,}".replace(",", " ")


def _parse_bet(raw: str):
    """
    Возвращает ("color", str) | ("number", int) | ("sector", (lo, hi)) | None.
    """
    low = raw.lower()
    if low in ("красное", "красный"):
        return ("color", "красное")
    if low in ("чёрное", "чёрный", "черное", "черный"):
        return ("color", "чёрное")
    if low in ("д1", "1-12"):
        return ("sector", (1, 12))
    if low in ("д2", "13-24"):
        return ("sector", (13, 24))
    if low in ("д3", "25-36"):
        return ("sector", (25, 36))
    try:
        n = int(raw)
        if 0 <= n <= 36:
            return ("number", n)
    except ValueError:
        pass
    return None


# ───────────────── Проверка кулдауна ──────────────────
def _check_cd(guild_id: int, user_id: int) -> float:
    """Возвращает оставшиеся секунды кулдауна (0 = можно играть)."""
    last = roulette_cd.get(guild_id, {}).get(user_id)
    if not last:
        return 0.0
    elapsed = (datetime.now() - last).total_seconds()
    return max(0.0, ROULETTE_CD_S - elapsed)


def _set_cd(guild_id: int, user_id: int):
    if guild_id not in roulette_cd:
        roulette_cd[guild_id] = {}
    roulette_cd[guild_id][user_id] = datetime.now()


# ───────────────── Подкрутка по роли ──────────────────
def _get_role_luck(guild_id: int, member: discord.Member) -> float:
    role_luck = casino_role_luck.get(guild_id, {})
    for role in member.roles:
        if role.id in role_luck:
            return role_luck[role.id]
    return 1.0


def _biased_outcome(bet_kind, bet_val, luck: float) -> tuple:
    """
    Возвращает (displayed_number, won).
    Число всегда соответствует итогу — внешне выглядит честно.
    luck=1.0 → честная вероятность, <1 → хуже, >1 → лучше.
    """
    if bet_kind == "color":
        base_prob = 18 / 37
        win_pool  = list(RED_NUMBERS if bet_val == "красное" else BLACK_NUMBERS)
        lose_pool = list(BLACK_NUMBERS if bet_val == "красное" else RED_NUMBERS) + [0]
    elif bet_kind == "number":
        base_prob = 1 / 37
        win_pool  = [bet_val]
        lose_pool = [n for n in range(37) if n != bet_val]
    else:  # sector
        lo, hi    = bet_val
        base_prob = 12 / 37
        win_pool  = list(range(lo, hi + 1))
        lose_pool = [n for n in range(37) if n < lo or n > hi]

    adj_prob = min(base_prob * luck, 0.97)
    won      = random.random() < adj_prob
    display  = random.choice(win_pool if won else lose_pool)
    return display, won


# ───────────────── Команда !рулетка ──────────────────
@bot.command(name="рулетка")
async def roulette_cmd(ctx, bet_type: str = None, amount: str = None):
    guild_id = ctx.guild.id
    user_id  = ctx.author.id

    # Валидация аргументов
    if not bet_type or not amount:
        embed = discord.Embed(
            title="🎰 Рулетка — Помощь",
            description=(
                "**Формат:** `!рулетка <ставка> <сумма>`\n\n"
                "**Ставки:**\n"
                "• `красное` — цвет x2\n"
                "• `чёрное` — цвет x2\n"
                "• `0-36` — число x35\n"
                "• `д1` / `1-12` — сектор 1–12 x3\n"
                "• `д2` / `13-24` — сектор 13–24 x3\n"
                "• `д3` / `25-36` — сектор 25–36 x3\n\n"
                f"**Мин. ставка:** {_fmt(ROULETTE_MIN)} 🎰\n\n"
                "**Ставки в фишках** (обмен: `!обмен <сумма>`)\n\n"
                "**Примеры:**\n"
                "`!рулетка красное 100`\n"
                "`!рулетка 17 50`\n"
                "`!рулетка д2 500`"
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
        return await ctx.send(embed=embed)

    # Кулдаун
    cd_left = _check_cd(guild_id, user_id)
    if cd_left > 0:
        return await ctx.send(
            f"⏳ {ctx.author.mention}, подожди ещё **{cd_left:.1f} сек.** перед следующей ставкой.",
            delete_after=4,
        )

    # Разбор ставки
    parsed = _parse_bet(bet_type)
    if not parsed:
        return await ctx.send(
            f"❌ Неверный тип ставки. Укажи: `красное`, `чёрное` или число `0-36`.",
            delete_after=6,
        )

    # Разбор суммы
    try:
        stake = int(amount.replace(" ", "").replace("_", ""))
    except ValueError:
        return await ctx.send("❌ Сумма должна быть целым числом.", delete_after=6)

    if stake < ROULETTE_MIN:
        return await ctx.send(f"❌ Минимальная ставка — **{_fmt(ROULETTE_MIN)}** 🎰", delete_after=6)

    balance = get_chips(guild_id, user_id)
    if balance < stake:
        return await ctx.send(
            f"❌ Недостаточно фишек! Нужно **{_fmt(stake)}** 🎰, у вас **{_fmt(balance)}** 🎰\n"
            f"Обменяй алмазы на фишки: `!обмен <сумма>`",
            delete_after=10,
        )

    # Ставка принята — ставим кулдаун
    _set_cd(guild_id, user_id)

    # ── Анимация ──
    anim_embed = discord.Embed(
        title="🎰 Крутим рулетку...",
        description="Подождите...",
        color=0xf1c40f,
    )
    anim_embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
    msg = await ctx.send(embed=anim_embed)

    for _ in range(3):
        fake_n = random.randint(0, 36)
        fake_color = _number_color(fake_n)
        anim_embed.description = f"{_color_emoji(fake_color)} **{fake_n}**..."
        await msg.edit(embed=anim_embed)
        await asyncio.sleep(0.8)

    # ── Результат ──
    bet_kind, bet_val = parsed
    luck         = _get_role_luck(guild_id, ctx.author)
    result_n, won = _biased_outcome(bet_kind, bet_val, luck)
    result_color = _number_color(result_n)
    color_emoji  = _color_emoji(result_color)

    if bet_kind == "color":
        bet_label = f"{_color_emoji(bet_val)} {bet_val}"
        payout    = stake * 2 if won else 0
    elif bet_kind == "number":
        bet_label = f"🎯 число {bet_val}"
        payout    = stake * 35 if won else 0
    else:  # sector
        lo, hi    = bet_val
        bet_label = f"📊 сектор {lo}–{hi}"
        payout    = stake * 3 if won else 0

    profit_delta = payout - stake
    add_chips(guild_id, user_id, profit_delta)
    _update_stats(guild_id, user_id, won, profit_delta)

    new_balance = get_chips(guild_id, user_id)

    if won:
        result_color_embed = discord.Color.green()
        result_title = "🎉 ПОБЕДА!"
        result_text  = f"**+{_fmt(payout)}** 🎰"
    else:
        result_color_embed = discord.Color.red()
        result_title = "💸 ПРОИГРЫШ"
        result_text  = f"**-{_fmt(stake)}** 🎰"

    final_embed = discord.Embed(
        title=f"🎰 РУЛЕТКА — {result_title}",
        color=result_color_embed,
        timestamp=datetime.now(),
    )
    final_embed.add_field(
        name="Ставка",
        value=f"**{_fmt(stake)}** 🎰 на {bet_label}",
        inline=True,
    )
    final_embed.add_field(
        name="Выпало",
        value=f"{color_emoji} **{result_n}** ({result_color})",
        inline=True,
    )
    final_embed.add_field(
        name="Результат",
        value=result_text,
        inline=True,
    )
    final_embed.add_field(
        name="Фишки",
        value=f"**{_fmt(new_balance)}** 🎰",
        inline=False,
    )
    final_embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))

    await msg.edit(embed=final_embed)


# ───────────────── /рулетка_топ ──────────────────
@tree.command(name="рулетка_топ", description="ТОП-10 игроков рулетки по профиту")
async def slash_roulette_top(interaction: discord.Interaction):
    guild_id = interaction.guild_id

    # Фильтруем только по этому серверу
    guild_entries = [
        (key, data)
        for key, data in roulette_stats.items()
        if key.startswith(f"{guild_id}:")
    ]

    if not guild_entries:
        return await interaction.response.send_message(
            "🎰 Пока никто не играл!", ephemeral=True
        )

    # Сортируем по профиту
    guild_entries.sort(key=lambda x: x[1]["profit"], reverse=True)
    top10 = guild_entries[:10]

    medals = ["🥇", "🥈", "🥉"]
    lines  = []
    for i, (key, data) in enumerate(top10):
        user_id  = int(key.split(":")[1])
        games    = data["games"]
        wins     = data["wins"]
        profit   = data["profit"]
        winrate  = round((wins / games * 100)) if games > 0 else 0
        medal    = medals[i] if i < 3 else f"`{i+1}.`"
        profit_s = f"+{_fmt(profit)}" if profit >= 0 else f"-{_fmt(abs(profit))}"
        lines.append(
            f"{medal} <@{user_id}>\n"
            f"🎮 Игр: **{games}** • ✅ Побед: **{wins}** • 📈 WR: **{winrate}%**\n"
            f"💰 Профит: **{profit_s}** 💎\n"
        )

    embed = discord.Embed(
        title="🏆 ТОП РУЛЕТКИ",
        description="\n".join(lines),
        color=discord.Color.gold(),
        timestamp=datetime.now(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
    await interaction.response.send_message(embed=embed)


# ───────────────── /казино_шанс — задать множитель роли ──────────────────
@tree.command(name="казино_шанс", description="Задать множитель удачи в казино для роли")
@app_commands.describe(
    роль="Роль, для которой настраивается множитель",
    множитель="0.1=почти всегда проигрыш · 1.0=честно · 3.0=почти всегда победа"
)
async def slash_casino_luck_set(
    interaction: discord.Interaction,
    роль: discord.Role,
    множитель: float,
):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Нет прав.", ephemeral=True)
    if множитель < 0.0 or множитель > 10.0:
        return await interaction.response.send_message(
            "❌ Множитель должен быть от 0.0 до 10.0.", ephemeral=True
        )
    gid = interaction.guild_id
    if gid not in casino_role_luck:
        casino_role_luck[gid] = {}
    casino_role_luck[gid][роль.id] = множитель
    save_data()
    await interaction.response.send_message(
        f"✅ Роль {роль.mention}: множитель казино **{множитель}x** сохранён.",
        ephemeral=True,
    )


@tree.command(name="казино_шанс_сброс", description="Сбросить множитель удачи для роли")
@app_commands.describe(роль="Роль для сброса")
async def slash_casino_luck_reset(interaction: discord.Interaction, роль: discord.Role):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Нет прав.", ephemeral=True)
    gid = interaction.guild_id
    removed = casino_role_luck.get(gid, {}).pop(роль.id, None)
    if removed is None:
        return await interaction.response.send_message(
            f"ℹ️ У роли {роль.mention} не было настроенного множителя.", ephemeral=True
        )
    save_data()
    await interaction.response.send_message(
        f"✅ Множитель для {роль.mention} сброшен.", ephemeral=True
    )


@tree.command(name="казино_шанс_проверить", description="Проверить какой множитель применится к участнику")
@app_commands.describe(участник="Участник для проверки")
async def slash_casino_luck_check(interaction: discord.Interaction, участник: discord.Member):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Нет прав.", ephemeral=True)
    gid       = interaction.guild_id
    role_luck = casino_role_luck.get(gid, {})

    matched_role  = None
    matched_mult  = None
    for role in участник.roles:
        if role.id in role_luck:
            matched_role = role
            matched_mult = role_luck[role.id]
            break

    lines = [f"**Участник:** {участник.mention}"]
    if matched_role:
        arrow = "🔻" if matched_mult < 1.0 else "🔺"
        lines.append(f"**Активный множитель:** {arrow} **{matched_mult}x** (роль {matched_role.mention})")
    else:
        lines.append("**Активный множитель:** ⚪ **1.0x** (ни одна роль не настроена)")

    if role_luck:
        configured = []
        for role_id, mult in role_luck.items():
            r = interaction.guild.get_role(role_id)
            rname = r.mention if r else f"<удалена {role_id}>"
            has = "✅" if any(ro.id == role_id for ro in участник.roles) else "❌"
            configured.append(f"{has} {rname} — **{mult}x**")
        lines.append("\n**Все настроенные роли (✅ = есть у участника):**\n" + "\n".join(configured))

    embed = discord.Embed(
        title="🎰 Диагностика казино",
        description="\n".join(lines),
        color=discord.Color.dark_gold(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(gid))
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="казино_шанс_список", description="Список ролей с настроенным множителем в казино")
async def slash_casino_luck_list(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Нет прав.", ephemeral=True)
    gid   = interaction.guild_id
    roles = casino_role_luck.get(gid, {})
    if not roles:
        return await interaction.response.send_message(
            "ℹ️ Нет настроенных ролей.", ephemeral=True
        )
    lines = []
    for role_id, mult in roles.items():
        role = interaction.guild.get_role(role_id)
        name = role.mention if role else f"<удалена id={role_id}>"
        arrow = "🔻" if mult < 1.0 else ("🔺" if mult > 1.0 else "⚪")
        lines.append(f"{arrow} {name} — **{mult}x**")
    embed = discord.Embed(
        title="🎰 Подкрутка казино — роли",
        description="\n".join(lines),
        color=discord.Color.dark_gold(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(gid))
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ═══════════════════════════════════════════════════════════
# ⚔️  ВЗП МОНИТОРИНГ — войны семей GTA5RP (vzp-gta5rp.com)
# ═══════════════════════════════════════════════════════════

VZP_API = "https://vzp-gta5rp.com/api"


async def _vzp_get(session: aiohttp.ClientSession, path: str, **params):
    try:
        async with session.get(
            f"{VZP_API}{path}", params=params or None,
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"User-Agent": "Mozilla/5.0 (compatible; bot/1.0)"},
        ) as r:
            if r.status == 200:
                return await r.json(content_type=None)
            body = await r.text()
            print(f"VZP API error {path}: HTTP {r.status} — {body[:200]}")
    except Exception as e:
        print(f"VZP API error {path}: {type(e).__name__}: {e}")
    return None


def _vzp_unwrap(data):
    """Разворачивает обёртку {"data": [...]} если есть."""
    if isinstance(data, dict):
        for key in ("data", "items", "history", "organizations"):
            if key in data:
                return data[key]
    return data


def _vzp_mentions(guild_id: int) -> str:
    cfg = vzp_monitor_config.get(guild_id, {})
    parts = [f"<@&{r}>" for r in cfg.get("mentionRoles", [])]
    parts += [f"<@{u}>" for u in cfg.get("mentionUsers", [])]
    return " ".join(parts)


def _parse_vzp_ts(raw) -> int | None:
    """API отдаёт ISO8601 ('2026-07-28T17:57:05.000Z'), а не unix-время."""
    if not raw:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    try:
        return int(datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def _duration_str(start_ts, end_ts) -> str:
    start = _parse_vzp_ts(start_ts)
    end = _parse_vzp_ts(end_ts)
    if start is None or end is None:
        return "—"
    secs = end - start
    m, s = divmod(abs(secs), 60)
    return f"{m}м {s}с"


# Внутренние коды точек ВЗП → читаемое название района (собрано по /stats/organizations/{id}/history)
VZP_DISTRICT_NAMES = {
    "STABCITY":                 "Байкерка",
    "EL_RANCHO_SMALL_OILBASE":  "Малая нефть",
    "BANNING_ANGAR":            "Мясо",
    "SANDYSHORES":              "Сенди Шорс",
    "GHETTO_ANTS":              "Муравейник",
    "NICOLA_PLACE":             "Тупик Миррор",
    "PUERTA_DUMP":              "Мусорка",
    "ELBURRO":                  "Татушка",
    "WINDFARM":                 "Ветряки",
    "PB_LUMBER":                "Лесопилка",
    "PALETOBAY":                "Палето Бей",
    "MIRROR_PARK":              "Миррор Парк",
}


def _vzp_map_name(event: dict) -> str:
    """'NEW_S_STABCITY' + pointName 'White Water AC' → 'White Water AC — Байкерка'."""
    point = event.get("pointName") or "?"
    code = event.get("map") or ""
    key = re.sub(r"^NEW_[SB]_", "", code)
    district = VZP_DISTRICT_NAMES.get(key)
    return f"{point} — {district}" if district else point


def _player_table(players: list) -> str:
    if not players:
        return "```нет данных```"
    header = f"{'Игрок':<20} {'K':>4} {'DMG':>7} {'HIT%':>5} {'HS%':>5}"
    sep = "─" * len(header)
    rows = [header, sep]
    for p in sorted(players, key=lambda x: x.get("kills", 0), reverse=True):
        name = str(p.get("charName") or p.get("characterName") or p.get("name") or "?")[:20]
        k    = p.get("kills", 0)
        dmg  = p.get("damage", 0)
        hit  = f"{p.get('hitPercent', 0):.1f}"
        hs   = f"{p.get('hsPercent', 0):.1f}"
        rows.append(f"{name:<20} {k:>4} {dmg:>7} {hit:>5} {hs:>5}")
    return "```\n" + "\n".join(rows) + "\n```"


async def _send_war_started(guild_id: int, event: dict):
    cfg = vzp_monitor_config.get(guild_id)
    if not cfg:
        return
    ch = bot.get_channel(cfg["alertChannelId"])
    if not ch:
        return

    family_name = cfg["familyName"]
    atk_name = event.get("attackerName", "?")
    def_name = event.get("defenderName", "?")
    our_side = "ATK ⚔️" if atk_name == family_name else "DEF 🛡️"
    opponent = def_name if atk_name == family_name else atk_name

    ts = _parse_vzp_ts(event.get("startedAt"))
    ts_str = f"<t:{ts}:T>" if ts else "—"

    embed = discord.Embed(
        title=f"⚔️ ВОЙНА НАЧАЛАСЬ — {event.get('pointName', '?')}",
        color=0xFF8C00,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Наша роль",     value=our_side,                        inline=True)
    embed.add_field(name="Противник",     value=opponent,                        inline=True)
    embed.add_field(name="Карта",         value=_vzp_map_name(event),            inline=True)
    embed.add_field(name="Макс. игроков", value=str(event.get("maxPlayers","?")),inline=True)
    embed.add_field(name="Начало",        value=ts_str,                          inline=True)
    embed.set_footer(text="vzp-gta5rp.com")

    mention = _vzp_mentions(guild_id)
    await ch.send(content=mention or None, embed=embed)


async def _send_war_result(guild_id: int, event: dict):
    cfg = vzp_monitor_config.get(guild_id)
    if not cfg:
        return
    ch = bot.get_channel(cfg["resultsChannelId"])
    if not ch:
        return

    family_name = cfg["familyName"]
    atk_name    = event.get("attackerName", "?")
    def_name    = event.get("defenderName", "?")
    winner_name = event.get("winnerName")
    we_are_atk  = (atk_name == family_name)

    we_won = (winner_name == family_name)
    color  = 0x57F287 if we_won else 0xED4245
    title  = ("✅ ПОБЕДА" if we_won else "❌ ПОРАЖЕНИЕ") + f" — {event.get('pointName','?')}"

    start_ts = event.get("startedAt")
    end_ts   = event.get("endedAt")
    duration = _duration_str(start_ts, end_ts)
    start_epoch = _parse_vzp_ts(start_ts)
    end_epoch   = _parse_vzp_ts(end_ts)
    start_str = f"<t:{start_epoch}:t>" if start_epoch else "—"
    end_str   = f"<t:{end_epoch}:t>"   if end_epoch   else "—"

    embed = discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Атака",      value=atk_name,               inline=True)
    embed.add_field(name="Защита",     value=def_name,               inline=True)
    embed.add_field(name="Победитель", value=winner_name or "?",     inline=True)
    embed.add_field(name="Карта",      value=_vzp_map_name(event),   inline=True)
    embed.add_field(name="Длительность", value=duration,             inline=True)

    atk_players = event.get("attackers") or []
    def_players = event.get("defenders") or []
    atk_stats = event.get("attackerStats") or {}
    def_stats = event.get("defenderStats") or {}

    our_players,  our_stats  = (atk_players, atk_stats) if we_are_atk else (def_players, def_stats)
    enemy_players, enemy_stats = (def_players, def_stats) if we_are_atk else (atk_players, atk_stats)

    our_label = f"Наша команда ({'ATK' if we_are_atk else 'DEF'})"
    embed.add_field(
        name=our_label,
        value=f"K: **{our_stats.get('kills',0)}** | DMG: **{our_stats.get('damage',0)}** | HS: **{our_stats.get('headshots',0)}**",
        inline=False,
    )
    embed.add_field(
        name="Противник",
        value=f"K: **{enemy_stats.get('kills',0)}** | DMG: **{enemy_stats.get('damage',0)}** | HS: **{enemy_stats.get('headshots',0)}**",
        inline=False,
    )
    if our_players:
        embed.add_field(name=f"📊 {our_label}", value=_player_table(our_players), inline=False)
    if enemy_players:
        embed.add_field(name="📊 Противник",    value=_player_table(enemy_players), inline=False)

    embed.set_footer(text=f"⏱ {start_str} → {end_str} | vzp-gta5rp.com")
    await ch.send(embed=embed)


# ─── Background monitoring loop ───────────────────────────

@tasks.loop(seconds=15)
async def vzp_monitor_loop():
    import time
    now = time.time()
    async with aiohttp.ClientSession() as session:
        for guild_id, cfg in list(vzp_monitor_config.items()):
            if not cfg.get("monitoringEnabled"):
                continue
            interval = cfg.get("pollInterval", 20)
            if now - vzp_last_check.get(guild_id, 0) < interval:
                continue
            vzp_last_check[guild_id] = now

            family_name = cfg.get("familyName")
            server_id = cfg.get("serverId")
            if not family_name or not server_id:
                continue

            processed = vzp_processed_events.setdefault(guild_id, {})
            changed = False

            # API игнорирует параметр serverId — фильтруем сами, ниже.
            events = await _vzp_get(session, "/events", limit=100)
            events = _vzp_unwrap(events)
            if not isinstance(events, list):
                events = []

            for ev in events:
                eid = str(ev.get("id") or ev.get("eventId") or "")
                if not eid:
                    continue
                if ev.get("serverId") != server_id:
                    continue
                if ev.get("attackerName") != family_name and ev.get("defenderName") != family_name:
                    continue

                ended  = ev.get("endedAt")
                status = processed.get(eid)

                if ended is None and status is None:
                    processed[eid] = "notified"
                    changed = True
                    try:
                        await _send_war_started(guild_id, ev)
                    except Exception as e:
                        print(f"VZP send_started error: {e}")

                elif ended is not None and status == "notified":
                    detail = await _vzp_get(session, f"/events/{eid}")
                    detail = _vzp_unwrap(detail) or ev
                    processed[eid] = "completed"
                    changed = True
                    try:
                        await _send_war_result(guild_id, detail)
                    except Exception as e:
                        print(f"VZP send_result error: {e}")

                elif ended is not None and status is None:
                    # bot missed the start — still send result notification
                    detail = await _vzp_get(session, f"/events/{eid}")
                    detail = _vzp_unwrap(detail) or ev
                    processed[eid] = "completed"
                    changed = True
                    try:
                        await _send_war_result(guild_id, detail)
                    except Exception as e:
                        print(f"VZP send_result (missed start) error: {e}")

            if changed:
                save_data()


@vzp_monitor_loop.before_loop
async def _before_vzp():
    await bot.wait_until_ready()


# ─── Helper: parse channel id from mention or raw id ──────

def _parse_channel_id(val: str):
    cleaned = val.strip().lstrip("<#").rstrip(">")
    try:
        return int(cleaned)
    except ValueError:
        return None


# ─── Setup Modal ──────────────────────────────────────────

class VzpSetupModal(ui.Modal, title="⚔️ Настройка мониторинга ВЗП"):
    family_input = ui.TextInput(
        label="ID семьи (из ссылки на vzp-gta5rp.com)",
        placeholder="15607 или https://vzp-gta5rp.com/stats/families/15607",
        required=True,
        max_length=200,
    )
    server_input = ui.TextInput(
        label="ID сервера GTA5RP",
        placeholder="1=Downtown, 20=Murrieta, 4=Vinewood, 5=Rockford",
        required=True,
        max_length=5,
    )
    alert_input = ui.TextInput(
        label="Канал уведомлений о начале войны",
        placeholder="ID канала или #упоминание",
        required=True,
        max_length=30,
    )
    results_input = ui.TextInput(
        label="Канал результатов войны",
        placeholder="ID канала или #упоминание",
        required=True,
        max_length=30,
    )
    mentions_input = ui.TextInput(
        label="Роли/юзеры для тега (ID через запятую)",
        placeholder="123456789, 987654321",
        required=False,
        max_length=300,
    )

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.family_input.value.strip().rstrip("/")
        family_id_str = raw.split("/")[-1]
        try:
            family_id = int(family_id_str)
        except ValueError:
            await interaction.response.send_message(
                "❌ Введи числовой ID семьи или ссылку вида `vzp-gta5rp.com/stats/families/15607`.", ephemeral=True
            )
            return

        async with aiohttp.ClientSession() as session:
            org_raw = await _vzp_get(session, f"/stats/organizations/{family_id}")
        org = _vzp_unwrap(org_raw)
        if not isinstance(org, dict) or not org.get("id"):
            await interaction.response.send_message(
                f"❌ Семья с ID `{family_id}` не найдена в API.", ephemeral=True
            )
            return

        await self._apply(interaction, org)

    async def _apply(self, interaction: discord.Interaction, family: dict):
        guild_id = interaction.guild_id

        try:
            srv_id = int(self.server_input.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ ID сервера должен быть числом.", ephemeral=True)
            return

        alert_ch   = _parse_channel_id(self.alert_input.value)
        results_ch = _parse_channel_id(self.results_input.value)
        if not alert_ch or not results_ch:
            await interaction.response.send_message("❌ Не удалось распознать ID каналов.", ephemeral=True)
            return

        mention_roles, mention_users = [], []
        for part in (self.mentions_input.value or "").split(","):
            part = part.strip().lstrip("<@&># ").rstrip("> ")
            if not part:
                continue
            try:
                mid = int(part)
                if interaction.guild.get_role(mid):
                    mention_roles.append(mid)
                else:
                    mention_users.append(mid)
            except ValueError:
                pass

        vzp_monitor_config[guild_id] = {
            "familyId":         family.get("id"),
            "familyName":       family.get("name"),
            "serverId":         srv_id,
            "alertChannelId":   alert_ch,
            "resultsChannelId": results_ch,
            "mentionRoles":     mention_roles,
            "mentionUsers":     mention_users,
            "pollInterval":     20,
            "monitoringEnabled": True,
        }
        vzp_processed_events.setdefault(guild_id, {})
        save_data()

        embed = discord.Embed(title="✅ Мониторинг ВЗП настроен", color=0x57F287, timestamp=datetime.now())
        embed.add_field(name="Семья",              value=f"{family.get('name')} (ID: {family.get('id')})", inline=False)
        embed.add_field(name="Сервер GTA5RP",      value=str(srv_id),         inline=True)
        embed.add_field(name="Канал начала войны", value=f"<#{alert_ch}>",    inline=True)
        embed.add_field(name="Канал результатов",  value=f"<#{results_ch}>",  inline=True)
        embed.add_field(name="Теги ролей",  value=" ".join(f"<@&{r}>" for r in mention_roles)  or "нет", inline=True)
        embed.add_field(name="Теги юзеров", value=" ".join(f"<@{u}>"  for u in mention_users)  or "нет", inline=True)
        embed.add_field(name="Интервал",    value="20 сек", inline=True)
        embed.set_footer(text="Мониторинг запущен")
        try:
            await interaction.response.send_message(embed=embed)
        except discord.InteractionResponded:
            await interaction.followup.send(embed=embed)


# ─── Slash Commands ───────────────────────────────────────

@tree.command(name="взп-настройка", description="Настроить мониторинг войн ВЗП")
async def vzp_setup_cmd(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Нет прав.", ephemeral=True)
        return
    # Показываем список серверов перед модалом
    async with aiohttp.ClientSession() as session:
        servers = await _vzp_get(session, "/servers")
    servers = _vzp_unwrap(servers)
    modal = VzpSetupModal()
    if isinstance(servers, list) and servers:
        placeholder = ", ".join(f"{s.get('id')}={s.get('name','?')}" for s in servers[:8])
        modal.server_input.placeholder = placeholder[:100]
    await interaction.response.send_modal(modal)


@tree.command(name="взп-статус", description="Текущие настройки мониторинга ВЗП")
async def vzp_status_cmd(interaction: discord.Interaction):
    cfg = vzp_monitor_config.get(interaction.guild_id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Мониторинг не настроен. Используй `/взп-настройка`.", ephemeral=True)
        return
    embed = discord.Embed(title="⚙️ Статус мониторинга ВЗП", color=0x5865F2, timestamp=datetime.now())
    embed.add_field(name="Семья",    value=f"{cfg.get('familyName')} (ID: {cfg.get('familyId')})", inline=False)
    embed.add_field(name="Сервер",   value=str(cfg.get("serverId")),   inline=True)
    embed.add_field(name="Статус",   value="🟢 Включён" if cfg.get("monitoringEnabled") else "🔴 Остановлен", inline=True)
    embed.add_field(name="Интервал", value=f"{cfg.get('pollInterval', 20)} сек", inline=True)
    embed.add_field(name="Канал начала",     value=f"<#{cfg.get('alertChannelId')}>",   inline=True)
    embed.add_field(name="Канал результатов",value=f"<#{cfg.get('resultsChannelId')}>", inline=True)
    embed.add_field(name="Теги ролей",  value=" ".join(f"<@&{r}>" for r in cfg.get("mentionRoles",[]))  or "нет", inline=True)
    embed.add_field(name="Теги юзеров", value=" ".join(f"<@{u}>"  for u in cfg.get("mentionUsers",[]))  or "нет", inline=True)
    embed.add_field(name="Обработано войн", value=str(len(vzp_processed_events.get(interaction.guild_id, {}))), inline=True)
    await interaction.response.send_message(embed=embed)


@tree.command(name="взп-стоп", description="Остановить мониторинг ВЗП")
async def vzp_stop_cmd(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Нет прав.", ephemeral=True)
        return
    cfg = vzp_monitor_config.get(interaction.guild_id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Мониторинг не настроен.", ephemeral=True)
        return
    cfg["monitoringEnabled"] = False
    save_data()
    await interaction.response.send_message("🔴 Мониторинг ВЗП остановлен.")


@tree.command(name="взп-старт", description="Запустить мониторинг ВЗП")
async def vzp_start_cmd(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Нет прав.", ephemeral=True)
        return
    cfg = vzp_monitor_config.get(interaction.guild_id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Мониторинг не настроен. Используй `/взп-настройка`.", ephemeral=True)
        return
    cfg["monitoringEnabled"] = True
    save_data()
    await interaction.response.send_message("🟢 Мониторинг ВЗП запущен.")


@tree.command(name="взп-интервал", description="Изменить интервал опроса API (15–120 сек)")
@app_commands.describe(секунды="Интервал опроса в секундах (мин. 15, макс. 120)")
async def vzp_interval_cmd(interaction: discord.Interaction, секунды: int):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Нет прав.", ephemeral=True)
        return
    cfg = vzp_monitor_config.get(interaction.guild_id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Мониторинг не настроен.", ephemeral=True)
        return
    if not (15 <= секунды <= 120):
        await interaction.response.send_message("❌ Интервал: от 15 до 120 секунд.", ephemeral=True)
        return
    cfg["pollInterval"] = секунды
    save_data()
    await interaction.response.send_message(f"✅ Интервал опроса: **{секунды} сек**.")


@tree.command(name="взп-семья", description="Статистика семьи на vzp-gta5rp.com")
async def vzp_family_cmd(interaction: discord.Interaction):
    cfg = vzp_monitor_config.get(interaction.guild_id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Мониторинг не настроен.", ephemeral=True)
        return
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        raw = await _vzp_get(session, f"/stats/organizations/{cfg['familyId']}")
    data = _vzp_unwrap(raw) if raw else None
    if not data or not isinstance(data, dict):
        await interaction.followup.send("❌ Не удалось получить статистику семьи.")
        return

    embed = discord.Embed(
        title=f"📊 Статистика — {data.get('name', cfg['familyName'])}",
        color=0x5865F2,
        timestamp=datetime.now(),
    )
    embed.add_field(name="Побед",      value=str(data.get("wins",   "?")), inline=True)
    embed.add_field(name="Поражений",  value=str(data.get("losses", "?")), inline=True)
    wr = data.get("winrate") or data.get("winRate")
    if wr is not None:
        embed.add_field(name="Винрейт", value=f"{float(wr):.1f}%", inline=True)
    gr = data.get("rankGlobal") or data.get("rank") or data.get("globalRank") or data.get("position")
    embed.add_field(name="Глобальный ранк", value=f"#{gr}" if gr is not None else "?", inline=True)
    sr = data.get("rankServer") or data.get("serverRank")
    if sr:
        embed.add_field(name="Ранк на сервере", value=f"#{sr}", inline=True)
    embed.set_footer(text="vzp-gta5rp.com")
    await interaction.followup.send(embed=embed)


@tree.command(name="взп-история", description="История последних войн семьи")
@app_commands.describe(количество="Сколько войн показать (1–20, по умолчанию 5)")
async def vzp_history_cmd(interaction: discord.Interaction, количество: int = 5):
    cfg = vzp_monitor_config.get(interaction.guild_id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Мониторинг не настроен.", ephemeral=True)
        return
    количество = max(1, min(количество, 20))
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        raw = await _vzp_get(session, f"/stats/organizations/{cfg['familyId']}/history",
                             limit=количество, offset=0)
    items = _vzp_unwrap(raw)
    if not isinstance(items, list):
        items = []

    embed = discord.Embed(
        title=f"📜 История войн — {cfg['familyName']}",
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc),
    )
    for ev in items[:количество]:
        role     = ev.get("role", "?")
        opponent = ev.get("opponentName", "?")
        result   = "✅ Победа" if ev.get("isWin") else "❌ Поражение"
        map_name = ev.get("map", "?")
        ts       = _parse_vzp_ts(ev.get("date"))
        time_s   = f"<t:{ts}:d>" if ts else "—"
        embed.add_field(
            name=f"{result} — {role}",
            value=f"vs {opponent}\n📍 {map_name}\n{time_s}",
            inline=False,
        )
    if not items:
        embed.description = "История войн пуста."
    embed.set_footer(text="vzp-gta5rp.com")
    await interaction.followup.send(embed=embed)


# ══════════════════════════════════════════════════════════

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args):
        pass  # suppress logs

def _run_health_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    server.serve_forever()

# ─────────────────────────────────────────────
# ТАЙМЕР ИНАКТИВА — авто-удаление по дате ДД.ММ.ГГГГ
# ─────────────────────────────────────────────

@tasks.loop(hours=1)
async def inactive_expire_loop():
    """Каждый час проверяет список инактива и удаляет тех, чья дата возвращения наступила."""
    import re as _re
    from datetime import date as _date
    today = datetime.now().date()
    for guild_id, users in list(inactive_list.items()):
        expired = []
        for uid, entry in users.items():
            m = _re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", entry.get("return_date", "").strip())
            if not m:
                continue
            try:
                rd = _date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                continue
            if today >= rd:
                expired.append(uid)
        if not expired:
            continue
        for uid in expired:
            inactive_list[guild_id].pop(uid, None)
        save_data()
        guild = bot.get_guild(guild_id)
        if guild:
            await refresh_inactive_message(guild)


threading.Thread(target=_run_health_server, daemon=True).start()
bot.run(os.getenv("DISCORD_TOKEN"))
