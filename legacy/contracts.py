"""contracts -- split out of main.py."""

from datetime import datetime

import discord
from discord import app_commands, ui
from legacy.app import bot
from legacy.helpers import (
    _footer,
    is_admin,
    is_admin_ctx,
)
from legacy.state import (
    active_contracts,
    contract_roles,
    contract_settings,
    save_data,
)


DEFAULT_CONTRACT_TEXT = (
    "**Контракт открыт!**\n"
    "Нажми кнопку ниже, чтобы взять контракт и указать время."
)


def build_contract_panel_embed(guild_id: int) -> discord.Embed:
    cfg = contract_settings.get(guild_id, {})
    text = cfg.get("text", DEFAULT_CONTRACT_TEXT)
    image_url = cfg.get("image_url")
    embed = discord.Embed(
        title="📄 Контракт",
        description=text,
        color=discord.Color.blue(),
        timestamp=datetime.now(),
    )
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
    return embed


def build_active_contract_embed(data: dict) -> discord.Embed:
    guild_id     = data.get("guild_id", 0)
    participants = data.get("participants", [])
    creator_id   = data.get("creator_id")
    duration     = data.get("duration", "—")
    start        = data.get("start", "—")

    if participants:
        parts_text = "\n".join(f"• <@{uid}>" for uid in participants)
    else:
        parts_text = "*Пока никто не принял участие*"

    embed = discord.Embed(
        title="📄 Активный контракт",
        color=discord.Color.gold(),
        timestamp=datetime.now(),
    )
    embed.add_field(name="⏱ Длительность", value=f"`{duration}`", inline=True)
    embed.add_field(name="🕐 Начало",       value=f"`{start}`",    inline=True)
    embed.add_field(name="👤 Создал",       value=f"<@{creator_id}>", inline=True)
    embed.add_field(name=f"👥 Участники ({len(participants)})", value=parts_text, inline=False)
    embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
    return embed


