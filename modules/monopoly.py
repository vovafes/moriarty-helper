"""
Monopoly -- /monopoly: weekly task tracker for the GTA5RP Murrieta "MONOPOLIST"
(state factions) and "Battle Pass" (Ghetto / Mafia) boards.

A leader picks a faction, then ticks the tasks done this week per difficulty tier
(drop-down menus). The embed shows points and how many board cells the faction
has moved. Progress resets every Sunday 23:59 MSK; the finished week is banked
into the faction's running total so the board position is never lost.

Sources: forum.gta5rp.com/threads/monopolist.3259111 and /threads/battle-pass.3345574
(tasks are copied from the images there -- edit TASKS if the admins change them).
Assumption: every ticked task adds its tier's points (300 / 500 / 1000), a cell = 1000 points.
"""

import datetime
import json
import time

import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands

from core import db, embeds, modules

TIERS = {
    "easy": ("🟢", "Лёгкие", 300),
    "medium": ("🟡", "Средние", 500),
    "hard": ("🔴", "Сложные", 1000),
}
CELL_POINTS = 1000
MSK = datetime.timezone(datetime.timedelta(hours=3))

_COMMON_MEDIUM = [
    "3 усиленных патруля с другими гос. фракциями (+50 чел., 1,5+ часов)",
    "Отбить 3 поставки контрабанды",
    "Выиграть 10 airdrop",
]

