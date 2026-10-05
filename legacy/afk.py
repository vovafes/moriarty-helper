"""afk -- split out of main.py."""

from datetime import datetime, timedelta

import discord
from discord import app_commands, ui
from discord.ext import tasks
from legacy.app import bot, now_msk, tree
from legacy.helpers import (
    _footer,
    build_afk_embed,
    build_inactive_embed,
    declension,
    is_admin,
    is_admin_ctx,
)
from legacy.state import (
    afk_list,
    afk_panels,
    inactive_list,
    inactive_panels,
    save_data,
)


async def refresh_afk_message(guild: discord.Guild):
    panel = afk_panels.get(guild.id)
    if not panel:
        return
    try:
        channel = guild.get_channel(panel["channel_id"])
        msg     = await channel.fetch_message(panel["message_id"])
        await msg.edit(embed=build_afk_embed(guild.id))
    except Exception:
        pass


async def refresh_inactive_message(guild: discord.Guild):
    panel = inactive_panels.get(guild.id)
    if not panel:
        return
    try:
        channel = guild.get_channel(panel["channel_id"])
        msg     = await channel.fetch_message(panel["message_id"])
        await msg.edit(embed=build_inactive_embed(guild.id))
    except Exception:
        pass


class AfkModal(ui.Modal, title="🕐 Уход в АФК"):
    reason      = ui.TextInput(label="Причина", placeholder="На работе / Учёба / Дела...", required=True)
    return_time = ui.TextInput(label="Вернусь в (например 18:30)", placeholder="18:30", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        user_id  = interaction.user.id

        raw = str(self.return_time).strip()
        import re as _re
        m = _re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", raw)
        if not m:
            return await interaction.response.send_message(
                "⚠️ Неверный формат времени. Используй формат **ЧЧ:ММ**, например `18:30`",
                ephemeral=True,
            )
        raw = f"{int(m.group(1)):02d}:{m.group(2)}"

        if guild_id not in afk_list:
            afk_list[guild_id] = {}

        afk_list[guild_id][user_id] = {
            "reason":      str(self.reason),
            "return_time": raw,
            "since":       now_msk(),
        }
        save_data()

        await refresh_afk_message(interaction.guild)

        embed = discord.Embed(
            description=(
                f"🕐 Вы добавлены в АФК-список\n"
                f"**Причина:** {self.reason}\n"
                f"**Вернусь в:** `{raw}`"
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
        await interaction.response.send_message(embed=embed, ephemeral=True)


class AfkView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Отошел АФК", style=discord.ButtonStyle.secondary, emoji="🕐", custom_id="afk_away")
    async def afk_away(self, interaction: discord.Interaction, button: ui.Button):
        guild_id = interaction.guild_id
        if guild_id in afk_list and interaction.user.id in afk_list[guild_id]:
            return await interaction.response.send_message("⚠️ Вы уже в АФК-списке!", ephemeral=True)
        await interaction.response.send_modal(AfkModal())

    @ui.button(label="Вернулся из АФК", style=discord.ButtonStyle.success, emoji="✅", custom_id="afk_back")
    async def afk_back(self, interaction: discord.Interaction, button: ui.Button):
        guild_id = interaction.guild_id
        user_id  = interaction.user.id
        if guild_id not in afk_list or user_id not in afk_list[guild_id]:
            return await interaction.response.send_message("⚠️ Вас нет в АФК-списке!", ephemeral=True)

        del afk_list[guild_id][user_id]
        save_data()
        await refresh_afk_message(interaction.guild)
        await interaction.response.send_message("✅ Вы убраны из АФК-списка. С возвращением!", ephemeral=True)


class InactiveModal(ui.Modal, title="📅 Уход в инактив"):
    reason      = ui.TextInput(label="Причина", placeholder="Отпуск / Работа / Дела...", required=True)
    return_date = ui.TextInput(label="Вернусь (например 25.04.2026)", placeholder="25.04.2026", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        user_id  = interaction.user.id

        raw = str(self.return_date).strip()
        import re as _re
        m = _re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", raw)
        if not m:
            return await interaction.response.send_message(
                "⚠️ Неверный формат даты. Используй формат **ДД.ММ.ГГГГ**, например `25.04.2026`",
                ephemeral=True,
            )
        day, mon, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= mon <= 12 and 1 <= day <= 31 and year >= 2020):
            return await interaction.response.send_message(
                "⚠️ Некорректная дата. Проверь, что день и месяц указаны правильно.",
                ephemeral=True,
            )

        if guild_id not in inactive_list:
            inactive_list[guild_id] = {}

        inactive_list[guild_id][user_id] = {
            "reason":      str(self.reason),
            "return_date": raw,
            "since":       now_msk(),
        }
        save_data()

        await refresh_inactive_message(interaction.guild)

        embed = discord.Embed(
            description=(
                f"📅 Вы добавлены в список инактива\n"
                f"**Причина:** {self.reason}\n"
                f"**Вернусь:** `{raw}`"
            ),
            color=discord.Color.orange(),
        )
        embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
        await interaction.response.send_message(embed=embed, ephemeral=True)


class InactiveView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Ухожу в инактив", style=discord.ButtonStyle.secondary, emoji="📅", custom_id="inactive_away")
    async def inactive_away(self, interaction: discord.Interaction, button: ui.Button):
        guild_id = interaction.guild_id
        if guild_id in inactive_list and interaction.user.id in inactive_list[guild_id]:
            return await interaction.response.send_message("⚠️ Вы уже в списке инактива!", ephemeral=True)
        await interaction.response.send_modal(InactiveModal())

    @ui.button(label="Вернулся из инактива", style=discord.ButtonStyle.success, emoji="✅", custom_id="inactive_back")
    async def inactive_back(self, interaction: discord.Interaction, button: ui.Button):
        guild_id = interaction.guild_id
        user_id  = interaction.user.id
        if guild_id not in inactive_list or user_id not in inactive_list[guild_id]:
            return await interaction.response.send_message("⚠️ Вас нет в списке инактива!", ephemeral=True)

        del inactive_list[guild_id][user_id]
        save_data()
        await refresh_inactive_message(interaction.guild)
        await interaction.response.send_message("✅ Вы убраны из инактива. С возвращением!", ephemeral=True)


@bot.command(name="афк")
async def create_afk(ctx):
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    """!афк — создать панель АФК в этом канале"""
    guild_id = ctx.guild.id
    if guild_id not in afk_list:
        afk_list[guild_id] = {}

    view  = AfkView()
    embed = build_afk_embed(guild_id)
    msg   = await ctx.send(embed=embed, view=view)
    afk_panels[guild_id] = {"message_id": msg.id, "channel_id": ctx.channel.id}
    save_data()
    await ctx.message.delete()


@bot.command(name="инактив")
async def create_inactive(ctx):
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    """!инактив — создать панель инактива в этом канале"""
    guild_id = ctx.guild.id
    if guild_id not in inactive_list:
        inactive_list[guild_id] = {}

    view  = InactiveView()
    embed = build_inactive_embed(guild_id)
    msg   = await ctx.send(embed=embed, view=view)
    inactive_panels[guild_id] = {"message_id": msg.id, "channel_id": ctx.channel.id}
    save_data()
    await ctx.message.delete()


@tree.command(name="афк_снять", description="Убрать пользователя из АФК-списка (админ)")
@app_commands.describe(пользователь="Кого убрать из списка АФК")
async def slash_afk_remove(interaction: discord.Interaction, пользователь: discord.Member):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    guild_id = interaction.guild_id
    if guild_id not in afk_list or пользователь.id not in afk_list[guild_id]:
        return await interaction.response.send_message(f"⚠️ {пользователь.mention} не в АФК-списке.", ephemeral=True)
    del afk_list[guild_id][пользователь.id]
    save_data()
    await refresh_afk_message(interaction.guild)
    await interaction.response.send_message(f"✅ {пользователь.mention} убран(а) из АФК-списка.", ephemeral=True)


@tree.command(name="афк_очистить", description="Очистить весь АФК-список (админ)")
async def slash_afk_clear(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    guild_id = interaction.guild_id
    count = len(afk_list.get(guild_id, {}))
    afk_list[guild_id] = {}
    save_data()
    await refresh_afk_message(interaction.guild)
    await interaction.response.send_message(f"✅ АФК-список очищен ({count} {declension(count)} убрано).", ephemeral=True)


@tree.command(name="инактив_снять", description="Убрать пользователя из списка инактива (админ)")
@app_commands.describe(пользователь="Кого убрать из списка инактива")
async def slash_inactive_remove(interaction: discord.Interaction, пользователь: discord.Member):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    guild_id = interaction.guild_id
    if guild_id not in inactive_list or пользователь.id not in inactive_list[guild_id]:
        return await interaction.response.send_message(f"⚠️ {пользователь.mention} не в списке инактива.", ephemeral=True)
    del inactive_list[guild_id][пользователь.id]
    save_data()
    await refresh_inactive_message(interaction.guild)
    await interaction.response.send_message(f"✅ {пользователь.mention} убран(а) из инактива.", ephemeral=True)


@tree.command(name="инактив_очистить", description="Очистить весь список инактива (админ)")
async def slash_inactive_clear(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    guild_id = interaction.guild_id
    count = len(inactive_list.get(guild_id, {}))
    inactive_list[guild_id] = {}
    save_data()
    await refresh_inactive_message(interaction.guild)
    await interaction.response.send_message(f"✅ Список инактива очищен ({count} {declension(count)} убрано).", ephemeral=True)


@tasks.loop(minutes=1)
async def afk_expire_loop():
    """Каждую минуту проверяет АФК-список и снимает тех, чьё время возвращения наступило (МСК)."""
    import re as _re
    now = now_msk()
    for guild_id, users in list(afk_list.items()):
        expired = []
        for uid, entry in list(users.items()):
            try:
                m = _re.match(r"^(\d{2}):(\d{2})$", entry.get("return_time", "").strip())
                if not m:
                    continue
                since = entry.get("since")
                if not isinstance(since, datetime):
                    continue
                target = since.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
                if target <= since:
                    target += timedelta(days=1)
                if now >= target:
                    expired.append(uid)
            except Exception as e:
                print(f"WARNING: afk_expire_loop bad entry guild={guild_id} user={uid}: {e}")
        if not expired:
            continue
        for uid in expired:
            afk_list[guild_id].pop(uid, None)
        save_data()
        guild = bot.get_guild(guild_id)
        if guild:
            await refresh_afk_message(guild)


@afk_expire_loop.error
async def afk_expire_loop_error(error: Exception):
    print(f"WARNING: afk_expire_loop crashed, restarting: {error}")
    if not afk_expire_loop.is_running():
        afk_expire_loop.restart()


@tasks.loop(hours=1)
async def inactive_expire_loop():
    """Каждый час проверяет список инактива и удаляет тех, чья дата возвращения наступила (МСК)."""
    import re as _re
    from datetime import date as _date
    today = now_msk().date()
    for guild_id, users in list(inactive_list.items()):
        expired = []
        for uid, entry in list(users.items()):
            try:
                m = _re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", entry.get("return_date", "").strip())
                if not m:
                    continue
                rd = _date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                if today >= rd:
                    expired.append(uid)
            except Exception as e:
                print(f"WARNING: inactive_expire_loop bad entry guild={guild_id} user={uid}: {e}")
        if not expired:
            continue
        for uid in expired:
            inactive_list[guild_id].pop(uid, None)
        save_data()
        guild = bot.get_guild(guild_id)
        if guild:
            await refresh_inactive_message(guild)


@inactive_expire_loop.error
async def inactive_expire_loop_error(error: Exception):
    print(f"WARNING: inactive_expire_loop crashed, restarting: {error}")
    if not inactive_expire_loop.is_running():
        inactive_expire_loop.restart()
