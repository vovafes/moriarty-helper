"""Panel modules: family-management features (tickets, events, warns, cabinets, ...)."""

import aiohttp
import discord

from core import modules
from legacy.afk import AfkView, InactiveView, refresh_afk_message, refresh_inactive_message
from legacy.cabinet import PersonalCabinetView, _refresh_cabinet_panel, build_cabinet_embed
from legacy.contracts import ContractPanelView, _refresh_contract_panel, build_contract_panel_embed
from legacy.feedback import FeedbackPanelView, _refresh_feedback_panel, build_feedback_panel_embed
from legacy.helpers import (
    DEFAULT_TICKET_DESC, DEFAULT_TICKET_TITLE, VZH_FACTION_LABELS, _footer, build_afk_embed, build_inactive_embed, remove_warn, set_warn,
)
from legacy.obshak import ObshakView, _refresh_obshak_panel, build_obshak_embed
from legacy.private_vc import _refresh_pvc_panel
from legacy.recruit import (
    RecruitCabinetView, _delete_warn_log_message, _refresh_recruit_cabinet_panel, build_recruit_cabinet_embed,
)
from legacy.roster import _refresh_roster
from legacy.state import (
    DEFAULT_TICKET_IMAGE, active_contracts, afk_list, afk_panels, cabinet_invite_links, cabinet_panels,
    contract_roles, contract_settings, event_command_roles, event_lists, event_roles, feedback_settings,
    inactive_list, inactive_panels, interview_channels, list_roles2, mp_roles, mp_roles2, obshak_deposits,
    obshak_log_channels, obshak_panels, obshak_ping_roles, private_vc_settings, private_vcs,
    recruit_cabinet_panels, recruit_stats, reject_log_channels, roster_settings, save_data, ticket_manager_roles,
    ticket_panels, ticket_ping_role, ticket_texts, ticket_viewer_roles, vzh_roles, vzh_roles2,
    vzh_schedule_settings, vzp_monitor_config, vzp_processed_events, vzp_roles, vzp_roles2, warn_log_channels,
    warn_roles, warns_db,
)
from legacy.tickets import TicketPanelView
from legacy.vzp_monitor import _vzp_get, _vzp_unwrap
from panel_modules.common import (
    PANEL_FIELDS, bot, channel_name, name_of, parse_ids, publish_panel, put, role_name, spawn, to_epoch,
)

reg = modules.register


def _text_channel(label="Канал", **kw):
    return {"key": "channel", "label": label, "type": "channel", "kind": "text", **kw}


# ── Заявки ──────────────────────────────────────────────────────────────────

def _tickets_load(gid):
    tt = ticket_texts.get(gid, {})
    return {
        "manager_role": ticket_manager_roles.get(gid), "ping_role": ticket_ping_role.get(gid),
        "log_channel": reject_log_channels.get(gid), "interview_channel": interview_channels.get(gid),
        "viewer_roles": ticket_viewer_roles.get(gid, []),
        "title": tt.get("title", DEFAULT_TICKET_TITLE), "desc": tt.get("desc", DEFAULT_TICKET_DESC),
        "image": tt.get("image", DEFAULT_TICKET_IMAGE),
    }


async def _refresh_ticket_panel(guild):
    panel = ticket_panels.get(guild.id, {})
    if not (panel.get("message_id") and panel.get("panel_channel_id")):
        return
    try:
        msg = await guild.get_channel(panel["panel_channel_id"]).fetch_message(panel["message_id"])
        tt = ticket_texts[guild.id]
        e = discord.Embed(title=tt["title"], description=tt["desc"], color=discord.Color.red())
        if tt["image"]:
            e.set_image(url=tt["image"])
        e.set_footer(text="MORIARTY", icon_url=_footer(guild.id))
        await msg.edit(embed=e)
    except Exception:
        pass


def _tickets_save(gid, cfg):
    put(ticket_manager_roles, gid, cfg["manager_role"])
    put(ticket_ping_role, gid, cfg["ping_role"])
    put(reject_log_channels, gid, cfg["log_channel"])
    put(interview_channels, gid, cfg["interview_channel"])
    put(ticket_viewer_roles, gid, cfg["viewer_roles"])
    ticket_texts[gid] = {"title": cfg["title"], "desc": cfg["desc"], "image": (cfg["image"] or "").strip()}
    save_data()
    if bot.get_guild(gid):
        spawn(_refresh_ticket_panel(bot.get_guild(gid)))