TASKS: dict[str, dict] = {
    "lspd": {
        "title": "LSPD — Police Department", "emoji": "🚓", "board": "Monopolist",
        "easy": [
            "Выписать штрафы 1250", "Доставить 300 угнанных авто", "Провести 14 наборов",
            "Провести 800 арестов в КПЗ/ФТ", "20 успешных отбитий рэкета",
        ],
        "medium": [
            "Выписать штрафы 1600", "Доставить 500 угнанных авто", "Провести 22 набора",
            "Провести 1500 арестов в КПЗ/ФТ", "6 успешных отбитий магазина Ammunation",
            "3 усиленных патруля с другими гос. фракциями (+50 чел., 1,5+ часов)",
            "Отбить 3 поставки контрабанды", "Оформить кейс-файл на рейд", "Выиграть 10 airdrop",
            "Провести успешный рейд", "Сопровождать 25 поставок",
        ],
        "hard": [
            "10 успешных отбитий магазина Ammunation", "Успешно отбить 3 раза Fort Zancudo/SASPA",
            "Провести 2 успешных рейда", "Выиграть 3 поезда (починить и отвезти машину)",
            "Отбить 7 поставок контрабанды",
        ],
    },
    "fib": {
        "title": "FIB — Federal Investigation Bureau", "emoji": "🕵️", "board": "Monopolist",
        "easy": [
            "Провести 14 наборов", "Провести 300 арестов в КПЗ/ФТ", "7 успешных отбитий рэкета",
            "1 усиленный патруль с гос. фракциями (+30 чел. мин.)", "Внедриться в 3 разные криминальные фракции",
        ],
        "medium": [
            "Собрать 10 улик на преступные синдикаты/уличные группировки",
            "3 усиленных патруля с гос. фракциями (+50 чел., 1,5+ часов)",
            "Оформить кейс-файл и получить одобрение", "Провести успешный рейд",
            "8 неоф. допросов с уликами на криминальную фракцию", "Провести 22 набора",
            "Провести 500 арестов в КПЗ/ФТ", "6 успешных отбитий магазина Ammunation",
            "Выиграть 10 airdrop", "Отбить 3 поставки контрабанды", "Сопровождать 25 поставок",
        ],
        "hard": [
            "Отреагировать на 5 похищений (оцепление/переговоры/штурм)", "Провести 2 успешных рейда",
            "10 успешных отбитий магазина Ammunation", "Выиграть 3 поезда (починить и отвезти машину)",
            "Успешно отбить 3 раза Fort Zancudo/SASPA", "Отбить 7 поставок контрабанды",
        ],
    },
    "sheriff": {
        "title": "Sheriff Department", "emoji": "🤠", "board": "Monopolist",
        "easy": [
            "Доставить 300 угнанных авто", "Провести 14 наборов", "Провести 600 арестов в ФТ/КПЗ",
            "1 усиленный патруль с гос. фракциями (+30 чел. от всех)", "Выписать штрафы 600",
        ],
        "medium": [
            "3 усиленных патруля с гос. фракциями (+50 чел., 1,5+ часов)",
            "Участник успешного рейда на крайм фракцию", "Провести 22 набора",
            "5 успешных отбитий магазина Ammunation", "Отбить 3 поставки контрабанды",
            "Сопровождать 25 поставок", "Выиграть 10 airdrop", "Доставить 500 угнанных авто",
            "Сдать 250 кг рыбы/шкур животных", "Проверить 100 лицензий на рыболовство/охоту",
        ],
        "hard": [
            "Быть участником 2 успешных рейдов", "10 успешных отбитий магазина Ammunation",
            "Выиграть 3 поезда (починить и отвезти машину)", "Отбить 3 раза Fort Zancudo/SASPA",
            "Отбить 7 поставок контрабанды", "Оказать помощь на 30 СО",
        ],
    },
    "ng": {
        "title": "National Guard", "emoji": "🪖", "board": "Monopolist",
        "easy": [
            "Провести 14 наборов", "Доставить 5 успешных поставок", "Провести 5 вечерних построений за неделю",
            "Успешно отбить 1 нападение на Форт-Занкудо", "Передать 40 преступников для ареста",
        ],
        "medium": [
            "Провести 22 набора", "Доставить 10 успешных поставок", "Успешно отбить 2 нападения на Форт-Занкудо",
            "Передать 70 преступников для ареста",
            "2 усиленных патруля с гос. фракциями (+50 чел., 1,5+ часов)", "Отбить 3 поставки контрабанды",
            "3 успешных отбития магазина Ammunation", "Пополнить склады фракций на 200.000 материалов",
            "Протокол защиты «код красный» — собрать все гос. структуры на форте",
            "3 общие тренировки с гос. структурами (мин. 40 чел.)",
            "Починить электро-щиток 1 раз при нападении на Fort Zancudo",
        ],
        "hard": [
            "Найти и поймать 10 дезертиров, применить санкции", "Скоординировать все гос. фракции во время ВП",
            "Пополнить склады фракций на 300.000 материалов", "Выиграть 2 поезда (починить и отвезти машину)",
            "Отбить 3 раза Fort Zancudo/SASPA", "Выиграть 10 airdrop",
        ],
    },
    "prison": {
        "title": "Federal Prison", "emoji": "⛓️", "board": "Monopolist",
        "easy": [
            "Провести 14 наборов", "Провести 5 свиданий с заключёнными", "Помочь гос. структурам оформить 45 граждан в СИЗО",
            "Найти и изъять 5 заточек", "Провести 4 тренировки по отбитию нападения на SASPA",
        ],
        "medium": [
            "Провести 22 набора", "Совместно с EMS организовать 3 проверки заключённых",
            "Провести 10 свиданий с заключёнными", "Повысить срок заключения 150 раз",
            "Провести 7 мероприятий для заключённых", "Уменьшить срок заключённым 75 раз",
            "Провести 150 обысков заключённых", "Помочь в 10 специальных операциях",
            "Провести 5 лекций для заключённых", "Провести 7 тренировок (от 7 человек)",
            "Протокол защиты «код синий» — собрать все гос. структуры в тюрьме",
        ],
        "hard": [
            "Отбить 3 нападения на SASPA", "Помочь в 15 специальных операциях",
            "Починить электро-щиток 1 раз при нападении на SASPA", "Провести 15 мероприятий с заключёнными",
            "Проверить документы/опознавательные знаки у КПП 120 человек",
        ],
    },
    "usss": {
        "title": "USSS — Government", "emoji": "🏛️", "board": "Monopolist",
        "easy": [
            "Провести обыск 70 граждан Капитолия", "Сопровождения первым лицам штата 10 раз",
            "Провести 14 наборов (USSS)", "Передать 30 задержанных для ареста",
            "Участие в усиленном патруле с другими гос. фракциями",
        ],
        "medium": [
            "Провести 22 набора", "Передать 50 задержанных для ареста",
            "2 усиленных патруля с гос. фракциями (+50 чел., 1,5+ часов)", "Помочь в 10 специальных операциях",
            "Провести 7 тренировок (от 5 человек)", "Провести 4 тренировки с другими гос. фракциями",
            "Охрана 7 судебных заседаний", "Участие в отбитии Fort Zancudo/SASPA 3 раза",
            "Помочь в отбитии 10 поставок контрабанды", "Реакция на 5 запросов отбития Ammunation",
            "5 успешных антикоррупционных миссий",
        ],
        "hard": [
            "Помочь в 20 специальных операциях", "Отбить 3 раза Fort Zancudo/SASPA",
            "Выиграть 7 airdrop", "Выиграть 2 поезда",
        ],
    },
    "ghetto": {
        "title": "Ghetto (банды)", "emoji": "🔫", "board": "Battle Pass",
        "easy": [
            "Угнать 300 автомобилей", "Провести 300 рэкетов", "Выполнить 300 ограблений домов",
            "Выиграть 8 войн за территорию", "1 место на любом PVP-событии для крайма",
            "Удачно напасть на поезд 2 раза", "5 поставок EMS/ARMY/SPANK (2 — в союзе с бандой/мафией)",
        ],
        "medium": [
            "10 удачных нападений на оружейный магазин", "Выиграть 5 Airdrop'ов", "20 побед в GRAFFITI WAR",
            "Ограбить 700 человек", "2 похищения гос. сотрудников с выкупом",
            "Напасть на Форт Занкудо и разгрузить 7 матовозок", "7 удачных нападений на поезд",
            "3 раза участвовать в отбитии чёрного рынка (1 — победа крайма)",
            "Больше всех очков в МП «Криминальный переполох»",
        ],
        "hard": [
            "1 место в МП «Территория X»", "Выпуск Weazel News про RP-прикрытие фракции",
            "3 успешных допроса сотрудников CID/DB за неделю", "Похитить 5+ человек с выкупом/территорией",
            "Удачный захват гос. фракции / отбитие рейда", "5 захватов флага/сопровождение вагонетки",
        ],
    },
    "mafia": {
        "title": "Mafia (мафии)", "emoji": "🎩", "board": "Battle Pass",
        "easy": [
            "Провести 35 посвящений", "Ограбление 300 человек", "Участие в каждом PVP-событии для крайма",
            "5 поставок EMS/ARMY/SPANK", "Отбить 20 нападений на бизнес", "3 удачных нападения на поезд",
            "Открыть клуб и провести 4 вечеринки",
        ],
        "medium": [
            "1 место на любом PVP-событии для крайма", "Сыграть 2 RP-стрелы", "3 похищения гос. служащих",
            "Провести 70 посвящений", "Выпустить 10 человек из Федеральной Тюрьмы", "Напасть на Форт Занкудо, разгрузить 7 матовозок",
            "Выиграть 5 Airdrop'ов", "3 раза участвовать в отбитии чёрного рынка (1 — победа)",
            "2 раза запросить проценты с ограбления Ammunation", "Больше всех фургонов на МП «Большой угон»",
        ],
        "hard": [
            "Удачный захват гос. фракции / отбитие рейда", "3 успешных допроса сотрудников CID/DB за неделю",
            "Похитить лидера синдиката за выкуп/бизнес", "5 захватов флага/сопровождение вагонетки",
            "Найти внедренца из гос. структуры и провести FCK", "1 место в МП «Территория X»",
        ],
    },
}
# _COMMON_MEDIUM is documentation only (the forum repeats these lines for every state faction).
del _COMMON_MEDIUM

