"""roulette -- split out of main.py."""

import asyncio
import json
import os
import random
from datetime import datetime

import discord
from discord import app_commands
from legacy.app import bot, tree
from legacy.helpers import (
    _fmt,
    _footer,
    add_chips,
    get_chips,
    is_admin,
)
from legacy.state import (
    ROULETTE_FILE,
    casino_role_luck,
    save_data,
)


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