async def _tickets_publish(ctx, p):
    guild = ctx.guild
    category = guild.get_channel(p["category"])
    channel = guild.get_channel(p["channel"])
    if not isinstance(category, discord.CategoryChannel) or channel is None:
        raise modules.ValidationError("Выберите текстовый канал и категорию")
    tt = ticket_texts.get(guild.id, {})
    e = discord.Embed(title=tt.get("title", DEFAULT_TICKET_TITLE), description=tt.get("desc", DEFAULT_TICKET_DESC),
                      color=discord.Color.red())
    img = tt.get("image", DEFAULT_TICKET_IMAGE)
    if img:
        e.set_image(url=img)
    e.set_footer(text="MORIARTY", icon_url=_footer(guild.id))
    try:
        msg = await channel.send(embed=e, view=TicketPanelView(category.id))
    except discord.Forbidden:
        raise modules.ValidationError("У бота нет прав писать в этот канал")
    ticket_panels[guild.id] = {"panel_channel_id": channel.id, "category_id": category.id, "message_id": msg.id}
    save_data()
    return f"Панель заявок опубликована в #{channel.name}; тикеты — в «{category.name}»"


reg(modules.Module(
    key="tickets", title="Заявки", icon="📋", category="family", toggleable=False,
    description="Подача заявок на вступление: панель с кнопкой, одобрение и отказ, каналы тикетов.",
    fields=[
        {"key": "manager_role", "label": "Тикет-менеджер", "type": "role", "default": None, "group": "Доступ",
         "help": "Тегается в тикетах и может одобрять/отклонять"},
        {"key": "ping_role", "label": "Пинг-роль", "type": "role", "default": None, "group": "Доступ"},
        {"key": "viewer_roles", "label": "Роли с доступом к тикетам", "type": "roles", "default": [], "group": "Доступ",
         "help": "Видят каналы тикетов"},
        {"key": "log_channel", "label": "Лог заявок", "type": "channel", "kind": "text", "default": None, "group": "Каналы"},
        {"key": "interview_channel", "label": "Войс для обзвонов", "type": "channel", "kind": "voice", "default": None,
         "group": "Каналы"},
        {"key": "title", "label": "Заголовок панели", "type": "text", "default": "", "group": "Текст панели"},
        {"key": "desc", "label": "Описание панели", "type": "longtext", "default": "", "group": "Текст панели"},
        {"key": "image", "label": "Картинка (ссылка)", "type": "text", "default": "", "group": "Текст панели",
         "help": "Пусто — без картинки"},
    ],
    loader=_tickets_load, saver=_tickets_save,
    actions=[modules.Action("publish", "Опубликовать панель заявок", _tickets_publish, params=[
        {"key": "channel", "label": "Канал для панели", "type": "channel", "kind": "text"},
        {"key": "category", "label": "Категория для тикетов", "type": "channel", "kind": "category"}],
        description="Отправит новое сообщение с кнопкой «Подать заявку»")],
))


# ── Сборы и роли событий ────────────────────────────────────────────────────

def _events_load(gid):
    ecr = event_command_roles.get(gid, {})
    return {
        "mp": mp_roles.get(gid), "mp2": mp_roles2.get(gid), "vzp": vzp_roles.get(gid), "vzp2": vzp_roles2.get(gid),
        "vzh": vzh_roles.get(gid), "vzh2": vzh_roles2.get(gid), "reaki": event_roles.get(gid), "reaki2": list_roles2.get(gid),
        "faction": vzh_schedule_settings.get(gid, "banda"),
        "can_vzp": ecr.get("vzp", []), "can_list": ecr.get("list", []), "can_vzh": ecr.get("vzh", []),
    }


def _events_save(gid, cfg):
    for store, key in ((mp_roles, "mp"), (mp_roles2, "mp2"), (vzp_roles, "vzp"), (vzp_roles2, "vzp2"),
                       (vzh_roles, "vzh"), (vzh_roles2, "vzh2"), (event_roles, "reaki"), (list_roles2, "reaki2")):
        put(store, gid, cfg[key])
    vzh_schedule_settings[gid] = cfg["faction"]
    put(event_command_roles, gid, {k: v for k, v in
                                   (("vzp", cfg["can_vzp"]), ("list", cfg["can_list"]), ("vzh", cfg["can_vzh"])) if v})
    save_data()