FACTION_CHOICES = [{"value": k, "label": f"{v['emoji']} {v['title']}"} for k, v in TASKS.items()]

db.register_schema("""
CREATE TABLE IF NOT EXISTS monopoly_state (
    guild_id   INTEGER NOT NULL,
    faction    TEXT    NOT NULL,
    week       TEXT    NOT NULL,
    done       TEXT    NOT NULL DEFAULT '{}',
    banked     INTEGER NOT NULL DEFAULT 0,
    channel_id INTEGER,
    message_id INTEGER,
    PRIMARY KEY (guild_id, faction)
);
""")

MODULE = modules.register(modules.Module(
    key="monopoly", title="Монополия / Battle Pass", icon="🎲", category="team", default_enabled=True,
    description="Недельный трекер заданий MONOPOLIST и Battle Pass: /monopoly — выбрать фракцию и отмечать выполненное.",
    fields=[
        {"key": "default_faction", "label": "Фракция по умолчанию", "type": "select", "default": "lspd",
         "options": FACTION_CHOICES},
        {"key": "editor_role", "label": "Кто может отмечать задания (пусто — любой)", "type": "role", "default": None},
    ],
))


def week_id(now: datetime.datetime | None = None) -> str:
    """Monday-based week in MSK; the week flips Sunday 23:59 -> Monday 00:00."""
    d = (now or datetime.datetime.now(MSK)).astimezone(MSK).date()
    monday = d - datetime.timedelta(days=d.weekday())
    return monday.isoformat()


