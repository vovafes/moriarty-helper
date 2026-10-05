"""voice_rewards -- split out of main.py."""

from datetime import datetime

import discord
from discord import app_commands, ui
from discord.ext import tasks
from legacy.app import bot, tree
from legacy.helpers import (
    _footer,
    add_points,
    is_admin,
)
from legacy.state import (
    save_data,
    voice_reward_settings,
)


def _member_is_muted(member: discord.Member) -> bool:
    """True если участник в муте (сам или сервером) или заглушён."""
    v = member.voice
    if v is None:
        return True
    return v.self_mute or v.mute or v.self_deaf or v.deaf


@tasks.loop(minutes=1)
async def voice_reward_loop():
    for guild in bot.guilds:
        settings = voice_reward_settings.get(guild.id)
        if not settings:
            continue
        categories   = settings.get("categories", [])
        excluded     = set(settings.get("excluded_channels", []))
        amount       = settings.get("amount", 10)
        amount_game  = settings.get("amount_game", amount)
        game_name    = settings.get("game_name", "GTA5RP")

        if not categories or amount <= 0:
            continue

        for cat_id in categories:
            category = guild.get_channel(cat_id)
            if not category or not isinstance(category, discord.CategoryChannel):
                continue

            for vc in category.voice_channels:
                if vc.id in excluded:
                    continue

                members = [m for m in vc.members if not m.bot]
                if len(members) < 2:
                    continue

                if any(_member_is_muted(m) for m in members):
                    continue

                for m in members:
                    if _member_playing_game(m, game_name):
                        add_points(guild.id, m.id, amount_game)
                    elif amount > 0:
                        add_points(guild.id, m.id, amount)

        save_data()


def _get_voice_settings(guild_id: int) -> dict:
    if guild_id not in voice_reward_settings:
        voice_reward_settings[guild_id] = {
            "categories": [],
            "excluded_channels": [],
            "amount": 10,
            "amount_game": 15,
            "game_name": "RAGE Multiplayer",
        }
    s = voice_reward_settings[guild_id]
    s.setdefault("amount_game", s.get("amount", 10) + 5)
    s.setdefault("game_name", "GTA5RP")
    return s


def _member_playing_game(member: discord.Member, game_name: str) -> bool:
    name_lower = game_name.lower()
    for act in member.activities:
        if isinstance(act, discord.Game) and name_lower in act.name.lower():
            return True
        if isinstance(act, discord.Activity) and name_lower in act.name.lower():
            return True
    return False


@tree.command(name="войс_категория", description="Добавить категорию для начисления валюты за голосовые каналы")
@app_commands.describe(категория="Категория с голосовыми каналами")
async def slash_voice_add_category(interaction: discord.Interaction, категория: discord.CategoryChannel):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    s = _get_voice_settings(interaction.guild_id)
    if категория.id in s["categories"]:
        return await interaction.response.send_message(
            f"❌ Категория **{категория.name}** уже добавлена.", ephemeral=True
        )
    s["categories"].append(категория.id)
    save_data()
    await interaction.response.send_message(
        f"✅ Категория **{категория.name}** добавлена. Голосовые каналы в ней начнут приносить 💎.", ephemeral=True
    )


@tree.command(name="войс_убрать_категорию", description="Убрать категорию из начисления валюты")
@app_commands.describe(категория="Категория для удаления")
async def slash_voice_remove_category(interaction: discord.Interaction, категория: discord.CategoryChannel):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    s = _get_voice_settings(interaction.guild_id)
    if категория.id not in s["categories"]:
        return await interaction.response.send_message(
            f"❌ Категория **{категория.name}** не найдена в списке.", ephemeral=True
        )
    s["categories"].remove(категория.id)
    save_data()
    await interaction.response.send_message(
        f"✅ Категория **{категория.name}** убрана.", ephemeral=True
    )