def _events_table(guild):
    rows = []
    for mid, ev in event_lists.items():
        ch = guild.get_channel(ev.get("channel_id") or 0)
        if ch is None:
            continue
        slots = ev.get("slots", {})
        rows.append({"title": ev.get("title"), "filled": f"{sum(1 for u in slots.values() if u)}/{ev.get('max', len(slots))}",
                     "channel": f"#{ch.name}", "message_id": mid})
    return rows


reg(modules.Module(
    key="events", title="Сборы", icon="🎯", category="family", toggleable=False,
    description="Роли для тегов при !vzp / !vzh / !list, доступ к командам сборов и расписание ВЗХ.",
    fields=[
        {"key": "mp", "label": "Роль МП", "type": "role", "default": None, "group": "Роли для тегов"},
        {"key": "mp2", "label": "Роль МП-2", "type": "role", "default": None, "group": "Роли для тегов"},
        {"key": "vzp", "label": "Роль ВЗП", "type": "role", "default": None, "group": "Роли для тегов"},
        {"key": "vzp2", "label": "Роль ВЗП-2", "type": "role", "default": None, "group": "Роли для тегов"},
        {"key": "vzh", "label": "Роль ВЗХ", "type": "role", "default": None, "group": "Роли для тегов"},
        {"key": "vzh2", "label": "Роль ВЗХ-2", "type": "role", "default": None, "group": "Роли для тегов"},
        {"key": "reaki", "label": "Роль реаки", "type": "role", "default": None, "group": "Роли для тегов"},
        {"key": "reaki2", "label": "Роль реаки-2", "type": "role", "default": None, "group": "Роли для тегов"},
        {"key": "can_vzp", "label": "Кто может !vzp", "type": "roles", "default": [], "group": "Доступ к командам",
         "help": "Пусто — только админы"},
        {"key": "can_list", "label": "Кто может !list", "type": "roles", "default": [], "group": "Доступ к командам"},
        {"key": "can_vzh", "label": "Кто может !vzh", "type": "roles", "default": [], "group": "Доступ к командам"},
        {"key": "faction", "label": "Фракция для расписания ВЗХ", "type": "select", "default": "banda", "group": "ВЗХ",
         "options": [{"value": k, "label": v} for k, v in VZH_FACTION_LABELS.items()],
         "help": "Банды: Пн, Ср, Пт, Вс. Мафии: Вт, Чт, Сб, Вс"},
    ],
    loader=_events_load, saver=_events_save,
    tables=[{"id": "lists", "title": "Активные списки сборов", "columns": [
        {"key": "title", "label": "Название"}, {"key": "filled", "label": "Заполнено"}, {"key": "channel", "label": "Канал"}]}],
))
modules.register_table("events", "lists", _events_table)


# ── Варны ───────────────────────────────────────────────────────────────────

def _warns_load(gid):
    wr = warn_roles.get(gid, {})
    return {"role1": wr.get(1), "role2": wr.get(2), "role3": wr.get(3), "log_channel": warn_log_channels.get(gid)}


def _warns_save(gid, cfg):
    put(warn_roles, gid, {i: cfg[f"role{i}"] for i in (1, 2, 3) if cfg[f"role{i}"]})
    put(warn_log_channels, gid, cfg["log_channel"])
    save_data()


def _warns_table(guild):
    rows = [{"user": name_of(guild, uid), "warns": f"{w.get('warns', 0)}/3", "reason": w.get("reason"),
             "moderator": name_of(guild, w.get("moderator"))}
            for uid, w in warns_db.get(guild.id, {}).items() if w.get("warns")]
    return sorted(rows, key=lambda r: r["warns"], reverse=True)


async def _swap_warn_role(guild, member, new_level):
    roles = warn_roles.get(guild.id, {})
    try:
        await member.remove_roles(*[r for r in (guild.get_role(i) for i in roles.values()) if r], reason="Обновление варн-роли")
        if new_level and guild.get_role(roles.get(new_level)):
            await member.add_roles(guild.get_role(roles[new_level]), reason=f"Warn {new_level}/3")
    except Exception:
        pass


