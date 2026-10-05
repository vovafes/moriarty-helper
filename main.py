"""
Moriarty Helper -- entry point.

The old 8.5k-line main.py now lives in legacy/ (one file per feature, behaviour
unchanged); the dashboard-era modules live in modules/ (cogs with a settings
schema, managed from the web panel). This file only wires them together.
"""

import os

import pvp_module
from legacy.app import bot, tree  # noqa: F401  (tree registers the slash commands below)

# Importing a legacy module registers its commands, views, events and loops on `bot`.
from legacy import (  # noqa: F401
    afk, cabinet, contracts, core_events, economy, events, feedback, obshak,
    private_vc, recruit, roster, roulette, settings_panel, shop, stats_gta,
    tickets, timers, voice_rewards, vzp_monitor, warns,
)

# Старый функционал в веб-панели (регистрирует модули панели поверх legacy-состояния)
import panel_modules  # noqa: F401

# Юр-ассистент Murrieta (модуль laws_module.py)
try:
    from laws_module import setup_laws
    setup_laws(bot)
except Exception as _laws_err:
    print(f"[laws_module] не подключён: {_laws_err}")

# Расписание PvP-событий фракций (модуль pvp_module.py)
try:
    pvp_module.setup_pvp(bot)
except Exception as _pvp_err:
    print(f"[pvp_module] не подключён: {_pvp_err}")

# Модули с настройками из веб-панели (modules/*.py)
MODULE_EXTENSIONS: list[str] = [
    "modules.server_logging",
    "modules.moderation",
    "modules.automod",
    "modules.anti_nuke",
    "modules.giveaways",
    "modules.automations",
    "modules.support_tickets",
]


async def _setup_hook():
    # Сначала модули: они регистрируют свои маршруты панели, а после старта сервера
    # новые маршруты добавить уже нельзя.
    for ext in MODULE_EXTENSIONS:
        try:
            await bot.load_extension(ext)
        except Exception as e:
            print(f"WARNING: модуль {ext} не загрузился: {e}")
    # Веб-панель живёт в event loop бота и на том же $PORT, что раньше занимал
    # health-сервер (GET /health по-прежнему отвечает "OK").
    try:
        from dashboard import server as dashboard_server
        await dashboard_server.start(bot)
    except Exception as e:  # панель не должна ронять бота
        print(f"WARNING: dashboard не запустился: {e}")


bot.setup_hook = _setup_hook

if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_TOKEN"))