@tree.command(name="войс_исключить", description="Исключить конкретный голосовой канал из начисления")
@app_commands.describe(канал="Голосовой канал для исключения")
async def slash_voice_exclude(interaction: discord.Interaction, канал: discord.VoiceChannel):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    s = _get_voice_settings(interaction.guild_id)
    if канал.id in s["excluded_channels"]:
        return await interaction.response.send_message(
            f"❌ Канал {канал.mention} уже исключён.", ephemeral=True
        )
    s["excluded_channels"].append(канал.id)
    save_data()
    await interaction.response.send_message(
        f"✅ Канал {канал.mention} исключён из начисления.", ephemeral=True
    )


@tree.command(name="войс_включить", description="Вернуть исключённый голосовой канал в начисление")
@app_commands.describe(канал="Голосовой канал для возврата")
async def slash_voice_include(interaction: discord.Interaction, канал: discord.VoiceChannel):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    s = _get_voice_settings(interaction.guild_id)
    if канал.id not in s["excluded_channels"]:
        return await interaction.response.send_message(
            f"❌ Канал {канал.mention} не был исключён.", ephemeral=True
        )
    s["excluded_channels"].remove(канал.id)
    save_data()
    await interaction.response.send_message(
        f"✅ Канал {канал.mention} возвращён в начисление.", ephemeral=True
    )


@tree.command(name="войс_сумма", description="Сколько 💎 начислять каждую минуту за активность в войсе")
@app_commands.describe(сумма="Количество баллов в минуту (по умолчанию 10)")
async def slash_voice_amount(interaction: discord.Interaction, сумма: int):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    if сумма < 1:
        return await interaction.response.send_message("❌ Сумма должна быть больше 0.", ephemeral=True)
    s = _get_voice_settings(interaction.guild_id)
    s["amount"] = сумма
    save_data()
    await interaction.response.send_message(
        f"✅ Начисление: **{сумма}** 💎 в минуту за активность в войсе.", ephemeral=True
    )