async def _warn_issue(ctx, p):
    member = ctx.guild.get_member(p["user"])
    if member is None:
        raise modules.ValidationError("Участник не найден на сервере")
    set_warn(ctx.guild.id, member.id, int(p["level"]), p["reason"], ctx.user_id or 0)
    await _swap_warn_role(ctx.guild, member, int(p["level"]))
    try:
        e = discord.Embed(title="⚠️ Вы получили warn", description=f"**Причина:** {p['reason']}\n**Варны:** {p['level']}/3",
                          color=discord.Color.red())
        e.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
        await member.send(embed=e)
    except Exception:
        pass
    return f"{member.display_name}: варн {p['level']}/3"


async def _warn_remove(ctx, p):
    member = ctx.guild.get_member(p["user"])
    if member is None:
        raise modules.ValidationError("Участник не найден на сервере")
    if not remove_warn(ctx.guild.id, member.id):
        raise modules.ValidationError("У участника нет варнов")
    await _swap_warn_role(ctx.guild, member, 0)
    await _delete_warn_log_message(ctx.guild, member.id)
    return f"С {member.display_name} снят варн"


reg(modules.Module(
    key="warns", title="Варны", icon="⚠️", category="family", toggleable=False,
    description="Предупреждения 1/3, 2/3, 3/3 с ролями; снять можно за баллы в магазине.",
    fields=[
        {"key": "role1", "label": "Роль за 1/3", "type": "role", "default": None, "group": "Варн-роли"},
        {"key": "role2", "label": "Роль за 2/3", "type": "role", "default": None, "group": "Варн-роли"},
        {"key": "role3", "label": "Роль за 3/3", "type": "role", "default": None, "group": "Варн-роли"},
        {"key": "log_channel", "label": "Лог варнов", "type": "channel", "kind": "text", "default": None, "group": "Логи",
         "help": "Сюда рекруты публикуют выданные варны; сообщение удаляется при снятии"},
    ],
    loader=_warns_load, saver=_warns_save,
    tables=[{"id": "list", "title": "Текущие варны", "columns": [
        {"key": "user", "label": "Участник"}, {"key": "warns", "label": "Варны"},
        {"key": "reason", "label": "Причина"}, {"key": "moderator", "label": "Выдал"}]}],
    actions=[
        modules.Action("issue", "Выдать варн", _warn_issue, params=[
            {"key": "user", "label": "Участник (ID)", "type": "user"},
            {"key": "level", "label": "Варн", "type": "select", "default": "1",
             "options": [{"value": "1", "label": "1/3"}, {"value": "2", "label": "2/3"}, {"value": "3", "label": "3/3"}]},
            {"key": "reason", "label": "Причина", "type": "text"}]),
        modules.Action("remove", "Снять варн", _warn_remove, params=[{"key": "user", "label": "Участник (ID)", "type": "user"}]),
    ],
))
modules.register_table("warns", "list", _warns_table)


# ── панели с «текст + картинка» (кабинеты, общак, контракты, feedback) ───────

def _panel_module(key, title, icon, description, store, builder, view_cls, refresh, extra_fields=(),
                  extra_load=None, extra_save=None, tables=(), actions=()):
    def load(gid):
        p = store.get(gid) or {}
        cfg = {"text": p.get("text") or "", "image_url": p.get("image_url") or ""}
        if extra_load:
            cfg.update(extra_load(gid))
        return cfg

    def save(gid, cfg):
        prev = store.get(gid) or {}
        store[gid] = {**prev, "text": cfg["text"] or None, "image_url": cfg["image_url"] or None}
        if extra_save:
            extra_save(gid, cfg)
        save_data()
        if prev.get("message_id") and bot.get_guild(gid):
            spawn(refresh(bot.get_guild(gid)))

    async def publish(ctx, p):
        return await publish_panel(ctx.guild, p["channel"], store, builder, view_cls())

    reg(modules.Module(
        key=key, title=title, icon=icon, category="family", toggleable=False, description=description,
        fields=[*extra_fields, *PANEL_FIELDS], loader=load, saver=save, tables=list(tables),
        actions=[modules.Action("publish", "Опубликовать панель", publish, params=[
            {"key": "channel", "label": "Канал", "type": "channel", "kind": "text"}],
            description="Удалит старое сообщение панели (если есть) и отправит новое"), *actions]))


