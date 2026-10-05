"""private_vc -- split out of main.py."""


import discord
from discord import app_commands, ui
from legacy.app import tree
from legacy.helpers import (
    _footer,
    is_admin,
)
from legacy.state import (
    private_vc_settings,
    private_vcs,
    save_data,
)


def build_private_vc_embed(owner: discord.Member, vc: discord.VoiceChannel) -> discord.Embed:
    limit = str(vc.user_limit) if vc.user_limit else "∞"
    embed = discord.Embed(
        title="🔒 Управление приватной комнатой",
        description=(
            "`+` • Добавить слот             `−` • Убрать слот\n"
            "👥 • Изменить слоты            🔄 • Передать канал\n"
            "🔓 • Открыть канал             🔒 • Закрыть канал\n"
            "👤➕ • Добавить пользователя  👤➖ • Убрать пользователя\n"
            "🙈 • Скрыть канал               👁 • Показать канал\n"
            "✏️ • Переименовать              🚫 • Заблокировать\n\n"
            "*Кнопки работают только для владельца канала.*"
        ),
        color=discord.Color.dark_red(),
    )
    embed.add_field(name="👑 Владелец", value=owner.mention, inline=True)
    embed.add_field(name="🔊 Канал",    value=vc.mention,    inline=True)
    embed.add_field(name="👥 Слоты",   value=limit,          inline=True)
    embed.set_footer(text="MORIARTY", icon_url=_footer(owner.guild.id))
    return embed


async def resolve_member(guild: discord.Guild, text: str) -> discord.Member | None:
    text = text.strip().lstrip("<@!").rstrip(">")
    try:
        return guild.get_member(int(text)) or await guild.fetch_member(int(text))
    except Exception:
        return discord.utils.find(
            lambda m: m.name.lower() == text.lower() or m.display_name.lower() == text.lower(),
            guild.members,
        )


class PVCRenameModal(ui.Modal, title="✏️ Переименовать канал"):
    name = ui.TextInput(label="Новое название", max_length=100, required=True)
    async def on_submit(self, interaction: discord.Interaction):
        vc, _ = _get_owner_vc(interaction)
        if not vc:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        await vc.edit(name=str(self.name))
        await interaction.response.send_message(f"✅ Канал переименован: **{self.name}**", ephemeral=True)


class PVCSlotsModal(ui.Modal, title="👥 Изменить количество слотов"):
    slots = ui.TextInput(label="Количество слотов (0 = без лимита)", placeholder="10", required=True, max_length=3)
    async def on_submit(self, interaction: discord.Interaction):
        vc, _ = _get_owner_vc(interaction)
        if not vc:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        try:
            n = max(0, min(99, int(str(self.slots))))
        except ValueError:
            return await interaction.response.send_message("❌ Введите число.", ephemeral=True)
        await vc.edit(user_limit=n)
        label = str(n) if n else "∞"
        await interaction.response.send_message(f"✅ Слотов: **{label}**", ephemeral=True)


class PVCUserActionModal(ui.Modal):
    user_input = ui.TextInput(label="Упомяните или введите ID пользователя", required=True)
    def __init__(self, action: str):
        titles = {
            "add": "👤➕ Добавить пользователя",
            "remove": "👤➖ Убрать пользователя",
            "transfer": "🔄 Передать канал",
            "block": "🚫 Заблокировать пользователя",
        }
        super().__init__(title=titles.get(action, "Действие"))
        self.action = action

    async def on_submit(self, interaction: discord.Interaction):
        vc, data = _get_owner_vc(interaction)
        if not vc:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        member = await resolve_member(interaction.guild, str(self.user_input))
        if not member:
            return await interaction.response.send_message("❌ Пользователь не найден.", ephemeral=True)
        if member == interaction.user:
            return await interaction.response.send_message("❌ Нельзя применить к себе.", ephemeral=True)

        if self.action == "add":
            await vc.set_permissions(member, connect=True, view_channel=True)
            await interaction.response.send_message(f"✅ {member.mention} добавлен в канал.", ephemeral=True)

        elif self.action == "remove":
            await vc.set_permissions(member, overwrite=None)
            if member in vc.members:
                await member.move_to(None)
            await interaction.response.send_message(f"✅ {member.mention} убран из канала.", ephemeral=True)

        elif self.action == "transfer":
            private_vcs[vc.id]["owner_id"] = member.id
            await vc.set_permissions(interaction.user, overwrite=None)
            await vc.set_permissions(member, connect=True, manage_channels=True, move_members=True)
            # Обновить панель
            await _refresh_pvc_panel(interaction.guild, vc)
            await interaction.response.send_message(f"✅ Канал передан {member.mention}.", ephemeral=True)

        elif self.action == "block":
            await vc.set_permissions(member, connect=False, view_channel=False)
            if member in vc.members:
                await member.move_to(None)
            await interaction.response.send_message(f"✅ {member.mention} заблокирован.", ephemeral=True)


def _get_owner_vc(interaction: discord.Interaction):
    """Возвращает (VoiceChannel, data) приватного канала владельца."""
    for vc_id, data in private_vcs.items():
        if data["owner_id"] == interaction.user.id and data["guild_id"] == interaction.guild_id:
            vc = interaction.guild.get_channel(vc_id)
            if vc:
                return vc, data
    return None, None


async def _refresh_pvc_panel(guild: discord.Guild, vc: discord.VoiceChannel):
    data = private_vcs.get(vc.id)
    if not data:
        return
    owner = guild.get_member(data["owner_id"])
    if not owner:
        return
    panel_ch = guild.get_channel(data.get("panel_channel_id"))
    if not panel_ch:
        return
    try:
        msg = await panel_ch.fetch_message(data["panel_msg_id"])
        await msg.edit(embed=build_private_vc_embed(owner, vc))
    except Exception:
        pass


