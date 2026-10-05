"""state -- split out of main.py."""

import json
import os
from datetime import datetime



DATA_FILE     = "data.json"


OBSHAK_FILE   = "obshak.json"


POINTS_FILE   = "points.json"


CHIPS_FILE    = "chips.json"


ROULETTE_FILE = "roulette.json"


DEFAULT_TICKET_IMAGE = "https://i.imgur.com/umswh4i.gif"


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


# 📊 СТАТИСТИКА РЕКРУТОВ { guild_id: { user_id: { "approved": int, "rejected": int } } }
recruit_stats: dict = {}


# ⚠️ КАНАЛ ЛОГОВ ВЫДАЧИ ВАРНОВ РЕКРУТАМИ { guild_id: channel_id }
warn_log_channels: dict = {}


# ⚠️ СООБЩЕНИЕ О ТЕКУЩЕМ ВАРНЕ (удаляется при снятии)
# { guild_id: { user_id: { "channel_id": int, "message_id": int } } }
warn_log_messages: dict = {}


# 🎖 ПАНЕЛЬ КАБИНЕТА РЕКРУТА
# { guild_id: { "channel_id": int, "message_id": int, "text": str|None, "image_url": str|None } }
recruit_cabinet_panels: dict = {}


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


# ⛏ РОЛЬ ВЗХ { guild_id: role_id }
vzh_roles: dict = {}


# ⛏ РОЛЬ ВЗХ-2 { guild_id: role_id }
vzh_roles2: dict = {}


# 📅 РАСПИСАНИЕ ВЗХ ПО ФРАКЦИЯМ { guild_id: "banda" | "mafia" }
# Банды: Пн+Ср+Пт+Вс, Мафии: Вт+Чт+Сб+Вс — используется, чтобы !vzh 20:00
# правильно определял дату сбора, даже если создаётся за несколько дней до самого ВЗХ.
vzh_schedule_settings: dict = {}


# 🎙 ВОЙС-ПРИСУТСТВИЕ — бот всегда сидит в голосовом канале, пока онлайн
# { guild_id: {"channel_id": int|None, "enabled": bool} }
voice_presence_settings: dict = {}


# 📣 РОЛЬ ДЛЯ ТЕГА В РЕАКИ-2 { guild_id: role_id }
list_roles2: dict = {}


# 💾 РЕЗЕРВНОЕ КОПИРОВАНИЕ ФАЙЛОВ ДАННЫХ
# { guild_id: { "channel_id": int, "interval_hours": int, "files": [str, ...], "last_backup": str|None } }
backup_settings: dict = {}


BACKUP_AVAILABLE_FILES = [
    "data.json", "points.json", "chips.json",
    "obshak.json", "roulette.json",
    "laws_config.json", "laws_usage.json", "laws.sqlite",
]


# 🎯 РОЛИ ДОСТУПА К КОМАНДАМ СБОРОВ { guild_id: { "vzp": [role_id,...], "vzh": [...], "list": [...] } }
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


# { guild_id: { "member_role_id": int, "academy_role_id": int,
#               "channel_id": int, "message_id": int } }
roster_settings: dict = {}


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
            "recruit_stats":        {str(g): {str(u): v for u, v in us.items()} for g, us in recruit_stats.items()},
            "warn_log_channels":    {str(g): v for g, v in warn_log_channels.items()},
            "warn_log_messages":    {str(g): {str(u): v for u, v in us.items()} for g, us in warn_log_messages.items()},
            "recruit_cabinet_panels": {str(g): v for g, v in recruit_cabinet_panels.items()},
            "backup_settings":       {str(g): v for g, v in backup_settings.items()},
            "vzh_roles":             {str(g): v for g, v in vzh_roles.items()},
            "vzh_roles2":            {str(g): v for g, v in vzh_roles2.items()},
            "vzh_schedule_settings": {str(g): v for g, v in vzh_schedule_settings.items()},
            "voice_presence_settings": {str(g): v for g, v in voice_presence_settings.items()},
        }
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"WARNING: Failed to save data: {e}")


def load_data():
    """Загружает данные из data.json при старте."""
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
        for g, us in data.get("recruit_stats", {}).items():
            recruit_stats[int(g)] = {int(u): v for u, v in us.items()}
        for g, v in data.get("warn_log_channels", {}).items():
            warn_log_channels[int(g)] = v
        for g, us in data.get("warn_log_messages", {}).items():
            warn_log_messages[int(g)] = {int(u): v for u, v in us.items()}
        for g, v in data.get("recruit_cabinet_panels", {}).items():
            recruit_cabinet_panels[int(g)] = v
        for g, v in data.get("backup_settings", {}).items():
            backup_settings[int(g)] = v
        for g, v in data.get("vzh_roles", {}).items():
            vzh_roles[int(g)] = v
        for g, v in data.get("vzh_roles2", {}).items():
            vzh_roles2[int(g)] = v
        for g, v in data.get("vzh_schedule_settings", {}).items():
            vzh_schedule_settings[int(g)] = v
        for g, v in data.get("voice_presence_settings", {}).items():
            voice_presence_settings[int(g)] = v

        print("OK: Data loaded from data.json")
    except Exception as e:
        print(f"WARNING: Failed to load data: {e}")


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
