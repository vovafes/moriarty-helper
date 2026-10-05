"""Panel modules: economy (balances, voice rewards, shop, roulette)."""

import time

import discord

from core import modules
from legacy.helpers import add_chips, add_points, get_chips, get_points, set_points, _footer
from legacy.roulette import roulette_stats
from legacy.shop import ShopView, refresh_shop_message
from legacy.helpers import build_shop_embed
from legacy.state import (
    casino_role_luck, chips_db, guild_shop_items, message_counts, points_db, save_data, shop_log_channels,
    shop_manager_roles, shop_panels, voice_minutes, voice_reward_settings,
)
from legacy.voice_rewards import _get_voice_settings
from panel_modules.common import bot, name_of, publish_panel, put, role_name, spawn

reg = modules.register


# ── Баланс и участники ──────────────────────────────────────────────────────

def _balances(guild):
    uids = set(points_db.get(guild.id, {})) | set(chips_db.get(guild.id, {})) \
        | set(message_counts.get(guild.id, {})) | set(voice_minutes.get(guild.id, {}))
    rows = [{"user": name_of(guild, u), "points": get_points(guild.id, u), "chips": get_chips(guild.id, u),
             "messages": message_counts.get(guild.id, {}).get(u, 0), "voice": voice_minutes.get(guild.id, {}).get(u, 0)}
            for u in uids]
    rows.sort(key=lambda r: (-r["points"], -r["chips"]))
    return rows[:200]


def _money_action(kind, sign, label, danger=False):
    getter, adder = (get_points, add_points) if kind == "points" else (get_chips, add_chips)

    async def run(ctx, p):
        if ctx.guild.get_member(p["user"]) is None:
            raise modules.ValidationError("Участник не найден на сервере")
        if sign < 0:
            current = getter(ctx.guild.id, p["user"])
            take = min(current, p["amount"])
            adder(ctx.guild.id, p["user"], -take)
            return f"Снято {take}; баланс {getter(ctx.guild.id, p['user'])}"
        adder(ctx.guild.id, p["user"], p["amount"])
        return f"Выдано {p['amount']}; баланс {getter(ctx.guild.id, p['user'])}"

    unit = "💎 баллы" if kind == "points" else "🎰 фишки"
    return modules.Action(f"{kind}_{'give' if sign > 0 else 'take'}", f"{label} ({unit.split()[0]})", run, params=[
        {"key": "user", "label": "Участник (ID)", "type": "user"},
        {"key": "amount", "label": "Количество", "type": "number", "min": 1, "max": 100000000}], danger=danger)


reg(modules.Module(
    key="economy", title="Баллы и фишки", icon="💎", category="economy", toggleable=False,
    description="Алмазы 💎 (за активность) и фишки 🎰 (для рулетки): балансы, выдача и списание.",
    fields=[],
    tables=[{"id": "balances", "title": "Балансы и активность (топ-200)", "columns": [
        {"key": "user", "label": "Участник"}, {"key": "points", "label": "💎 Баллы"}, {"key": "chips", "label": "🎰 Фишки"},
        {"key": "messages", "label": "Сообщений"}, {"key": "voice", "label": "Минут в войсе"}]}],
    actions=[_money_action("points", +1, "Выдать"), _money_action("points", -1, "Снять", danger=True),
             _money_action("chips", +1, "Выдать"), _money_action("chips", -1, "Снять", danger=True)],
))
modules.register_table("economy", "balances", _balances)


# ── Награда за голос ────────────────────────────────────────────────────────

def _voice_load(gid):
    s = _get_voice_settings(gid)
    return {"amount": s.get("amount", 10), "amount_game": s.get("amount_game", 15), "game_name": s.get("game_name", ""),
            "categories": s.get("categories", []), "excluded": s.get("excluded_channels", [])}


def _voice_save(gid, cfg):
    s = _get_voice_settings(gid)
    s["amount"] = max(0, cfg["amount"] or 0)
    s["amount_game"] = max(1, cfg["amount_game"] or 1)
    s["game_name"] = (cfg["game_name"] or "").strip() or "GTA5RP"
    s["categories"] = cfg["categories"]
    s["excluded_channels"] = cfg["excluded"]
    save_data()


reg(modules.Module(
    key="voice_rewards", title="Награда за голос", icon="🎙", category="economy", toggleable=False,
    description="Алмазы за время в голосовых каналах; больше, если участник играет.",
    fields=[
        {"key": "amount", "label": "💎 в минуту (без игры)", "type": "number", "default": 10, "min": 0, "max": 100000, "group": "Ставки"},
        {"key": "amount_game", "label": "💎 в минуту (в игре)", "type": "number", "default": 15, "min": 1, "max": 100000, "group": "Ставки"},
        {"key": "game_name", "label": "Название игры", "type": "text", "default": "", "group": "Ставки",
         "help": "По статусу участника; например RAGE Multiplayer"},
        {"key": "categories", "label": "Категории, где начисляется", "type": "channels", "kind": "category", "default": [], "group": "Где начислять"},
        {"key": "excluded", "label": "Исключённые каналы", "type": "channels", "kind": "voice", "default": [], "group": "Где начислять"},
    ],
    loader=_voice_load, saver=_voice_save,
))


# ── Магазин ─────────────────────────────────────────────────────────────────

ITEM_ACTIONS = [{"value": "notify", "label": "Только уведомление"}, {"value": "give_role", "label": "Выдать роль"},
                {"value": "remove_warn", "label": "Снять варн"}]


def _shop_load(gid):
    return {"manager_role": shop_manager_roles.get(gid), "log_channel": shop_log_channels.get(gid)}