def week_end_ts(wk: str) -> int:
    monday = datetime.datetime.fromisoformat(wk).replace(tzinfo=MSK)
    return int((monday + datetime.timedelta(days=7)).timestamp())


def points_of(faction: str, done: dict) -> int:
    return sum(TIERS[t][2] * len([i for i in idx if 0 <= i < len(TASKS[faction][t])]) for t, idx in done.items() if t in TIERS)


def load(guild_id: int, faction: str) -> dict:
    """Current-week state; rolls the previous week's points into `banked` first."""
    wk = week_id()
    rows = db.query("SELECT * FROM monopoly_state WHERE guild_id=? AND faction=?", (guild_id, faction))
    if not rows:
        db.execute("INSERT INTO monopoly_state (guild_id, faction, week) VALUES (?,?,?)", (guild_id, faction, wk))
        return {"week": wk, "done": {}, "banked": 0, "channel_id": None, "message_id": None}
    r = dict(rows[0])
    done = json.loads(r["done"] or "{}")
    if r["week"] != wk:
        banked = r["banked"] + points_of(faction, done)
        db.execute("UPDATE monopoly_state SET week=?, done='{}', banked=? WHERE guild_id=? AND faction=?",
                   (wk, banked, guild_id, faction))
        r.update(week=wk, banked=banked)
        done = {}
    return {"week": wk, "done": done, "banked": r["banked"], "channel_id": r["channel_id"], "message_id": r["message_id"]}


def save_tier(guild_id: int, faction: str, tier: str, picked: list[int]) -> None:
    st = load(guild_id, faction)
    done = st["done"]
    if picked:
        done[tier] = sorted(picked)
    else:
        done.pop(tier, None)
    db.execute("UPDATE monopoly_state SET done=? WHERE guild_id=? AND faction=?",
               (json.dumps(done), guild_id, faction))


def reset_week(guild_id: int, faction: str) -> None:
    load(guild_id, faction)
    db.execute("UPDATE monopoly_state SET done='{}' WHERE guild_id=? AND faction=?", (guild_id, faction))


def set_panel(guild_id: int, faction: str, channel_id: int | None, message_id: int | None) -> None:
    load(guild_id, faction)
    db.execute("UPDATE monopoly_state SET channel_id=?, message_id=? WHERE guild_id=? AND faction=?",
               (channel_id, message_id, guild_id, faction))


def build_embed(faction: str, st: dict) -> discord.Embed:
    f = TASKS[faction]
    week_pts = points_of(faction, st["done"])
    total = st["banked"] + week_pts
    cells, rest = divmod(total, CELL_POINTS)
    e = embeds.make(f"{f['emoji']} {f['title']} — {f['board']}", color=0xF5A623)
    e.description = (
        f"**За неделю:** {week_pts} баллов\n"
        f"**Всего:** {total} баллов → **{cells}** клеток пройдено (+{rest}/{CELL_POINTS} до следующей)\n"
        f"Сброс недели: <t:{week_end_ts(st['week'])}:R> (вс 23:59 МСК)"
    )
    for tier, (icon, name, pts) in TIERS.items():
        tasks = f[tier]
        done_idx = set(st["done"].get(tier, []))
        lines = [("✅" if i in done_idx else "⬜") + " " + t for i, t in enumerate(tasks)]
        e.add_field(name=f"{icon} {name} — {pts} б. за задание ({len(done_idx)}/{len(tasks)})",
                    value=embeds.clip("\n".join(lines), 1024), inline=False)
    return e


