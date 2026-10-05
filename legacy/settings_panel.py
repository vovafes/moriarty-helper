"""settings_panel -- split out of main.py."""

from datetime import datetime

import discord
import pvp_module
from discord import ui
from legacy.app import bot, tree
from legacy.cabinet import (
    _refresh_cabinet_panel,
)
from legacy.feedback import (
    _refresh_feedback_panel,
)
from legacy.helpers import (
    VZH_FACTION_LABELS,
    _footer,
    _vzh_schedule_str,
    get_vzh_faction,
    is_admin,
)
from legacy.obshak import (
    _refresh_obshak_panel,
)
from legacy.recruit import (
    _refresh_recruit_cabinet_panel,
)
from legacy.state import (
    BACKUP_AVAILABLE_FILES,
    admin_roles,
    afk_panels,
    backup_settings,
    cabinet_invite_links,
    cabinet_panels,
    event_command_roles,
    event_roles,
    extra_admin_roles,
    feedback_settings,
    guild_branding,
    inactive_panels,
    mp_roles,
    obshak_log_channels,
    obshak_panels,
    recruit_cabinet_panels,
    reject_log_channels,
    save_data,
    shop_log_channels,
    shop_manager_roles,
    ticket_counters,
    ticket_manager_roles,
    ticket_panels,
    ticket_ping_role,
    ticket_viewer_roles,
    voice_presence_settings,
    voice_reward_settings,
    vzh_schedule_settings,
    vzp_roles,
    warn_log_channels,
    warn_roles,
)
from legacy.tickets import (
    TicketTextModal,
)
from legacy.timers import (
    _voice_presence_ensure,
    send_backup_now,
)
from legacy.voice_rewards import (
    _get_voice_settings,
)


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
    rc_p = recruit_cabinet_panels.get(gid, {})
    embed.add_field(
        name="📊 Панели",
        value=(
            f"АФК: {channel_str(afk_p.get('channel_id'))}\n"
            f"Инактив: {channel_str(inact_p.get('channel_id'))}\n"
            f"Кабинет рекрута: {channel_str(rc_p.get('channel_id'))}\n"
            f"Лог варнов: {channel_str(warn_log_channels.get(gid))}"
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
            f"`!list`: {roles_list_str('list')}\n"
            f"`!vzh`: {roles_list_str('vzh')}"
        ),
        inline=False,
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(gid))
    await interaction.response.send_message(embed=embed, ephemeral=True)


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
        f"Feedback: {_cs(guild, fs.get('log_channel_id'))}\n"
        f"Варны: {_cs(guild, warn_log_channels.get(gid))}"
    ), inline=True)
    e.add_field(name="🎯 Сборы", value=(
        f"ВЗП: {_ecr('vzp')}\n"
        f"Реаки: {_ecr('list')}\n"
        f"ВЗХ: {_ecr('vzh')}"
    ), inline=True)
    e.add_field(name="🔊 Войс / 🖼 Контент", value=(
        f"💎/мин: **{vs.get('amount', 10)}**\n"
        f"Ссылка кабинета: {'✅' if cabinet_invite_links.get(gid) else '⚠️ нет'}\n"
        f"Fb-роль: {_rs(guild, fs.get('ping_role_id'))}\n"
        f"Кабинет рекрута: {'✅' if recruit_cabinet_panels.get(gid, {}).get('message_id') else '⚠️ не создан'}"
    ), inline=True)
    bs = backup_settings.get(gid, {})
    e.add_field(name="💾 Бэкапы", value=(
        f"Канал: {_cs(guild, bs.get('channel_id'))}\n"
        f"Период: **{bs.get('interval_hours', 1)}** ч.\n"
        f"Файлов выбрано: {len(bs.get('files', []))}"
    ), inline=True)
    vp = voice_presence_settings.get(gid, {})
    e.add_field(name="🎙 Войс-присутствие", value=(
        f"Канал: {_cs(guild, vp.get('channel_id'))}\n"
        f"Статус: {'🟢 Вкл' if vp.get('enabled') else '🔴 Выкл'}"
    ), inline=True)
    pvp_cfg = pvp_module.load_config()
    pvg = pvp_module.guild_config(pvp_cfg, gid)
    pvp_on = sum(1 for v in pvg["events"].values() if v)
    e.add_field(name="⚔️ PvP-события", value=(
        f"Канал: {_cs(guild, pvg.get('channel_id'))}\n"
        f"Включено: **{pvp_on}/{len(pvp_module.EVENTS)}**"
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
            f"**Feedback пинг-роль:** {_rs(guild, fs.get('ping_role_id'))}\n"
            f"**Лог варнов:** {_cs(guild, warn_log_channels.get(gid))}"
        )
    elif category == "fb_role":
        fs = feedback_settings.get(gid) or {}
        e.title = "🔔 Feedback пинг-роль"
        e.description = f"Текущая: {_rs(guild, fs.get('ping_role_id'))}"
    elif category == "warn_log":
        e.title = "⚠️ Лог варнов"
        e.description = (
            f"Текущий канал: {_cs(guild, warn_log_channels.get(gid))}\n\n"
            "Сюда рекруты будут отправлять сообщения о выданных варнах "
            "(через кабинет рекрута). Сообщение автоматически удаляется, "
            "когда варн снимается."
        )
    elif category == "events":
        ecr = event_command_roles.get(gid, {})
        def _ecr(t):
            ids = ecr.get(t, [])
            return "\n".join(f"  • <@&{r}>" for r in ids) if ids else "  *только админ*"
        e.title = "🎯 Доступ к командам сбора"
        e.description = (
            f"**!vzp:**\n{_ecr('vzp')}\n\n"
            f"**!list:**\n{_ecr('list')}\n\n"
            f"**!vzh:**\n{_ecr('vzh')}"
        )
    elif category in ("event_vzp", "event_list", "event_vzh"):
        etype = category.split("_", 1)[1]
        ecr = event_command_roles.get(gid, {})
        ids = ecr.get(etype, [])
        e.title = f"🎯 Доступ к !{etype}"
        e.description = f"Роли:\n{_roles_list(guild, ids)}\n\nДобавь или убери роль ниже."
        if etype == "vzh":
            faction = get_vzh_faction(gid)
            e.description += (
                f"\n\n**Фракция для расписания ВЗХ:** {VZH_FACTION_LABELS.get(faction, faction)}\n"
                f"Дни: {_vzh_schedule_str(faction)}"
            )
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
        rcp = recruit_cabinet_panels.get(gid, {})
        op = obshak_panels.get(gid, {})
        link = cabinet_invite_links.get(gid)
        br = guild_branding.get(gid) or {}
        e.title = "🖼 Контент — тексты, фото, ссылки"
        e.description = (
            f"**Личный кабинет**\n"
            f"Текст: {'✅' if cp.get('text') else '⚠️ нет'}  "
            f"Фото: {'✅' if cp.get('image_url') else '⚠️ нет'}  "
            f"Ссылка: {'✅' if link else '⚠️ нет'}\n\n"
            f"**Кабинет рекрута**\n"
            f"Текст: {'✅' if rcp.get('text') else '⚠️ нет'}  "
            f"Фото: {'✅' if rcp.get('image_url') else '⚠️ нет'}\n\n"
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
    elif category == "backup":
        bs = backup_settings.get(gid, {})
        files = bs.get("files", [])
        last = bs.get("last_backup")
        e.title = "💾 Резервное копирование"
        e.description = (
            f"**Канал:** {_cs(guild, bs.get('channel_id'))}\n"
            f"**Период:** каждые **{bs.get('interval_hours', 1)}** ч.\n"
            f"**Последний бэкап:** {last or '*ещё не было*'}\n\n"
            f"**Файлы для отправки:**\n"
            + ("\n".join(f"✅ {fn}" for fn in files) if files else "*ничего не выбрано*")
        )
    elif category == "voice_presence":
        vp = voice_presence_settings.get(gid, {})
        e.title = "🎙 Войс-присутствие"
        e.description = (
            f"**Канал:** {_cs(guild, vp.get('channel_id'))}\n"
            f"**Статус:** {'🟢 Включено' if vp.get('enabled') else '🔴 Выключено'}\n\n"
            "Пока бот онлайн, он будет сидеть в этом голосовом канале и "
            "автоматически переподключаться, если его выкинет."
        )
    elif category == "pvp":
        pvg = pvp_module.guild_config(pvp_module.load_config(), gid)
        e.title = "⚔️ PvP-события"
        lines = [f"**Канал публикаций:** {_cs(guild, pvg.get('channel_id'))}", ""]
        for key, meta in pvp_module.EVENTS.items():
            status = "🟢 вкл" if pvg["events"].get(key, True) else "🔴 выкл"
            lines.append(f"{meta['label']} · {status} — {', '.join(meta['times'])} МСК")
        e.description = "\n".join(lines)
    return e


# Категории настроек разбиты на 2 страницы, чтобы панель не была перегружена.
CFG_CATEGORY_PAGES = [
    [
        discord.SelectOption(label="📋 Заявки",        value="tickets", description="Менеджер, пинг, лог, доступ, текст"),
        discord.SelectOption(label="🔑 Роли системы",  value="roles",   description="МП, ВЗП, Реаки, Магазин"),
        discord.SelectOption(label="⚠️ Варн-роли",     value="warns",   description="Роли за 1, 2, 3 предупреждения"),
        discord.SelectOption(label="📢 Каналы / Логи", value="logs",    description="Логи и feedback канал/роль"),
        discord.SelectOption(label="🎯 Сборы",          value="events",  description="Доступ к !vzp !vzh !list"),
    ],
    [
        discord.SelectOption(label="🔊 Голосовые",      value="voice",   description="Баллы, категории, исключения"),
        discord.SelectOption(label="🖼 Контент",        value="content", description="Тексты, фото, ссылки панелей"),
        discord.SelectOption(label="💾 Бэкапы",         value="backup",  description="Канал, файлы и период автобэкапа"),
        discord.SelectOption(label="🎙 Войс-присутствие", value="voice_presence", description="Бот всегда сидит в голосовом канале"),
        discord.SelectOption(label="⚔️ PvP-события",    value="pvp",     description="AirDrop, чёрный рынок, война за граффити"),
    ],
]


class CfgCategorySelect(ui.Select):
    def __init__(self, page: int = 1):
        options = CFG_CATEGORY_PAGES[page - 1]
        super().__init__(placeholder=f"Выбери категорию настроек… (стр. {page}/{len(CFG_CATEGORY_PAGES)})", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        cat   = self.values[0]
        embed = build_cfg_category_embed(interaction.guild, cat)
        view  = _cfg_make_view(interaction.guild, cat)
        await interaction.response.edit_message(embed=embed, view=view)


class CfgMainView(ui.View):
    def __init__(self, page: int = 1):
        super().__init__(timeout=300)
        self.add_item(CfgCategorySelect(page))

        total_pages = len(CFG_CATEGORY_PAGES)
        if total_pages > 1:
            if page > 1:
                prev_btn = _cfg_btn("◀ Страница назад", row=1)
                async def _prev(inter, p=page): await inter.response.edit_message(embed=build_cfg_main_embed(inter.guild), view=CfgMainView(page=p - 1))
                prev_btn.callback = _prev
                self.add_item(prev_btn)
            if page < total_pages:
                next_btn = _cfg_btn("Страница вперёд ▶", row=1)
                async def _next(inter, p=page): await inter.response.edit_message(embed=build_cfg_main_embed(inter.guild), view=CfgMainView(page=p + 1))
                next_btn.callback = _next
                self.add_item(next_btn)


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

        btn_warn_log = _cfg_btn("⚠️ Лог варнов →", row=0)
        async def _wl(inter):
            await inter.response.edit_message(
                embed=build_cfg_category_embed(inter.guild, "warn_log"),
                view=_CfgWarnLogView(inter.guild),
            )
        btn_warn_log.callback = _wl
        self.add_item(btn_warn_log)

        self.add_item(_CfgChannelPicker(lambda gid, cid: reject_log_channels.__setitem__(gid, cid), "logs", 1, "📋 Лог заявок — выбери канал"))
        self.add_item(_CfgChannelPicker(lambda gid, cid: shop_log_channels.__setitem__(gid, cid), "logs", 2, "🛍 Лог магазина — выбери канал"))
        self.add_item(_CfgChannelPicker(lambda gid, cid: obshak_log_channels.__setitem__(gid, cid), "logs", 3, "💰 Лог общака — выбери канал"))
        self.add_item(_CfgChannelPicker(lambda gid, cid: feedback_settings.setdefault(gid, {}).__setitem__("log_channel_id", cid), "logs", 4, "💬 Feedback канал — выбери канал"))


class _CfgWarnLogView(ui.View):
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
        self.add_item(_CfgChannelPicker(lambda gid, cid: warn_log_channels.__setitem__(gid, cid), "warn_log", 1, "⚠️ Лог варнов — выбери канал"))


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

        for label, etype in [("⚔️ ВЗП", "vzp"), ("🎯 Реаки", "list"), ("⛏ ВЗХ", "vzh")]:
            btn = _cfg_btn(label, style=discord.ButtonStyle.primary, row=1)
            async def _cb(inter, et=etype):
                await inter.response.edit_message(
                    embed=build_cfg_category_embed(inter.guild, f"event_{et}"),
                    view=_CfgEventTypeView(inter.guild, et),
                )
            btn.callback = _cb
            self.add_item(btn)


class _CfgVzhFactionSelect(ui.Select):
    """Явный выбор фракции для расписания ВЗХ (banda/mafia) — не тумблер, чтобы не путать текущее состояние."""
    def __init__(self, guild_id: int):
        current = get_vzh_faction(guild_id)
        options = [
            discord.SelectOption(label=VZH_FACTION_LABELS["banda"], value="banda",
                                  description=f"Дни: {_vzh_schedule_str('banda')}", default=(current == "banda")),
            discord.SelectOption(label=VZH_FACTION_LABELS["mafia"], value="mafia",
                                  description=f"Дни: {_vzh_schedule_str('mafia')}", default=(current == "mafia")),
        ]
        super().__init__(placeholder="📅 Фракция для расписания ВЗХ…", options=options, row=3)

    async def callback(self, interaction: discord.Interaction):
        faction = self.values[0]
        vzh_schedule_settings[interaction.guild_id] = faction
        save_data()
        await interaction.response.send_message(
            f"✅ Расписание ВЗХ: {VZH_FACTION_LABELS[faction]} ({_vzh_schedule_str(faction)})", ephemeral=True
        )
        embed = build_cfg_category_embed(interaction.guild, "event_vzh")
        await interaction.message.edit(embed=embed, view=_CfgEventTypeView(interaction.guild, "vzh"))


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

        if etype == "vzh":
            self.add_item(_CfgVzhFactionSelect(gid))


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

        # Кабинет рекрута
        self.add_item(_modal_btn("✏️ Рекрут текст", "Кабинет рекрута — текст", "Текст описания",
            lambda gid: (recruit_cabinet_panels.get(gid) or {}).get("text", ""),
            lambda gid, v: recruit_cabinet_panels.setdefault(gid, {}).__setitem__("text", v),
            "content", row=1, text_style=discord.TextStyle.paragraph, refresh_fn=_refresh_recruit_cabinet_panel))
        self.add_item(_modal_btn("🖼 Рекрут фото", "Кабинет рекрута — фото", "Ссылка на изображение",
            lambda gid: (recruit_cabinet_panels.get(gid) or {}).get("image_url", ""),
            lambda gid, v: recruit_cabinet_panels.setdefault(gid, {}).__setitem__("image_url", v),
            "content", row=1, refresh_fn=_refresh_recruit_cabinet_panel))

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


BACKUP_INTERVAL_CHOICES = [1, 3, 6, 12, 24, 48]


class _CfgBackupFilesSelect(ui.Select):
    """Мультивыбор файлов, которые бот будет слать в канал бэкапов."""
    def __init__(self, guild_id: int):
        selected = set(backup_settings.get(guild_id, {}).get("files", []))
        options = [
            discord.SelectOption(label=fn, value=fn, default=(fn in selected))
            for fn in BACKUP_AVAILABLE_FILES
        ]
        super().__init__(
            placeholder="📁 Какие файлы бэкапить…",
            options=options, row=2,
            min_values=0, max_values=len(options),
        )

    async def callback(self, interaction: discord.Interaction):
        backup_settings.setdefault(interaction.guild_id, {})["files"] = list(self.values)
        save_data()
        await interaction.response.send_message(
            f"✅ Выбрано файлов: **{len(self.values)}**", ephemeral=True)
        embed = build_cfg_category_embed(interaction.guild, "backup")
        await interaction.message.edit(embed=embed, view=_CfgBackupView(interaction.guild))


class _CfgBackupIntervalSelect(ui.Select):
    """Как часто (в часах) отправлять бэкап."""
    def __init__(self, guild_id: int):
        current = backup_settings.get(guild_id, {}).get("interval_hours", 1)
        options = [
            discord.SelectOption(label=f"Каждые {h} ч.", value=str(h), default=(h == current))
            for h in BACKUP_INTERVAL_CHOICES
        ]
        super().__init__(placeholder="⏱ Период автобэкапа…", options=options, row=3)

    async def callback(self, interaction: discord.Interaction):
        backup_settings.setdefault(interaction.guild_id, {})["interval_hours"] = int(self.values[0])
        save_data()
        await interaction.response.send_message(
            f"✅ Период: каждые **{self.values[0]}** ч.", ephemeral=True)
        embed = build_cfg_category_embed(interaction.guild, "backup")
        await interaction.message.edit(embed=embed, view=_CfgBackupView(interaction.guild))


class _CfgBackupView(ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=300)
        gid = guild.id
        backup_settings.setdefault(gid, {"channel_id": None, "interval_hours": 1, "files": [], "last_backup": None})

        back = _cfg_btn("◀ Назад", row=0)
        async def _back(inter): await inter.response.edit_message(embed=build_cfg_main_embed(inter.guild), view=CfgMainView())
        back.callback = _back
        self.add_item(back)

        btn_now = _cfg_btn("📤 Отправить сейчас", style=discord.ButtonStyle.primary, row=0)
        async def _now(inter):
            bs = backup_settings.get(inter.guild_id, {})
            if not bs.get("channel_id"):
                return await inter.response.send_message("❌ Сначала выбери канал для бэкапов.", ephemeral=True)
            if not bs.get("files"):
                return await inter.response.send_message("❌ Сначала выбери хотя бы один файл.", ephemeral=True)
            await inter.response.defer(ephemeral=True)
            ok = await send_backup_now(inter.guild)
            await inter.followup.send("✅ Бэкап отправлен." if ok else "❌ Не удалось отправить бэкап (проверь канал/права).", ephemeral=True)
        btn_now.callback = _now
        self.add_item(btn_now)

        self.add_item(_CfgChannelPicker(
            lambda gid_, cid: backup_settings.setdefault(gid_, {}).__setitem__("channel_id", cid),
            "backup", 1, "💾 Канал для бэкапов"))
        self.add_item(_CfgBackupFilesSelect(gid))
        self.add_item(_CfgBackupIntervalSelect(gid))


class _CfgVoicePresenceView(ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=300)
        gid = guild.id
        vp = voice_presence_settings.setdefault(gid, {"channel_id": None, "enabled": False})

        back = _cfg_btn("◀ Назад", row=0)
        async def _back(inter): await inter.response.edit_message(embed=build_cfg_main_embed(inter.guild), view=CfgMainView())
        back.callback = _back
        self.add_item(back)

        toggle_label = "🔴 Выключить" if vp.get("enabled") else "🟢 Включить"
        toggle_style = discord.ButtonStyle.danger if vp.get("enabled") else discord.ButtonStyle.success
        btn_toggle = _cfg_btn(toggle_label, style=toggle_style, row=0)
        async def _toggle(inter):
            v = voice_presence_settings.setdefault(inter.guild_id, {"channel_id": None, "enabled": False})
            if not v.get("enabled") and not v.get("channel_id"):
                return await inter.response.send_message("❌ Сначала выбери голосовой канал.", ephemeral=True)
            v["enabled"] = not v.get("enabled")
            save_data()

            embed = build_cfg_category_embed(inter.guild, "voice_presence")
            await inter.response.edit_message(embed=embed, view=_CfgVoicePresenceView(inter.guild))

            if v["enabled"]:
                await _voice_presence_ensure(inter.guild)
            else:
                vc = inter.guild.voice_client
                if vc and vc.is_connected():
                    try:
                        await vc.disconnect(force=True)
                    except Exception:
                        pass
        btn_toggle.callback = _toggle
        self.add_item(btn_toggle)

        self.add_item(_CfgChannelPicker(
            lambda gid_, cid: voice_presence_settings.setdefault(gid_, {"channel_id": None, "enabled": False}).__setitem__("channel_id", cid),
            "voice_presence", 1, "🎙 Голосовой канал для присутствия",
            channel_types=[discord.ChannelType.voice],
        ))


class _CfgPvpChannelPicker(ui.ChannelSelect):
    def __init__(self, row: int):
        super().__init__(placeholder="⚔️ Канал для публикации PvP-событий",
                          channel_types=[discord.ChannelType.text], row=row)

    async def callback(self, interaction: discord.Interaction):
        ch = self.values[0]
        cfg = pvp_module.load_config()
        g = pvp_module.guild_config(cfg, interaction.guild_id)
        g["channel_id"] = ch.id
        pvp_module.save_config(cfg)
        await interaction.response.send_message(f"✅ Сохранено: {ch.mention}", ephemeral=True)
        embed = build_cfg_category_embed(interaction.guild, "pvp")
        await interaction.message.edit(embed=embed)


class _CfgPvpView(ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=300)
        gid = guild.id
        cfg = pvp_module.load_config()
        pvg = pvp_module.guild_config(cfg, gid)

        back = _cfg_btn("◀ Назад", row=0)
        async def _back(inter): await inter.response.edit_message(embed=build_cfg_main_embed(inter.guild), view=CfgMainView())
        back.callback = _back
        self.add_item(back)

        for i, (key, meta) in enumerate(pvp_module.EVENTS.items(), start=1):
            enabled = pvg["events"].get(key, True)
            btn = _cfg_btn(
                f"{meta['label']}: {'🟢 вкл' if enabled else '🔴 выкл'}",
                style=discord.ButtonStyle.danger if enabled else discord.ButtonStyle.success,
                row=1,
            )

            def _make_toggle(event_key: str):
                async def _toggle(inter: discord.Interaction):
                    c = pvp_module.load_config()
                    g = pvp_module.guild_config(c, inter.guild_id)
                    g["events"][event_key] = not g["events"].get(event_key, True)
                    pvp_module.save_config(c)
                    embed = build_cfg_category_embed(inter.guild, "pvp")
                    await inter.response.edit_message(embed=embed, view=_CfgPvpView(inter.guild))
                return _toggle

            btn.callback = _make_toggle(key)
            self.add_item(btn)

        self.add_item(_CfgPvpChannelPicker(row=2))


def _cfg_make_view(guild: discord.Guild, cat: str) -> ui.View:
    if cat == "tickets":        return _CfgTicketsView(guild)
    if cat == "ticket_access":  return _CfgTicketAccessView(guild)
    if cat == "roles":          return _CfgRolesView()
    if cat == "warns":          return _CfgWarnsView()
    if cat == "logs":           return _CfgLogsView(guild)
    if cat == "fb_role":        return _CfgFbRoleView(guild)
    if cat == "warn_log":       return _CfgWarnLogView(guild)
    if cat == "events":         return _CfgEventsView()
    if cat.startswith("event_"):
        return _CfgEventTypeView(guild, cat.split("_", 1)[1])
    if cat == "voice":          return _CfgVoiceView(guild)
    if cat == "content":        return _CfgContentView(guild)
    if cat == "backup":         return _CfgBackupView(guild)
    if cat == "voice_presence": return _CfgVoicePresenceView(guild)
    if cat == "pvp":            return _CfgPvpView(guild)
    return CfgMainView()


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