_panel_module(
    "cabinet", "Личный кабинет", "🪪", "Панель с профилем участника: баланс, варны, статистика.",
    cabinet_panels, build_cabinet_embed, PersonalCabinetView, _refresh_cabinet_panel,
    extra_fields=[{"key": "invite", "label": "Пригласительная ссылка", "type": "text", "default": "", "group": "Ссылки"}],
    extra_load=lambda gid: {"invite": cabinet_invite_links.get(gid) or ""},
    extra_save=lambda gid, cfg: put(cabinet_invite_links, gid, (cfg["invite"] or "").strip()),
)


def _recruit_table(guild):
    rows = [{"user": name_of(guild, uid), "approved": s.get("approved", 0), "rejected": s.get("rejected", 0)}
            for uid, s in recruit_stats.get(guild.id, {}).items()]
    return sorted(rows, key=lambda r: -(r["approved"] + r["rejected"]))


_panel_module(
    "recruit", "Кабинет рекрута", "🎖", "Панель рекрута: статистика одобрений и выдача варнов.",
    recruit_cabinet_panels, build_recruit_cabinet_embed, RecruitCabinetView, _refresh_recruit_cabinet_panel,
    tables=[{"id": "stats", "title": "Статистика рекрутов", "columns": [
        {"key": "user", "label": "Рекрут"}, {"key": "approved", "label": "Одобрено"}, {"key": "rejected", "label": "Отклонено"}]}],
)
modules.register_table("recruit", "stats", _recruit_table)


def _obshak_table(guild):
    rows = [{"user": name_of(guild, d.get("user_id")), "amount": d.get("amount"), "date": d.get("date", "")[:16].replace("T", " ")}
            for d in obshak_deposits.get(guild.id, [])]
    return rows[::-1][:200]


_panel_module(
    "obshak", "Общак", "💰", "Пополнение общака семьи с логами.",
    obshak_panels, build_obshak_embed, ObshakView, _refresh_obshak_panel,
    extra_fields=[
        {"key": "log_channel", "label": "Лог общака", "type": "channel", "kind": "text", "default": None, "group": "Логи"},
        {"key": "ping_role", "label": "Кого тегать в логах", "type": "role", "default": None, "group": "Логи"}],
    extra_load=lambda gid: {"log_channel": obshak_log_channels.get(gid), "ping_role": obshak_ping_roles.get(gid)},
    extra_save=lambda gid, cfg: (put(obshak_log_channels, gid, cfg["log_channel"]), put(obshak_ping_roles, gid, cfg["ping_role"])),
    tables=[{"id": "deposits", "title": "Последние пополнения", "columns": [
        {"key": "user", "label": "Кто"}, {"key": "amount", "label": "Сумма"}, {"key": "date", "label": "Когда"}]}],
)
modules.register_table("obshak", "deposits", _obshak_table)


def _contracts_table(guild):
    rows = []
    for mid, c in active_contracts.items():
        if c.get("guild_id") != guild.id:
            continue
        rows.append({"creator": name_of(guild, c.get("creator_id")), "duration": c.get("duration"),
                     "start": c.get("start"), "members": len(c.get("participants", [])),
                     "channel": channel_name(guild, c.get("channel_id"))})
    return rows


def _contracts_load(gid):
    return {"role": contract_roles.get(gid)}


_panel_module(
    "contracts", "Контракты", "📄", "Взятие контрактов с отслеживанием времени и участников.",
    contract_settings, build_contract_panel_embed, ContractPanelView, _refresh_contract_panel,
    extra_fields=[{"key": "role", "label": "Роль для тега при создании контракта", "type": "role", "default": None,
                   "group": "Уведомления"}],
    extra_load=_contracts_load, extra_save=lambda gid, cfg: put(contract_roles, gid, cfg["role"]),
    tables=[{"id": "active", "title": "Активные контракты", "columns": [
        {"key": "creator", "label": "Создал"}, {"key": "duration", "label": "Срок"}, {"key": "start", "label": "Начало"},
        {"key": "members", "label": "Участников"}, {"key": "channel", "label": "Канал"}]}],
)
modules.register_table("contracts", "active", _contracts_table)