def can_edit(interaction: discord.Interaction) -> bool:
    role_id = modules.get_config(interaction.guild_id, "monopoly").get("editor_role")
    perms = interaction.user.guild_permissions
    if perms.administrator or perms.manage_guild or not role_id:
        return True
    return any(r.id == int(role_id) for r in getattr(interaction.user, "roles", []))


class FactionSelect(discord.ui.Select):
    def __init__(self, current: str):
        super().__init__(placeholder="Выберите фракцию", options=[
            discord.SelectOption(label=v["title"][:100], value=k, emoji=v["emoji"], default=(k == current),
                                 description=v["board"]) for k, v in TASKS.items()])

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(**await render(interaction, self.values[0]))


class TierSelect(discord.ui.Select):
    def __init__(self, faction: str, tier: str, done: set[int]):
        icon, name, pts = TIERS[tier]
        tasks = TASKS[faction][tier]
        super().__init__(placeholder=f"{icon} {name}: отметьте выполненные", min_values=0, max_values=len(tasks),
                         options=[discord.SelectOption(label=t[:100], value=str(i), default=(i in done))
                                  for i, t in enumerate(tasks)])
        self.faction, self.tier = faction, tier

    async def callback(self, interaction: discord.Interaction):
        if not can_edit(interaction):
            return await interaction.response.send_message("❌ Нет прав отмечать задания.", ephemeral=True)
        await db.run(save_tier, interaction.guild_id, self.faction, self.tier, [int(v) for v in self.values])
        await interaction.response.edit_message(**await render(interaction, self.faction))
        await refresh_panel(interaction, self.faction)


class TierButton(discord.ui.Button):
    def __init__(self, faction: str, tier: str):
        icon, name, _ = TIERS[tier]
        super().__init__(label=name, emoji=icon, style=discord.ButtonStyle.secondary)
        self.faction, self.tier = faction, tier

    async def callback(self, interaction: discord.Interaction):
        st = await db.run(load, interaction.guild_id, self.faction)
        view = discord.ui.View(timeout=300)
        view.add_item(TierSelect(self.faction, self.tier, set(st["done"].get(self.tier, []))))
        back = discord.ui.Button(label="Назад", style=discord.ButtonStyle.primary)

        async def _back(i: discord.Interaction):
            await i.response.edit_message(**await render(i, self.faction))
        back.callback = _back
        view.add_item(back)
        await interaction.response.edit_message(view=view)


class ResetButton(discord.ui.Button):
    def __init__(self, faction: str):
        super().__init__(label="Сбросить неделю", emoji="🗑️", style=discord.ButtonStyle.danger)
        self.faction = faction

    async def callback(self, interaction: discord.Interaction):
        if not can_edit(interaction):
            return await interaction.response.send_message("❌ Нет прав.", ephemeral=True)
        await db.run(reset_week, interaction.guild_id, self.faction)
        await interaction.response.edit_message(**await render(interaction, self.faction))
        await refresh_panel(interaction, self.faction)


class PublishButton(discord.ui.Button):
    def __init__(self, faction: str):
        super().__init__(label="Опубликовать в канал", emoji="📌", style=discord.ButtonStyle.success)
        self.faction = faction

    async def callback(self, interaction: discord.Interaction):
        if not can_edit(interaction):
            return await interaction.response.send_message("❌ Нет прав.", ephemeral=True)
        st = await db.run(load, interaction.guild_id, self.faction)
        await interaction.response.defer(ephemeral=True)
        try:
            msg = await interaction.channel.send(embed=build_embed(self.faction, st))
        except discord.HTTPException as e:
            return await interaction.followup.send(f"❌ Не удалось отправить: {e}", ephemeral=True)
        await db.run(set_panel, interaction.guild_id, self.faction, msg.channel.id, msg.id)
        await interaction.followup.send("📌 Опубликовано — дальше это сообщение обновляется само при каждой отметке.", ephemeral=True)


async def render(interaction: discord.Interaction, faction: str) -> dict:
    st = await db.run(load, interaction.guild_id, faction)
    view = discord.ui.View(timeout=600)
    view.add_item(FactionSelect(faction))
    for tier in TIERS:
        view.add_item(TierButton(faction, tier))
    view.add_item(PublishButton(faction))
    view.add_item(ResetButton(faction))
    return {"embed": build_embed(faction, st), "view": view}


async def refresh_panel(interaction: discord.Interaction, faction: str) -> None:
    await refresh_message(interaction.guild, faction)


