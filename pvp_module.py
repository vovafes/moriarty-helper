"""
pvp_module.py — модуль расписания PvP-событий фракций (GTA5RP).

Подключается к main.py через setup_pvp(bot).

Событий три (время фиксировано разработчиками сервера, не настраивается):
    • AirDrop            — каждые 3 часа начиная с 02:00 МСК
    • Чёрный рынок       — раз в день, 20:00 МСК
    • Война за граффити  — каждые 2 часа начиная с 08:45 МСК

Каждое событие можно включить/выключить и выбрать канал для публикации
(общий канал на сервер — задаётся один раз, события просто вкл/выкл).

Команды:
    /pvp          — показать расписание всех событий (когда ближайшее)
    /pvp_канал    — задать канал публикации (только админ)
    /pvp_событие  — включить/выключить публикацию конкретного события (только админ)
    /pvp_статус   — текущие настройки модуля (только админ)
    /pvp_фото     — задать картинку-шпаргалку, которая будет прикрепляться к постам (только админ)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import tasks

CONFIG_FILE = "pvp_config.json"
MSK = timezone(timedelta(hours=3))

# Ссылка-шпаргалка, которую прислал пользователь — используется как запасной
# вариант, пока админ не задаст свою картинку через /pvp_фото.
DEFAULT_REFERENCE_URL = "https://imgur.com/a/naCIsB5"

EVENTS = {
    "airdrop": {
        "label": "🪂 AirDrop",
        "times": [f"{h:02d}:00" for h in range(2, 24, 3)],  # 02:00, 05:00, ... 23:00
        "color": 0xF1C40F,
        "announce_title": "🪂 AirDrop начался!",
        "announce_desc": "Открыт AirDrop. Следующий — через 3 часа.",
    },
    "black_market": {
        "label": "🏴 Чёрный рынок",
        "times": ["20:00"],
        "color": 0x2C2F33,
        "announce_title": "🏴 Чёрный рынок открыт!",
        "announce_desc": "Чёрный рынок открыт до 20:00 МСК завтра.",
    },
    "graffiti_war": {
        "label": "🎨 Война за граффити",
        "times": [f"{(8 + 2 * i) % 24:02d}:45" for i in range(12)],  # 08:45, 10:45, ... 06:45
        "color": 0x9B59B6,
        "announce_title": "🎨 Война за граффити началась!",
        "announce_desc": "Началась война за граффити. Следующая — через 2 часа.",
    },
}


# ──────────────────────────────────────────────────────────────
# Конфиг (JSON, по гильдиям)
# ──────────────────────────────────────────────────────────────

def load_config() -> dict:
    p = Path(CONFIG_FILE)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    Path(CONFIG_FILE).write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def guild_config(cfg: dict, guild_id: int) -> dict:
    g = cfg.setdefault(str(guild_id), {})
    g.setdefault("channel_id", None)
    g.setdefault("events", {key: True for key in EVENTS})
    g.setdefault("image_url", None)
    g.setdefault("last_posted", {})
    return g


def now_msk() -> datetime:
    return datetime.now(MSK).replace(tzinfo=None)


def _next_occurrence(times: list[str], now: datetime) -> datetime:
    today_candidates = []
    for t in times:
        h, m = map(int, t.split(":"))
        cand = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if cand <= now:
            cand += timedelta(days=1)
        today_candidates.append(cand)
    return min(today_candidates)


# ──────────────────────────────────────────────────────────────
# Embeds
# ──────────────────────────────────────────────────────────────

def _schedule_embed(cfg: dict, guild_id: int) -> discord.Embed:
    g = guild_config(cfg, guild_id)
    now = now_msk()
    embed = discord.Embed(
        title="⚔️ Расписание PvP-событий",
        color=0x5865F2,
        timestamp=datetime.now(),
    )
    for key, meta in EVENTS.items():
        enabled = g["events"].get(key, True)
        nxt = _next_occurrence(meta["times"], now)
        status = "🟢 вкл" if enabled else "🔴 выкл"
        embed.add_field(
            name=f"{meta['label']} · {status}",
            value=f"Время: {', '.join(meta['times'])} (МСК)\nБлижайшее: **{nxt.strftime('%d.%m %H:%M')}** МСК",
            inline=False,
        )
    channel_id = g.get("channel_id")
    embed.add_field(
        name="Канал публикаций",
        value=f"<#{channel_id}>" if channel_id else "не задан (`/pvp_канал`)",
        inline=False,
    )
    image_url = g.get("image_url") or DEFAULT_REFERENCE_URL
    if image_url and image_url.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
        embed.set_image(url=image_url)
    else:
        embed.add_field(name="Шпаргалка", value=image_url, inline=False)
    embed.set_footer(text="Время начала событий фиксировано сервером GTA5RP")
    return embed


def _announce_embed(key: str, g: dict) -> discord.Embed:
    meta = EVENTS[key]
    embed = discord.Embed(
        title=meta["announce_title"],
        description=meta["announce_desc"],
        color=meta["color"],
        timestamp=datetime.now(),
    )
    embed.set_footer(text=f"{now_msk().strftime('%H:%M')} МСК")
    image_url = g.get("image_url")
    if image_url:
        embed.set_image(url=image_url)
    return embed


# ──────────────────────────────────────────────────────────────
# Фоновая задача публикации
# ──────────────────────────────────────────────────────────────

def setup_pvp(bot) -> None:
    tree = bot.tree

    @tasks.loop(minutes=1)
    async def pvp_schedule_loop():
        cfg = load_config()
        now = now_msk()
        hhmm = now.strftime("%H:%M")
        today = now.strftime("%Y-%m-%d")
        changed = False

        for guild_id_str, g in list(cfg.items()):
            channel_id = g.get("channel_id")
            if not channel_id:
                continue
            for key, meta in EVENTS.items():
                if hhmm not in meta["times"]:
                    continue
                if not g.get("events", {}).get(key, True):
                    continue
                dedup_key = f"{today} {hhmm}"
                if g.get("last_posted", {}).get(key) == dedup_key:
                    continue
                channel = bot.get_channel(channel_id)
                if channel is None:
                    try:
                        channel = await bot.fetch_channel(channel_id)
                    except Exception as e:
                        print(f"WARNING: pvp_schedule_loop channel {channel_id}: {e}")
                        continue
                try:
                    await channel.send(embed=_announce_embed(key, g))
                except Exception as e:
                    print(f"WARNING: pvp_schedule_loop send {key} guild={guild_id_str}: {e}")
                    continue
                g.setdefault("last_posted", {})[key] = dedup_key
                changed = True

        if changed:
            save_config(cfg)

    @pvp_schedule_loop.error
    async def pvp_schedule_loop_error(error: Exception):
        print(f"WARNING: pvp_schedule_loop crashed, restarting: {error}")
        if not pvp_schedule_loop.is_running():
            pvp_schedule_loop.restart()

    @bot.listen("on_ready")
    async def _pvp_on_ready():
        if not pvp_schedule_loop.is_running():
            pvp_schedule_loop.start()

    # ─── Slash-команды ───────────────────────────────────────

    @tree.command(name="pvp", description="Расписание PvP-событий фракций")
    async def pvp_cmd(interaction: discord.Interaction):
        cfg = load_config()
        await interaction.response.send_message(embed=_schedule_embed(cfg, interaction.guild_id))

    @tree.command(name="pvp_канал", description="Задать канал для публикации PvP-событий")
    @app_commands.describe(канал="Канал, куда бот будет постить начало событий")
    async def pvp_channel_cmd(interaction: discord.Interaction, канал: discord.TextChannel):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Только для админа.", ephemeral=True)
            return
        cfg = load_config()
        g = guild_config(cfg, interaction.guild_id)
        g["channel_id"] = канал.id
        save_config(cfg)
        await interaction.response.send_message(f"✅ PvP-события будут публиковаться в {канал.mention}.")

    @tree.command(name="pvp_событие", description="Включить/выключить публикацию PvP-события")
    @app_commands.describe(событие="Какое событие", включено="Включить или выключить публикацию")
    @app_commands.choices(событие=[
        app_commands.Choice(name="AirDrop", value="airdrop"),
        app_commands.Choice(name="Чёрный рынок", value="black_market"),
        app_commands.Choice(name="Война за граффити", value="graffiti_war"),
    ])
    async def pvp_event_cmd(interaction: discord.Interaction, событие: app_commands.Choice[str], включено: bool):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Только для админа.", ephemeral=True)
            return
        cfg = load_config()
        g = guild_config(cfg, interaction.guild_id)
        g["events"][событие.value] = включено
        save_config(cfg)
        state = "включена" if включено else "выключена"
        await interaction.response.send_message(f"✅ Публикация «{событие.name}» {state}.")

    @tree.command(name="pvp_фото", description="Задать картинку, которая будет прикрепляться к постам о событиях")
    @app_commands.describe(url="Прямая ссылка на картинку (.png/.jpg/.webp) или 'сброс'")
    async def pvp_photo_cmd(interaction: discord.Interaction, url: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Только для админа.", ephemeral=True)
            return
        cfg = load_config()
        g = guild_config(cfg, interaction.guild_id)
        if url.strip().lower() in ("сброс", "reset", "off", "none"):
            g["image_url"] = None
            save_config(cfg)
            await interaction.response.send_message("✅ Картинка убрана из постов.")
            return
        if not url.lower().startswith("http") or not url.lower().split("?")[0].endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
            await interaction.response.send_message(
                "❌ Нужна прямая ссылка на файл изображения (заканчивается на .png/.jpg/.webp/.gif). "
                "У имгур-альбома возьми ссылку через «Copy image address», а не адрес страницы альбома.",
                ephemeral=True,
            )
            return
        g["image_url"] = url
        save_config(cfg)
        await interaction.response.send_message("✅ Картинка будет прикрепляться к постам о событиях.")

    @tree.command(name="pvp_статус", description="Текущие настройки модуля PvP-событий")
    async def pvp_status_cmd(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Только для админа.", ephemeral=True)
            return
        cfg = load_config()
        await interaction.response.send_message(embed=_schedule_embed(cfg, interaction.guild_id), ephemeral=True)