def _feedback_extra_load(gid):
    fs = feedback_settings.get(gid) or {}
    return {"log_channel": fs.get("log_channel_id"), "ping_role": fs.get("ping_role_id")}


def _feedback_extra_save(gid, cfg):
    fs = feedback_settings.setdefault(gid, {})
    fs["log_channel_id"], fs["ping_role_id"] = cfg["log_channel"], cfg["ping_role"]


async def _feedback_publish(ctx, p):
    return await publish_panel(ctx.guild, p["channel"], feedback_settings, build_feedback_panel_embed,
                               FeedbackPanelView(), channel_key="panel_channel_id", message_key="panel_message_id")


def _feedback_load(gid):
    fs = feedback_settings.get(gid) or {}
    return {"text": fs.get("text") or "", "image_url": fs.get("image_url") or "", **_feedback_extra_load(gid)}


def _feedback_save(gid, cfg):
    fs = feedback_settings.setdefault(gid, {})
    fs["text"], fs["image_url"] = cfg["text"] or None, cfg["image_url"] or None
    _feedback_extra_save(gid, cfg)
    save_data()
    if fs.get("panel_message_id") and bot.get_guild(gid):
        spawn(_refresh_feedback_panel(bot.get_guild(gid)))


reg(modules.Module(
    key="feedback", title="Предложения", icon="📝", category="family", toggleable=False,
    description="Сбор предложений от участников с тредами для обсуждения.",
    fields=[
        {"key": "log_channel", "label": "Канал предложений", "type": "channel", "kind": "text", "default": None, "group": "Каналы"},
        {"key": "ping_role", "label": "Кого тегать", "type": "role", "default": None, "group": "Каналы"}, *PANEL_FIELDS],
    loader=_feedback_load, saver=_feedback_save,
    actions=[modules.Action("publish", "Опубликовать панель", _feedback_publish, params=[
        {"key": "channel", "label": "Канал", "type": "channel", "kind": "text"}])],
))


# ── Приватные комнаты ───────────────────────────────────────────────────────

def _pvc_load(gid):
    s = private_vc_settings.get(gid, {})
    return {"create_channel": s.get("create_channel_id"), "category": s.get("category_id"), "panel_channel": s.get("panel_channel_id")}


def _pvc_save(gid, cfg):
    if cfg["create_channel"] and cfg["category"] and cfg["panel_channel"]:
        private_vc_settings[gid] = {"create_channel_id": cfg["create_channel"], "category_id": cfg["category"],
                                    "panel_channel_id": cfg["panel_channel"]}
    elif not any((cfg["create_channel"], cfg["category"], cfg["panel_channel"])):
        private_vc_settings.pop(gid, None)
    else:
        raise modules.ValidationError("Заполните все три поля (или очистите все, чтобы отключить)")
    save_data()


def _pvc_table(guild):
    rows = []
    for cid, v in private_vcs.items():
        ch = guild.get_channel(cid)
        if ch is None or v.get("guild_id") != guild.id:
            continue
        rows.append({"channel": ch.name, "owner": name_of(guild, v.get("owner_id")), "members": len(ch.members)})
    return rows


reg(modules.Module(
    key="private_vc", title="Приватные комнаты", icon="🔒", category="family", toggleable=False,
    description="Заход в канал-триггер создаёт личный голосовой канал с панелью управления.",
    fields=[
        {"key": "create_channel", "label": "Канал-триггер", "type": "channel", "kind": "voice", "default": None},
        {"key": "category", "label": "Категория для комнат", "type": "channel", "kind": "category", "default": None},
        {"key": "panel_channel", "label": "Канал для панелей", "type": "channel", "kind": "text", "default": None},
    ],
    loader=_pvc_load, saver=_pvc_save,
    tables=[{"id": "active", "title": "Сейчас открыты", "columns": [
        {"key": "channel", "label": "Комната"}, {"key": "owner", "label": "Владелец"}, {"key": "members", "label": "Внутри"}]}],
))
modules.register_table("private_vc", "active", _pvc_table)


# ── Состав семьи ────────────────────────────────────────────────────────────

def _roster_load(gid):
    r = roster_settings.get(gid, {})
    return {"member_role": r.get("member_role_id"), "academy_role": r.get("academy_role_id"), "channel": r.get("channel_id")}


