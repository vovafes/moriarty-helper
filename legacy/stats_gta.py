"""stats_gta -- split out of main.py."""

import re
from datetime import datetime

import aiohttp
import discord
from discord.ext import tasks
from legacy.app import bot


RAGEMP_API = "https://cdn.rage.mp/master/"


# { guild_id: { "channel_id": int, "message_id": int } }
stats_panels: dict = {}


SERVER_ORDER = [
    "Downtown", "Strawberry", "VineWood", "Blackberry", "Insquad",
    "Sunrise", "Rainbow", "Richman", "Eclipse", "La Mesa", "Burton",
    "Rockford", "Alta", "Del Perro", "Davis", "Harmony", "Redwood",
    "Hawick", "Grapeseed", "Murrieta", "Vespucci", "Milton", "La Puerta",
]


_SERVER_ORDER_LOWER = {name.lower(): i for i, name in enumerate(SERVER_ORDER)}


async def fetch_gta5rp_stats() -> tuple[list[tuple[str, int]], int] | None:
    """Возвращает [(название, онлайн), ...] в порядке SERVER_ORDER + общий онлайн."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(RAGEMP_API, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
    except Exception:
        return None

    servers = []
    for addr, info in data.items():
        name_raw = info.get("name", "")
        if "gta5rp.com" not in name_raw.lower() and "gta5rp.com" not in addr.lower():
            continue
        m = re.search(r"GTA5RP\.COM \| (.+?) \|", name_raw, re.IGNORECASE)
        short_name = m.group(1).strip() if m else name_raw
        players = info.get("players", 0)
        servers.append((short_name, players))

    if not servers:
        return None

    servers.sort(key=lambda x: _SERVER_ORDER_LOWER.get(x[0].lower(), 9999))
    total = sum(p for _, p in servers)
    return servers, total


def build_stats_embed(servers: list[tuple[str, int]], total: int) -> discord.Embed:
    embed = discord.Embed(
        title="Статистика серверов GTA5RP",
        description="**Актуальная статистика (Обновляется каждые 30 секунд)**\n",
        color=0xf1c40f,
        timestamp=datetime.now(),
    )
    lines = "\n".join(f"**{name}** — {players} игр." for name, players in servers)
    embed.description += lines
    embed.add_field(name="🌐 Общий онлайн", value=f"**{total:,}** игроков".replace(",", " "), inline=False)
    embed.set_footer(text="rage.mp • GTA5RP.COM")
    return embed


@bot.tree.command(name="статистика", description="Показать онлайн серверов GTA5RP (авто-обновление)")
async def stats_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    result = await fetch_gta5rp_stats()
    if not result:
        return await interaction.followup.send("❌ Не удалось получить данные.", ephemeral=True)

    servers, total = result
    embed = build_stats_embed(servers, total)
    msg = await interaction.channel.send(embed=embed)

    stats_panels[interaction.guild_id] = {
        "channel_id": interaction.channel_id,
        "message_id": msg.id,
    }

    await interaction.followup.send("✅ Панель создана, будет обновляться каждые 30 сек.", ephemeral=True)


@bot.command(name="статистика")
async def stats_prefix(ctx):
    result = await fetch_gta5rp_stats()
    if not result:
        return await ctx.send("❌ Не удалось получить данные.")

    servers, total = result
    embed = build_stats_embed(servers, total)
    msg = await ctx.send(embed=embed)

    stats_panels[ctx.guild.id] = {
        "channel_id": ctx.channel.id,
        "message_id": msg.id,
    }


@tasks.loop(seconds=30)
async def update_stats():
    if not stats_panels:
        return
    result = await fetch_gta5rp_stats()
    if not result:
        return
    servers, total = result
    embed = build_stats_embed(servers, total)

    for guild_id, panel in list(stats_panels.items()):
        ch = bot.get_channel(panel["channel_id"])
        if not ch:
            continue
        try:
            msg = await ch.fetch_message(panel["message_id"])
            await msg.edit(embed=embed)
        except Exception:
            stats_panels.pop(guild_id, None)