async def refresh_message(guild: discord.Guild, faction: str) -> None:
    """Keep a previously published channel message in sync."""
    st = await db.run(load, guild.id, faction)
    if not (st["channel_id"] and st["message_id"]):
        return
    ch = guild.get_channel(st["channel_id"])
    if ch is None:
        return
    try:
        msg = ch.get_partial_message(st["message_id"])
        await msg.edit(embed=build_embed(faction, st))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        await db.run(set_panel, guild.id, faction, None, None)


class Monopoly(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="monopoly", description="Недельные задания Monopolist / Battle Pass: выбрать фракцию и отметить выполненное")
    @app_commands.describe(faction="Фракция (по умолчанию — из настроек)")
    @app_commands.choices(faction=[app_commands.Choice(name=o["label"][:100], value=o["value"]) for o in FACTION_CHOICES])
    @app_commands.guild_only()
    async def monopoly(self, interaction: discord.Interaction, faction: str | None = None):
        cfg = modules.get_config(interaction.guild_id, "monopoly")
        if not cfg["enabled"]:
            return await interaction.response.send_message("Модуль выключен.", ephemeral=True)
        faction = faction or cfg.get("default_faction") or "lspd"
        await interaction.response.send_message(**await render(interaction, faction), ephemeral=True)


# ── web panel: /api/guild/{gid}/monopoly[/{faction}] ────────────────────────

def summary(guild_id: int, faction: str) -> dict:
    st = load(guild_id, faction)
    f = TASKS[faction]
    week_pts = points_of(faction, st["done"])
    total = st["banked"] + week_pts
    return {
        "faction": faction, "title": f["title"], "emoji": f["emoji"], "board": f["board"],
        "week_points": week_pts, "total": total, "cells": total // CELL_POINTS, "rest": total % CELL_POINTS,
        "cell_points": CELL_POINTS, "week_end": week_end_ts(st["week"]),
        "published": bool(st["channel_id"] and st["message_id"]),
        "tiers": [{"key": t, "name": name, "icon": icon, "points": pts, "tasks": f[t],
                   "done": [i for i in st["done"].get(t, []) if 0 <= i < len(f[t])]}
                  for t, (icon, name, pts) in TIERS.items()],
    }


def _register_routes(app: web.Application) -> None:
    from dashboard import server

    def _faction(request):
        fk = request.match_info.get("faction")
        if fk not in TASKS:
            raise web.HTTPNotFound(text='{"error":"unknown faction"}', content_type="application/json")
        return fk

    async def get_all(request: web.Request):
        _, guild = server._guild_for(request)
        out = [await db.run(summary, guild.id, k) for k in TASKS]
        return web.json_response(out)

    async def get_one(request: web.Request):
        _, guild = server._guild_for(request)
        return web.json_response(await db.run(summary, guild.id, _faction(request)))

    async def post_one(request: web.Request):
        server._check_origin(request)
        session, guild = server._guild_for(request)
        fk = _faction(request)
        try:
            body = await request.json()
            tier, picked = body["tier"], [int(i) for i in body["picked"]]
        except (ValueError, KeyError, TypeError):
            raise web.HTTPBadRequest(text='{"error":"expected {tier, picked[]}"}', content_type="application/json")
        if tier not in TIERS or any(not 0 <= i < len(TASKS[fk][tier]) for i in picked):
            raise web.HTTPBadRequest(text='{"error":"bad tier or task index"}', content_type="application/json")
        await db.run(save_tier, guild.id, fk, tier, sorted(set(picked)))
        await db.run(db.audit, guild.id, session["user"]["id"], session["user"]["name"], "monopoly",
                     "monopoly_tick", {"faction": fk, "tier": tier, "done": len(set(picked))})
        await refresh_message(guild, fk)
        return web.json_response(await db.run(summary, guild.id, fk))

    app.router.add_get("/api/guild/{gid}/monopoly", get_all)
    app.router.add_get("/api/guild/{gid}/monopoly/{faction}", get_one)
    app.router.add_post("/api/guild/{gid}/monopoly/{faction}", post_one)


from dashboard import server as _server  # noqa: E402
_server.register_routes(_register_routes)


async def setup(bot):
    await bot.add_cog(Monopoly(bot))