def _roster_save(gid, cfg):
    r = roster_settings.setdefault(gid, {})
    r["member_role_id"], r["academy_role_id"], r["channel_id"] = cfg["member_role"], cfg["academy_role"], cfg["channel"]
    save_data()


async def _roster_refresh(ctx, p):
    if not roster_settings.get(ctx.guild.id, {}).get("channel_id"):
        raise modules.ValidationError("Сначала выберите канал и сохраните")
    await _refresh_roster(ctx.guild)
    return "Состав обновлён"


reg(modules.Module(
    key="roster", title="Состав семьи", icon="📋", category="family", toggleable=False,
    description="Авто-обновляемый список участников и академии по ролям.",
    fields=[
        {"key": "member_role", "label": "Роль участника", "type": "role", "default": None},
        {"key": "academy_role", "label": "Роль академии", "type": "role", "default": None},
        {"key": "channel", "label": "Канал для списка", "type": "channel", "kind": "text", "default": None},
    ],
    loader=_roster_load, saver=_roster_save,
    actions=[modules.Action("refresh", "Обновить список сейчас", _roster_refresh)],
))


# ── АФК и инактив ───────────────────────────────────────────────────────────

def _afk_table(guild):
    return [{"user": name_of(guild, uid), "reason": e.get("reason"), "until": e.get("return_time"), "since": to_epoch(e.get("since"))}
            for uid, e in afk_list.get(guild.id, {}).items()]


def _inactive_table(guild):
    return [{"user": name_of(guild, uid), "reason": e.get("reason"), "until": e.get("return_date"), "since": to_epoch(e.get("since"))}
            for uid, e in inactive_list.get(guild.id, {}).items()]


async def _afk_publish(ctx, p):
    afk_list.setdefault(ctx.guild.id, {})
    return await publish_panel(ctx.guild, p["channel"], afk_panels, lambda gid: build_afk_embed(gid), AfkView())


async def _inactive_publish(ctx, p):
    inactive_list.setdefault(ctx.guild.id, {})
    return await publish_panel(ctx.guild, p["channel"], inactive_panels, lambda gid: build_inactive_embed(gid), InactiveView())


def _remover(store, refresh):
    async def run(ctx, p):
        if store.get(ctx.guild.id, {}).pop(p["user"], None) is None:
            raise modules.ValidationError("Этого участника нет в списке")
        save_data()
        await refresh(ctx.guild)
        return "Убран из списка"
    return run


def _clearer(store, refresh):
    async def run(ctx, p):
        n = len(store.get(ctx.guild.id, {}))
        store[ctx.guild.id] = {}
        save_data()
        await refresh(ctx.guild)
        return f"Список очищен ({n})"
    return run


_AFK_COLS = [{"key": "user", "label": "Участник"}, {"key": "reason", "label": "Причина"},
             {"key": "until", "label": "До"}, {"key": "since", "label": "С", "format": "time"}]
_USER_PARAM = [{"key": "user", "label": "Участник (ID)", "type": "user"}]
_CHAN_PARAM = [{"key": "channel", "label": "Канал", "type": "channel", "kind": "text"}]

reg(modules.Module(
    key="afk", title="АФК и инактив", icon="🕐", category="family", toggleable=False,
    description="Панели, где участники отмечают, что отсутствуют; списки очищаются сами по времени.",
    fields=[],
    tables=[{"id": "afk", "title": "Сейчас в АФК", "columns": _AFK_COLS},
            {"id": "inactive", "title": "Сейчас в инактиве", "columns": _AFK_COLS}],
    actions=[
        modules.Action("publish_afk", "Опубликовать панель АФК", _afk_publish, params=_CHAN_PARAM),
        modules.Action("publish_inactive", "Опубликовать панель инактива", _inactive_publish, params=_CHAN_PARAM),
        modules.Action("remove_afk", "Убрать из АФК", _remover(afk_list, refresh_afk_message), params=_USER_PARAM),
        modules.Action("remove_inactive", "Убрать из инактива", _remover(inactive_list, refresh_inactive_message), params=_USER_PARAM),
        modules.Action("clear_afk", "Очистить АФК", _clearer(afk_list, refresh_afk_message), danger=True,
                       confirm="Убрать всех из списка АФК?"),
        modules.Action("clear_inactive", "Очистить инактив", _clearer(inactive_list, refresh_inactive_message), danger=True,
                       confirm="Убрать всех из списка инактива?"),
    ],
))
modules.register_table("afk", "afk", _afk_table)
modules.register_table("afk", "inactive", _inactive_table)


