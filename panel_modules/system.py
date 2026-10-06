"""Panel modules: system settings (branding, admin roles, backups, voice presence, PvP)."""

import discord

import pvp_module
from core import modules
from legacy.state import (
    BACKUP_AVAILABLE_FILES, admin_roles, backup_settings, extra_admin_roles, guild_branding, save_data,
    voice_presence_settings,
)
from legacy.timers import _voice_presence_ensure, send_backup_now
from panel_modules.common import bot, put, spawn

reg = modules.register


# ── Брендинг ────────────────────────────────────────────────────────────────

def _brand_load(gid):
    b = guild_branding.get(gid) or {}
    return {"footer_icon": b.get("footer_icon") or "", "approve_gif": b.get("approve_gif") or "", "afk_image": b.get("afk_image") or ""}


def _brand_save(gid, cfg):
    put(guild_branding, gid, {k: (cfg[k] or "").strip() for k in ("footer_icon", "approve_gif", "afk_image") if (cfg[k] or "").strip()})
    save_data()


reg(modules.Module(
    key="branding", title="Брендинг", icon="🖼", category="system", toggleable=False,
    description="Картинки в сообщениях бота. Пусто — стандартные.",
    fields=[
        {"key": "footer_icon", "label": "Иконка в футере", "type": "text", "default": "", "help": "Прямая ссылка на картинку"},
        {"key": "approve_gif", "label": "GIF при одобрении заявки", "type": "text", "default": ""},
        {"key": "afk_image", "label": "Картинка панели АФК", "type": "text", "default": ""},
    ],
    loader=_brand_load, saver=_brand_save,
))


# ── Администраторы бота ─────────────────────────────────────────────────────

def _admin_load(gid):
    return {"admin_role": admin_roles.get(gid), "extra_roles": extra_admin_roles.get(gid, [])}


def _admin_save(gid, cfg):
    put(admin_roles, gid, cfg["admin_role"])
    put(extra_admin_roles, gid, cfg["extra_roles"])
    save_data()


reg(modules.Module(
    key="admin_access", title="Администраторы бота", icon="🔑", category="system", toggleable=False,
    description="Кто может пользоваться админ-командами бота (помимо администраторов Discord).",
    fields=[
        {"key": "admin_role", "label": "Главная админ-роль", "type": "role", "default": None},
        {"key": "extra_roles", "label": "Дополнительные админ-роли", "type": "roles", "default": []},
    ],
    loader=_admin_load, saver=_admin_save,
))


# ── Бэкапы ──────────────────────────────────────────────────────────────────

INTERVALS = [1, 3, 6, 12, 24, 48]


def _backup_load(gid):
    b = backup_settings.get(gid, {})
    return {"channel": b.get("channel_id"), "interval": str(b.get("interval_hours", 1)), "files": b.get("files", []),
            "last": b.get("last_backup") or "ещё не было"}


def _backup_save(gid, cfg):
    b = backup_settings.setdefault(gid, {})
    b["channel_id"], b["interval_hours"], b["files"] = cfg["channel"], int(cfg["interval"]), cfg["files"]
    save_data()


async def _backup_now(ctx, p):
    if not await send_backup_now(ctx.guild):
        raise modules.ValidationError("Не вышло: выберите канал и хотя бы один файл (и сохраните)")
    return "Бэкап отправлен в канал"


reg(modules.Module(
    key="backup", title="Резервные копии", icon="💾", category="system", toggleable=False,
    description="Бот по расписанию отправляет выбранные файлы данных в канал.",
    fields=[
        {"key": "channel", "label": "Канал для бэкапов", "type": "channel", "kind": "text", "default": None},
        {"key": "interval", "label": "Период", "type": "select", "default": "1",
         "options": [{"value": str(h), "label": f"каждые {h} ч."} for h in INTERVALS]},
        {"key": "files", "label": "Файлы", "type": "multiselect", "default": [],
         "options": list(BACKUP_AVAILABLE_FILES)},
        {"key": "last", "label": "Последний бэкап", "type": "text", "default": "", "readonly": True},
    ],
    loader=_backup_load, saver=_backup_save,
    actions=[modules.Action("now", "Сделать бэкап сейчас", _backup_now)],
))


# ── Войс-присутствие ────────────────────────────────────────────────────────

def _vp_load(gid):
    v = voice_presence_settings.get(gid, {})
    return {"enabled": bool(v.get("enabled")), "channel": v.get("channel_id")}


async def _vp_apply(guild, enabled):
    if enabled:
        await _voice_presence_ensure(guild)
    else:
        vc = guild.voice_client
        if vc and vc.is_connected():
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass


def _vp_save(gid, cfg):
    if cfg["enabled"] and not cfg["channel"]:
        raise modules.ValidationError("Сначала выберите голосовой канал")
    guild = bot.get_guild(gid)
    if cfg["channel"] and guild is not None:
        ch = guild.get_channel(cfg["channel"])
        if not isinstance(ch, discord.VoiceChannel):
            raise modules.ValidationError("Выбранный канал не найден или не является голосовым")
    prev = voice_presence_settings.get(gid, {})
    voice_presence_settings[gid] = {"channel_id": cfg["channel"], "enabled": bool(cfg["enabled"])}
    save_data()
    if (bool(prev.get("enabled")), prev.get("channel_id")) != (bool(cfg["enabled"]), cfg["channel"]) and bot.get_guild(gid):
        spawn(_vp_apply(bot.get_guild(gid), bool(cfg["enabled"])))


reg(modules.Module(
    key="voice_presence", title="Войс-присутствие", icon="🎧", category="system",
    description="Бот сидит в выбранном голосовом канале, пока онлайн, и переподключается, если его выкинет.",
    fields=[{"key": "channel", "label": "Голосовой канал", "type": "channel", "kind": "voice", "default": None}],
    loader=_vp_load, saver=_vp_save,
))


# ── PvP-события ─────────────────────────────────────────────────────────────

def _pvp_load(gid):
    g = pvp_module.guild_config(pvp_module.load_config(), gid)
    return {"channel": g.get("channel_id"), "image_url": g.get("image_url") or "",
            "events": [k for k in pvp_module.EVENTS if g["events"].get(k, True)]}


def _pvp_save(gid, cfg):
    full = pvp_module.load_config()
    g = pvp_module.guild_config(full, gid)
    g["channel_id"] = cfg["channel"]
    g["image_url"] = (cfg["image_url"] or "").strip() or None
    g["events"] = {k: (k in cfg["events"]) for k in pvp_module.EVENTS}
    pvp_module.save_config(full)


reg(modules.Module(
    key="pvp", title="PvP-события", icon="💥", category="family", toggleable=False,
    description="Анонсы AirDrop, чёрного рынка и войны за граффити по расписанию.",
    fields=[
        {"key": "channel", "label": "Канал публикаций", "type": "channel", "kind": "text", "default": None},
        {"key": "events", "label": "Какие события публиковать", "type": "multiselect", "default": [],
         "options": [{"value": k, "label": f"{m['label']} — {', '.join(m['times'])} МСК"} for k, m in pvp_module.EVENTS.items()]},
        {"key": "image_url", "label": "Картинка к постам", "type": "text", "default": ""},
    ],
    loader=_pvp_load, saver=_pvp_save,
))
