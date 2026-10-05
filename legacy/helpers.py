"""helpers -- split out of main.py."""

from datetime import datetime, timedelta

import discord
from legacy.state import (
    admin_roles,
    afk_list,
    chips_db,
    event_command_roles,
    extra_admin_roles,
    guild_branding,
    guild_shop_items,
    inactive_list,
    points_db,
    recruit_stats,
    save_chips,
    save_data,
    save_points,
    ticket_manager_roles,
    vzh_schedule_settings,
    warns_db,
)


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
    """Проверка доступа к командам сборов (!vzp, !vzh, !list). Админ всегда может."""
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


# Расписание ВЗХ по фракциям (datetime.weekday(): Пн=0 ... Вс=6)
VZH_FACTION_WEEKDAYS = {
    "banda": {0, 2, 4, 6},   # Пн, Ср, Пт, Вс
    "mafia": {1, 3, 5, 6},   # Вт, Чт, Сб, Вс
}


VZH_FACTION_LABELS = {"banda": "🔫 Банды", "mafia": "🕴 Мафии"}


VZH_WEEKDAY_NAMES  = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def get_vzh_faction(guild_id: int) -> str:
    return vzh_schedule_settings.get(guild_id, "banda")


def _vzh_schedule_str(faction: str) -> str:
    days = sorted(VZH_FACTION_WEEKDAYS.get(faction, VZH_FACTION_WEEKDAYS["banda"]))
    return "+".join(VZH_WEEKDAY_NAMES[d] for d in days)


def next_vzh_datetime(anchor: datetime, hour: int, minute: int, faction: str) -> datetime:
    """Ближайшая дата (начиная с anchor), попадающая в расписание ВЗХ для фракции,
    на которую ещё не наступило указанное время. Не просто «завтра», а конкретный
    день недели по хардкод-расписанию — так !vzh, созданный за пару дней до сбора,
    не путает сегодня/завтра."""
    allowed = VZH_FACTION_WEEKDAYS.get(faction, VZH_FACTION_WEEKDAYS["banda"])
    for delta in range(8):
        candidate_date = (anchor + timedelta(days=delta)).date()
        if candidate_date.weekday() not in allowed:
            continue
        candidate = datetime.combine(candidate_date, anchor.time()).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if candidate >= anchor:
            return candidate
    return anchor.replace(hour=hour, minute=minute, second=0, microsecond=0)


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


def set_warn(guild_id: int, user_id: int, count: int, reason: str, moderator_id: int, payment_method: str = "any"):
    """payment_method: "any" (баллы или деньги) | "money" (только деньги)"""
    if guild_id not in warns_db:
        warns_db[guild_id] = {}
    warns_db[guild_id][user_id] = {
        "warns": count,
        "reason": reason,
        "moderator": moderator_id,
        "timestamp": datetime.now(),
        "payment_method": payment_method,
    }
    save_data()


def warn_payment_label(warn_data: dict) -> str:
    return "💵 только деньгами" if warn_data.get("payment_method") == "money" else "💎 баллами или деньгами"


def remove_warn(guild_id: int, user_id: int) -> bool:
    if guild_id in warns_db and user_id in warns_db[guild_id]:
        del warns_db[guild_id][user_id]
        save_data()
        return True
    return False


def decrement_warn(guild_id: int, user_id: int) -> int:
    """Снимает варн ровно на один уровень. Возвращает новый уровень (0 — варнов не осталось, -1 — варнов не было)."""
    warn_data = get_warns(guild_id, user_id)
    if not warn_data:
        return -1
    new_level = warn_data["warns"] - 1
    if new_level <= 0:
        remove_warn(guild_id, user_id)
        return 0
    warns_db[guild_id][user_id]["warns"] = new_level
    save_data()
    return new_level


def get_recruit_stats(guild_id: int, user_id: int) -> dict:
    return recruit_stats.get(guild_id, {}).get(user_id, {"approved": 0, "rejected": 0})


def bump_recruit_stat(guild_id: int, user_id: int, field: str):
    g = recruit_stats.setdefault(guild_id, {})
    u = g.setdefault(user_id, {"approved": 0, "rejected": 0})
    u[field] = u.get(field, 0) + 1
    save_data()


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


def _fmt(n: int) -> str:
    """Форматирование числа с разрядами: 1000 → 1 000"""
    return f"{n:,}".replace(",", " ")