# ── Мониторинг ВЗП ──────────────────────────────────────────────────────────

def _vzp_load(gid):
    c = vzp_monitor_config.get(gid) or {}
    return {
        "enabled": bool(c.get("monitoringEnabled")), "family": f"{c.get('familyName')} (ID {c.get('familyId')})" if c.get("familyId") else "",
        "server_id": c.get("serverId"), "alert_channel": c.get("alertChannelId"), "results_channel": c.get("resultsChannelId"),
        "mention_roles": c.get("mentionRoles", []), "mention_users": " ".join(str(u) for u in c.get("mentionUsers", [])),
        "poll_interval": c.get("pollInterval", 20),
    }


def _vzp_save(gid, cfg):
    c = vzp_monitor_config.get(gid)
    if not c or not c.get("familyId"):
        raise modules.ValidationError("Сначала привяжите семью кнопкой «Привязать семью»")
    c.update({"serverId": cfg["server_id"], "alertChannelId": cfg["alert_channel"], "resultsChannelId": cfg["results_channel"],
              "mentionRoles": cfg["mention_roles"], "mentionUsers": parse_ids(cfg["mention_users"]),
              "pollInterval": cfg["poll_interval"] or 20, "monitoringEnabled": bool(cfg["enabled"])})
    save_data()


async def _vzp_set_family(ctx, p):
    raw = str(p["family"]).strip().rstrip("/").split("/")[-1]
    if not raw.isdigit():
        raise modules.ValidationError("Нужен числовой ID семьи или ссылка вида vzp-gta5rp.com/stats/families/15607")
    async with aiohttp.ClientSession() as session:
        org = _vzp_unwrap(await _vzp_get(session, f"/stats/organizations/{raw}"))
    if not isinstance(org, dict) or not org.get("id"):
        raise modules.ValidationError(f"Семья с ID {raw} не найдена в API")
    c = vzp_monitor_config.setdefault(ctx.guild.id, {
        "serverId": None, "alertChannelId": None, "resultsChannelId": None, "mentionRoles": [], "mentionUsers": [],
        "pollInterval": 20, "monitoringEnabled": False})
    c["familyId"], c["familyName"] = org.get("id"), org.get("name")
    vzp_processed_events.setdefault(ctx.guild.id, {})
    save_data()
    return f"Привязана семья «{org.get('name')}»"


reg(modules.Module(
    key="vzp_monitor", title="Мониторинг ВЗП", icon="⚔️", category="family", default_enabled=False,
    description="Следит за войнами семьи через API vzp-gta5rp.com и пишет о начале и результатах.",
    fields=[
        {"key": "family", "label": "Привязанная семья", "type": "text", "default": "", "group": "Семья",
         "help": "Меняется кнопкой «Привязать семью» ниже (это поле только для просмотра)", "readonly": True},
        {"key": "server_id", "label": "ID сервера GTA5RP", "type": "number", "default": None, "min": 1, "max": 99, "group": "Сервер",
         "help": "1 = Downtown, 20 = Murrieta и т.д. (номера серверов GTA5RP)"},
        {"key": "poll_interval", "label": "Интервал опроса, сек", "type": "number", "default": 20, "min": 5, "max": 600, "group": "Сервер"},
        {"key": "alert_channel", "label": "Канал: начало войны", "type": "channel", "kind": "text", "default": None, "group": "Уведомления"},
        {"key": "results_channel", "label": "Канал: результаты", "type": "channel", "kind": "text", "default": None, "group": "Уведомления"},
        {"key": "mention_roles", "label": "Роли для тега", "type": "roles", "default": [], "group": "Уведомления"},
        {"key": "mention_users", "label": "Пользователи для тега (ID)", "type": "text", "default": "", "group": "Уведомления",
         "help": "Через пробел или запятую"},
    ],
    loader=_vzp_load, saver=_vzp_save,
    actions=[modules.Action("family", "Привязать семью", _vzp_set_family, params=[
        {"key": "family", "label": "ID семьи или ссылка", "type": "text"}])],
))