def _shop_save(gid, cfg):
    put(shop_manager_roles, gid, cfg["manager_role"])
    put(shop_log_channels, gid, cfg["log_channel"])
    save_data()


def _shop_items(guild):
    return [{"id": iid, "name": f"{i.get('emoji', '🛒')} {i.get('name')}", "price": i.get("price"),
             "action": i.get("action"), "role": role_name(guild, i.get("role_id")) if i.get("role_id") else "—",
             "description": i.get("description")} for iid, i in guild_shop_items.get(guild.id, {}).items()]


async def _item_add(ctx, p):
    items = guild_shop_items.setdefault(ctx.guild.id, {})
    iid = str(int(time.time()))
    while iid in items:
        iid = str(int(iid) + 1)
    role_id = p.get("role")
    if p["action"] == "give_role" and not role_id:
        raise modules.ValidationError("Для действия «Выдать роль» выберите роль")
    items[iid] = {"name": p["name"].strip(), "price": int(p["price"]), "emoji": (p.get("emoji") or "").strip() or "🛒",
                  "description": (p.get("description") or "").strip(), "action": p["action"],
                  "role_id": role_id if p["action"] == "give_role" else None}
    save_data()
    await refresh_shop_message(ctx.guild)
    return f"Товар добавлен (ID {iid})"


async def _item_remove(ctx, p):
    items = guild_shop_items.get(ctx.guild.id, {})
    if p["id"] not in items:
        raise modules.ValidationError("Товар с таким ID не найден (смотрите таблицу ниже)")
    name = items.pop(p["id"])["name"]
    save_data()
    await refresh_shop_message(ctx.guild)
    return f"Товар «{name}» удалён"


async def _shop_publish(ctx, p):
    return await publish_panel(ctx.guild, p["channel"], shop_panels, build_shop_embed, ShopView(ctx.guild.id))


reg(modules.Module(
    key="shop", title="Магазин", icon="🛒", category="economy", toggleable=False,
    description="Товары за баллы: снятие варна, выдача роли или уведомление менеджеру.",
    fields=[
        {"key": "manager_role", "label": "Менеджер магазина", "type": "role", "default": None, "group": "Настройки",
         "help": "Роль, которой приходят заказы"},
        {"key": "log_channel", "label": "Лог покупок", "type": "channel", "kind": "text", "default": None, "group": "Настройки"},
    ],
    loader=_shop_load, saver=_shop_save,
    tables=[{"id": "items", "title": "Товары", "columns": [
        {"key": "id", "label": "ID"}, {"key": "name", "label": "Товар"}, {"key": "price", "label": "Цена 💎"},
        {"key": "action", "label": "Действие"}, {"key": "role", "label": "Роль"}, {"key": "description", "label": "Описание"}]}],
    actions=[
        modules.Action("add", "Добавить товар", _item_add, params=[
            {"key": "name", "label": "Название", "type": "text"},
            {"key": "price", "label": "Цена (баллы)", "type": "number", "min": 0, "max": 100000000},
            {"key": "emoji", "label": "Эмодзи", "type": "text", "required": False},
            {"key": "description", "label": "Описание", "type": "text", "required": False},
            {"key": "action", "label": "Действие", "type": "select", "default": "notify", "options": ITEM_ACTIONS},
            {"key": "role", "label": "Роль (для «Выдать роль»)", "type": "role", "required": False}]),
        modules.Action("remove", "Удалить товар", _item_remove, params=[{"key": "id", "label": "ID товара", "type": "text"}], danger=True),
        modules.Action("publish", "Опубликовать панель магазина", _shop_publish, params=[
            {"key": "channel", "label": "Канал", "type": "channel", "kind": "text"}]),
    ],
))
modules.register_table("shop", "items", _shop_items)


# ── Казино: удача по ролям ──────────────────────────────────────────────────

def _luck_table(guild):
    return [{"role": role_name(guild, rid), "mult": f"{m}x"} for rid, m in casino_role_luck.get(guild.id, {}).items()]


async def _luck_set(ctx, p):
    if ctx.guild.get_role(p["role"]) is None:
        raise modules.ValidationError("Роль не найдена")
    casino_role_luck.setdefault(ctx.guild.id, {})[p["role"]] = float(p["mult"])
    save_data()
    return f"{role_name(ctx.guild, p['role'])}: {p['mult']}x"


async def _luck_reset(ctx, p):
    if casino_role_luck.get(ctx.guild.id, {}).pop(p["role"], None) is None:
        raise modules.ValidationError("Для этой роли множитель не задан")
    save_data()
    return "Множитель сброшен"


reg(modules.Module(
    key="roulette", title="Рулетка и казино", icon="🎰", category="economy", toggleable=False,
    description="Множители шанса победы по ролям: 1.0 — честно, меньше — хуже, больше — лучше.",
    fields=[],
    tables=[{"id": "luck", "title": "Множители по ролям", "columns": [{"key": "role", "label": "Роль"}, {"key": "mult", "label": "Множитель"}]}],
    actions=[
        modules.Action("set", "Задать множитель", _luck_set, params=[
            {"key": "role", "label": "Роль", "type": "role"},
            {"key": "mult", "label": "Множитель (0–10)", "type": "number", "min": 0, "max": 10, "step": 0.1, "default": 1}]),
        modules.Action("reset", "Сбросить множитель", _luck_reset, params=[{"key": "role", "label": "Роль", "type": "role"}]),
    ],
))
modules.register_table("roulette", "luck", _luck_table)