class PrivateVCView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(emoji="➕", style=discord.ButtonStyle.secondary, custom_id="pvc_add_slot", row=0)
    async def add_slot(self, interaction: discord.Interaction, button: ui.Button):
        vc, _ = _get_owner_vc(interaction)
        if not vc:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        new_limit = (vc.user_limit or 0) + 1
        await vc.edit(user_limit=new_limit)
        await interaction.response.send_message(f"✅ Слотов: **{new_limit}**", ephemeral=True)

    @ui.button(emoji="➖", style=discord.ButtonStyle.secondary, custom_id="pvc_remove_slot", row=0)
    async def remove_slot(self, interaction: discord.Interaction, button: ui.Button):
        vc, _ = _get_owner_vc(interaction)
        if not vc:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        new_limit = max(0, (vc.user_limit or 1) - 1)
        await vc.edit(user_limit=new_limit)
        label = str(new_limit) if new_limit else "∞"
        await interaction.response.send_message(f"✅ Слотов: **{label}**", ephemeral=True)

    @ui.button(emoji="👥", style=discord.ButtonStyle.secondary, custom_id="pvc_set_slots", row=0)
    async def set_slots(self, interaction: discord.Interaction, button: ui.Button):
        _, data = _get_owner_vc(interaction)
        if not data:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        await interaction.response.send_modal(PVCSlotsModal())

    @ui.button(emoji="🔓", style=discord.ButtonStyle.secondary, custom_id="pvc_open", row=0)
    async def open_channel(self, interaction: discord.Interaction, button: ui.Button):
        vc, _ = _get_owner_vc(interaction)
        if not vc:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        await vc.set_permissions(interaction.guild.default_role, connect=True)
        await interaction.response.send_message("✅ Канал открыт.", ephemeral=True)

    @ui.button(emoji="🔒", style=discord.ButtonStyle.secondary, custom_id="pvc_close", row=0)
    async def close_channel(self, interaction: discord.Interaction, button: ui.Button):
        vc, _ = _get_owner_vc(interaction)
        if not vc:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        await vc.set_permissions(interaction.guild.default_role, connect=False)
        await interaction.response.send_message("✅ Канал закрыт.", ephemeral=True)

    @ui.button(emoji="👤", style=discord.ButtonStyle.secondary, custom_id="pvc_add_user", row=1)
    async def add_user(self, interaction: discord.Interaction, button: ui.Button):
        _, data = _get_owner_vc(interaction)
        if not data:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        await interaction.response.send_modal(PVCUserActionModal("add"))

    @ui.button(emoji="🚷", style=discord.ButtonStyle.secondary, custom_id="pvc_remove_user", row=1)
    async def remove_user(self, interaction: discord.Interaction, button: ui.Button):
        _, data = _get_owner_vc(interaction)
        if not data:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        await interaction.response.send_modal(PVCUserActionModal("remove"))

    @ui.button(emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="pvc_transfer", row=1)
    async def transfer(self, interaction: discord.Interaction, button: ui.Button):
        _, data = _get_owner_vc(interaction)
        if not data:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        await interaction.response.send_modal(PVCUserActionModal("transfer"))

    @ui.button(emoji="🙈", style=discord.ButtonStyle.secondary, custom_id="pvc_hide", row=1)
    async def hide_channel(self, interaction: discord.Interaction, button: ui.Button):
        vc, _ = _get_owner_vc(interaction)
        if not vc:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        await vc.set_permissions(interaction.guild.default_role, view_channel=False)
        await interaction.response.send_message("✅ Канал скрыт.", ephemeral=True)

    @ui.button(emoji="👁", style=discord.ButtonStyle.secondary, custom_id="pvc_show", row=1)
    async def show_channel(self, interaction: discord.Interaction, button: ui.Button):
        vc, _ = _get_owner_vc(interaction)
        if not vc:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        await vc.set_permissions(interaction.guild.default_role, view_channel=True)
        await interaction.response.send_message("✅ Канал показан.", ephemeral=True)

    @ui.button(emoji="✏️", style=discord.ButtonStyle.secondary, custom_id="pvc_rename", row=2)
    async def rename(self, interaction: discord.Interaction, button: ui.Button):
        _, data = _get_owner_vc(interaction)
        if not data:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        await interaction.response.send_modal(PVCRenameModal())

    @ui.button(emoji="🚫", style=discord.ButtonStyle.secondary, custom_id="pvc_block", row=2)
    async def block_user(self, interaction: discord.Interaction, button: ui.Button):
        _, data = _get_owner_vc(interaction)
        if not data:
            return await interaction.response.send_message("❌ У вас нет приватного канала!", ephemeral=True)
        await interaction.response.send_modal(PVCUserActionModal("block"))


@tree.command(name="приват", description="Настроить систему приватных комнат")
@app_commands.describe(
    канал_создания="Голосовой канал — зайди сюда, чтобы получить приватную комнату",
    категория="Категория, где создаются приватные каналы",
    канал_панели="Текстовый канал, куда присылается панель управления",
)
async def slash_private_vc(
    interaction: discord.Interaction,
    канал_создания: discord.VoiceChannel,
    категория: discord.CategoryChannel,
    канал_панели: discord.TextChannel,
):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    private_vc_settings[interaction.guild_id] = {
        "create_channel_id": канал_создания.id,
        "category_id":       категория.id,
        "panel_channel_id":  канал_панели.id,
    }
    save_data()
    await interaction.response.send_message(
        f"✅ Приватные комнаты настроены!\n"
        f"Триггер: {канал_создания.mention}\n"
        f"Категория: **{категория.name}**\n"
        f"Панель: {канал_панели.mention}",
        ephemeral=True,
    )