@tree.command(name="войс_настройки", description="Показать настройки начисления за голосовые каналы")
async def slash_voice_settings(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    s = voice_reward_settings.get(interaction.guild_id, {})

    cats = s.get("categories", [])
    excl = s.get("excluded_channels", [])
    amt  = s.get("amount", 10)

    cats_text = (
        "\n".join(
            f"• {interaction.guild.get_channel(c).name if interaction.guild.get_channel(c) else f'[{c}]'}"
            for c in cats
        ) or "—"
    )
    excl_text = (
        "\n".join(
            f"• <#{c}>" for c in excl
        ) or "—"
    )

    embed = discord.Embed(title="🎙 Начисление за голосовые каналы", color=0x5865F2, timestamp=datetime.now())
    embed.add_field(name="💎 Баллов в минуту", value=str(amt), inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.add_field(name="📂 Категории", value=cats_text, inline=False)
    embed.add_field(name="🚫 Исключённые каналы", value=excl_text, inline=False)
    embed.add_field(
        name="📋 Правила",
        value=(
            "• 1 человек → ❌ нет начисления\n"
            "• 2+ все без мута → ✅ все получают\n"
            "• Хотя бы 1 в муте → ❌ никто не получает"
        ),
        inline=False,
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
    await interaction.response.send_message(embed=embed, ephemeral=True)


def _build_activity_embed(guild: discord.Guild) -> discord.Embed:
    s = _get_voice_settings(guild.id)
    cats = s.get("categories", [])
    excl = s.get("excluded_channels", [])

    cats_text = (
        "\n".join(
            f"• {guild.get_channel(c).name if guild.get_channel(c) else f'[{c}]'}"
            for c in cats
        ) or "—"
    )
    excl_text  = "\n".join(f"• <#{c}>" for c in excl) or "—"

    embed = discord.Embed(
        title="🎙 Настройки активности",
        color=0x5865F2,
        timestamp=datetime.now(),
    )
    embed.add_field(name="💎 Войс без игры", value=f"**{s.get('amount', 10)}** /мин", inline=True)
    embed.add_field(name="💎 Войс + игра", value=f"**{s.get('amount_game', 15)}** /мин", inline=True)
    embed.add_field(name="🕹 Название игры", value=s.get("game_name", "RAGE Multiplayer"), inline=True)
    embed.add_field(name="📂 Категории войса", value=cats_text, inline=False)
    embed.add_field(name="🚫 Исключённые каналы", value=excl_text, inline=False)
    embed.set_footer(text="MORIARTY", icon_url=_footer(guild.id))
    return embed


class ActivityRatesModal(ui.Modal, title="💎 Настройки активности"):
    amount = ui.TextInput(
        label="Алмазы/мин (войс без игры)",
        placeholder="10",
        required=True,
        max_length=6,
    )
    amount_game = ui.TextInput(
        label="Алмазы/мин (войс + игра)",
        placeholder="15",
        required=True,
        max_length=6,
    )
    game_name = ui.TextInput(
        label="Название игры (для детекции)",
        placeholder="RAGE Multiplayer",
        required=True,
        max_length=64,
    )

    def __init__(self, orig_msg: discord.Message):
        super().__init__()
        self._orig = orig_msg

    async def on_submit(self, interaction: discord.Interaction):
        s = _get_voice_settings(interaction.guild_id)
        try:
            s["amount"] = max(0, int(str(self.amount).strip()))
        except ValueError:
            pass
        try:
            s["amount_game"] = max(1, int(str(self.amount_game).strip()))
        except ValueError:
            pass
        s["game_name"] = str(self.game_name).strip() or "GTA5RP"
        save_data()
        await interaction.response.edit_message(embed=_build_activity_embed(interaction.guild), view=ActivityView(interaction.guild))


class ActivityCategoryAddSelect(ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="➕ Добавить категорию войса",
            channel_types=[discord.ChannelType.category],
            min_values=1,
            max_values=1,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        s = _get_voice_settings(interaction.guild_id)
        cat_id = self.values[0].id
        if cat_id not in s["categories"]:
            s["categories"].append(cat_id)
            save_data()
        await interaction.response.edit_message(embed=_build_activity_embed(interaction.guild), view=ActivityView(interaction.guild))


class ActivityCategoryRemoveSelect(ui.Select):
    def __init__(self, guild: discord.Guild):
        s = _get_voice_settings(guild.id)
        options = []
        for cat_id in s.get("categories", []):
            ch = guild.get_channel(cat_id)
            name = ch.name if ch else str(cat_id)
            options.append(discord.SelectOption(label=name, value=str(cat_id), emoji="➖"))
        if not options:
            options = [discord.SelectOption(label="(нет категорий)", value="none", emoji="❌")]
        super().__init__(placeholder="➖ Убрать категорию войса", options=options, min_values=1, max_values=1, row=3)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            return await interaction.response.defer()
        s = _get_voice_settings(interaction.guild_id)
        cat_id = int(self.values[0])
        if cat_id in s["categories"]:
            s["categories"].remove(cat_id)
            save_data()
        await interaction.response.edit_message(embed=_build_activity_embed(interaction.guild), view=ActivityView(interaction.guild))


class ActivityView(ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=300)
        self.add_item(ActivityCategoryAddSelect())
        self.add_item(ActivityCategoryRemoveSelect(guild))

    @ui.button(label="✏️ Ставки и игра", style=discord.ButtonStyle.primary, row=0)
    async def btn_rates(self, interaction: discord.Interaction, button: ui.Button):
        s = _get_voice_settings(interaction.guild_id)
        modal = ActivityRatesModal(None)
        modal.amount.default      = str(s.get("amount", 10))
        modal.amount_game.default = str(s.get("amount_game", 15))
        modal.game_name.default   = s.get("game_name", "RAGE Multiplayer")
        await interaction.response.send_modal(modal)


@tree.command(name="активность", description="Настройки начисления алмазов за голосовую активность")
async def slash_activity_settings(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    embed = _build_activity_embed(interaction.guild)
    view  = ActivityView(interaction.guild)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