class ContractModal(ui.Modal, title="📄 Взять контракт"):
    duration = ui.TextInput(
        label="Длительность",
        placeholder="2:20",
        required=True,
        max_length=20,
    )
    start = ui.TextInput(
        label="Начало",
        placeholder="10:20 / сейчас / через 15 минут",
        required=True,
        max_length=50,
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        role_id  = contract_roles.get(guild_id)
        content  = f"<@&{role_id}>" if role_id else None

        contract_data = {
            "guild_id":    guild_id,
            "creator_id":  interaction.user.id,
            "duration":    str(self.duration),
            "start":       str(self.start),
            "channel_id":  interaction.channel_id,
            "participants": [],
        }

        embed = build_active_contract_embed(contract_data)
        view  = ActiveContractView(0)
        msg   = await interaction.channel.send(content=content, embed=embed, view=view)

        contract_data["message_id"] = msg.id
        active_contracts[msg.id]    = contract_data
        save_data()

        await interaction.response.send_message("✅ Контракт создан!", ephemeral=True)


class ContractPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Взять контракт", style=discord.ButtonStyle.primary, emoji="✅", custom_id="contract_take")
    async def take(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(ContractModal())


class ActiveContractView(ui.View):
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id

    @ui.button(label="Принять участие", style=discord.ButtonStyle.success, emoji="➕",
               custom_id="contract_join")
    async def join(self, interaction: discord.Interaction, button: ui.Button):
        # Ищем контракт по ID сообщения
        msg_id = interaction.message.id
        data   = active_contracts.get(msg_id)
        if not data:
            return await interaction.response.send_message("❌ Контракт недоступен!", ephemeral=True)
        if interaction.user.id in data["participants"]:
            return await interaction.response.send_message("⚠️ Вы уже участвуете!", ephemeral=True)
        data["participants"].append(interaction.user.id)
        save_data()
        await interaction.response.edit_message(embed=build_active_contract_embed(data))

    @ui.button(label="Отменить участие", style=discord.ButtonStyle.danger, emoji="➖",
               custom_id="contract_leave")
    async def leave(self, interaction: discord.Interaction, button: ui.Button):
        msg_id = interaction.message.id
        data   = active_contracts.get(msg_id)
        if not data:
            return await interaction.response.send_message("❌ Контракт недоступен!", ephemeral=True)
        if interaction.user.id not in data["participants"]:
            return await interaction.response.send_message("⚠️ Вас нет в участниках!", ephemeral=True)
        data["participants"].remove(interaction.user.id)
        save_data()
        await interaction.response.edit_message(embed=build_active_contract_embed(data))

    @ui.button(label="Контракт взят", style=discord.ButtonStyle.secondary, emoji="🔒",
               custom_id="contract_close")
    async def close(self, interaction: discord.Interaction, button: ui.Button):
        msg_id = interaction.message.id
        data   = active_contracts.get(msg_id)
        if not data:
            return await interaction.response.send_message("❌ Контракт недоступен!", ephemeral=True)

        # Закрыть может только создатель или администратор
        is_creator = interaction.user.id == data["creator_id"]
        if not is_creator and not is_admin(interaction):
            return await interaction.response.send_message(
                "❌ Закрыть контракт может только его создатель или администратор.", ephemeral=True
            )

        active_contracts.pop(msg_id, None)
        save_data()
        await interaction.message.delete()
        await interaction.response.send_message("✅ Контракт закрыт.", ephemeral=True)


@bot.command(name="контракт")
async def contract_panel_cmd(ctx):
    """!контракт — создать/обновить панель контрактов в этом канале"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()

    guild_id = ctx.guild.id
    if guild_id not in contract_settings:
        contract_settings[guild_id] = {}

    contract_settings[guild_id]["channel_id"] = ctx.channel.id

    embed = build_contract_panel_embed(guild_id)
    view  = ContractPanelView()
    msg   = await ctx.send(embed=embed, view=view)
    contract_settings[guild_id]["message_id"] = msg.id
    save_data()

    try:
        await ctx.message.delete()
    except Exception:
        pass


@bot.tree.command(name="контракт_текст", description="Изменить текст панели контрактов")
@app_commands.describe(текст="Новый текст для панели контрактов")
async def contract_text_cmd(interaction: discord.Interaction, текст: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    guild_id = interaction.guild_id
    if guild_id not in contract_settings:
        contract_settings[guild_id] = {}
    contract_settings[guild_id]["text"] = текст
    save_data()
    await _refresh_contract_panel(interaction.guild)
    await interaction.response.send_message("✅ Текст контракта обновлён!", ephemeral=True)


@bot.tree.command(name="контракт_фото", description="Изменить фото панели контрактов")
@app_commands.describe(url="Прямая ссылка на изображение")
async def contract_photo_cmd(interaction: discord.Interaction, url: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    guild_id = interaction.guild_id
    if guild_id not in contract_settings:
        contract_settings[guild_id] = {}
    contract_settings[guild_id]["image_url"] = url
    save_data()
    await _refresh_contract_panel(interaction.guild)
    await interaction.response.send_message("✅ Фото контракта обновлено!", ephemeral=True)


@bot.tree.command(name="контракт_роль", description="Роль, которая тегается при создании контракта")
@app_commands.describe(роль="Роль для упоминания")
async def contract_role_cmd(interaction: discord.Interaction, роль: discord.Role):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    contract_roles[interaction.guild_id] = роль.id
    save_data()
    await interaction.response.send_message(f"✅ Роль контракта: {роль.mention}", ephemeral=True)


async def _refresh_contract_panel(guild: discord.Guild):
    """Обновляет embed панели контрактов при изменении текста/фото."""
    cfg = contract_settings.get(guild.id)
    if not cfg or not cfg.get("message_id"):
        return
    try:
        channel = guild.get_channel(cfg["channel_id"])
        if not channel:
            return
        msg = await channel.fetch_message(cfg["message_id"])
        await msg.edit(embed=build_contract_panel_embed(guild.id))
    except Exception:
        pass
