"""recruit -- split out of main.py."""

from datetime import datetime

import discord
from discord import app_commands, ui
from legacy.app import tree
from legacy.helpers import (
    _footer,
    get_recruit_stats,
    get_warns,
    is_admin,
    is_ticket_manager,
    remove_warn,
    set_warn,
    warn_payment_label,
)
from legacy.state import (
    recruit_cabinet_panels,
    save_data,
    warn_log_channels,
    warn_log_messages,
    warn_roles,
)


DEFAULT_RECRUIT_CABINET_TEXT = "Кабинет рекрута: статистика по заявкам и выдача варнов."


def is_recruiter(interaction: discord.Interaction) -> bool:
    return is_ticket_manager(interaction)


def build_recruit_cabinet_embed(guild_id: int) -> discord.Embed:
    settings  = recruit_cabinet_panels.get(guild_id, {})
    text      = settings.get("text") or DEFAULT_RECRUIT_CABINET_TEXT
    image_url = settings.get("image_url")

    embed = discord.Embed(
        title="🎖 Кабинет рекрута",
        description=text,
        color=0x2b2d31,
    )
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
    return embed


async def _refresh_recruit_cabinet_panel(guild: discord.Guild):
    settings = recruit_cabinet_panels.get(guild.id)
    if not settings or not settings.get("message_id"):
        return
    try:
        ch  = guild.get_channel(settings["channel_id"])
        msg = await ch.fetch_message(settings["message_id"])
        await msg.edit(embed=build_recruit_cabinet_embed(guild.id), view=RecruitCabinetView())
    except Exception:
        pass


async def _delete_warn_log_message(guild: discord.Guild, user_id: int):
    """Удаляет сообщение о варне из канала логов (варн снят)."""
    ref = warn_log_messages.get(guild.id, {}).pop(user_id, None)
    if ref is None:
        return
    save_data()
    try:
        ch = guild.get_channel(ref["channel_id"])
        if ch:
            msg = await ch.fetch_message(ref["message_id"])
            await msg.delete()
    except Exception:
        pass


class RemoveWarnModal(ui.Modal, title="✅ Снять варн"):
    user_id_input = ui.TextInput(label="ID пользователя", placeholder="123456789012345678", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        try:
            target_id = int(str(self.user_id_input).strip())
        except ValueError:
            return await interaction.response.send_message("❌ Некорректный ID пользователя.", ephemeral=True)

        if not remove_warn(guild.id, target_id):
            return await interaction.response.send_message("❌ У пользователя нет варнов.", ephemeral=True)

        member = guild.get_member(target_id)
        guild_warn_roles = warn_roles.get(guild.id, {})
        roles_to_remove = [guild.get_role(rid) for rid in guild_warn_roles.values() if guild.get_role(rid)]
        if member:
            try:
                await member.remove_roles(*[r for r in roles_to_remove if r], reason="Снятие варна")
            except Exception:
                pass

        await _delete_warn_log_message(guild, target_id)

        embed = discord.Embed(
            title="✅ Warn снят",
            description=f"У {member.mention if member else f'<@{target_id}>'} снят warn",
            color=discord.Color.green(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="Снял", value=interaction.user.mention, inline=False)
        embed.set_footer(text="MORIARTY", icon_url=_footer(guild.id))
        await interaction.response.send_message(embed=embed)

        if member:
            try:
                dm_embed = discord.Embed(
                    title="✅ С вас снят варн",
                    color=discord.Color.green(),
                    timestamp=datetime.now(),
                )
                dm_embed.set_footer(text="MORIARTY", icon_url=_footer(guild.id))
                await member.send(embed=dm_embed)
            except Exception:
                pass


class IssueWarnModal(ui.Modal):
    user_id_input = ui.TextInput(label="ID пользователя", placeholder="123456789012345678", required=True)
    reason_input  = ui.TextInput(label="Причина", style=discord.TextStyle.paragraph, required=True)
    level_input   = ui.TextInput(label="Номер варна (1, 2 или 3)", placeholder="1", required=True, max_length=1)

    def __init__(self, payment_method: str = "any"):
        pay_word = "только деньги" if payment_method == "money" else "баллы/деньги"
        super().__init__(title=f"⚠️ Выдать варн — {pay_word}")
        self.payment_method = payment_method

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild

        try:
            target_id = int(str(self.user_id_input).strip())
        except ValueError:
            return await interaction.response.send_message("❌ Некорректный ID пользователя.", ephemeral=True)

        try:
            level = int(str(self.level_input).strip())
        except ValueError:
            level = 0
        if level not in (1, 2, 3):
            return await interaction.response.send_message("❌ Номер варна должен быть 1, 2 или 3.", ephemeral=True)

        member = guild.get_member(target_id)
        if member is None:
            try:
                member = await guild.fetch_member(target_id)
            except Exception:
                return await interaction.response.send_message("❌ Пользователь не найден на сервере.", ephemeral=True)

        reason = str(self.reason_input)

        await interaction.response.defer(ephemeral=True)

        set_warn(guild.id, member.id, level, reason, interaction.user.id, self.payment_method)

        guild_warn_roles = warn_roles.get(guild.id, {})
        roles_to_remove = [guild.get_role(rid) for rid in guild_warn_roles.values() if guild.get_role(rid)]
        new_role = guild.get_role(guild_warn_roles.get(level))
        try:
            await member.remove_roles(*[r for r in roles_to_remove if r and r != new_role], reason="Обновление варн-роли")
            if new_role:
                await member.add_roles(new_role, reason=f"Warn {level}/3")
        except Exception:
            pass

        removable = warn_payment_label(get_warns(guild.id, member.id))

        log_embed = discord.Embed(
            title="⚠️ Выдан варн",
            color=0x2b2d31,
            timestamp=datetime.now(),
        )
        log_embed.add_field(name="Пользователь", value=member.mention, inline=True)
        log_embed.add_field(name="Варн", value=f"**{level}/3**", inline=True)
        log_embed.add_field(name="Снять можно", value=removable, inline=True)
        log_embed.add_field(name="Причина", value=reason, inline=False)
        log_embed.add_field(name="Выдал", value=interaction.user.mention, inline=False)
        log_embed.set_footer(text="MORIARTY", icon_url=_footer(guild.id))

        log_ch_id = warn_log_channels.get(guild.id)
        log_ch = guild.get_channel(log_ch_id) if log_ch_id else None
        if log_ch:
            try:
                await _delete_warn_log_message(guild, member.id)
                msg = await log_ch.send(embed=log_embed)
                warn_log_messages.setdefault(guild.id, {})[member.id] = {
                    "channel_id": log_ch.id,
                    "message_id": msg.id,
                }
                save_data()
            except Exception:
                pass

        try:
            dm_embed = discord.Embed(
                title="⚠️ Вы получили warn",
                description=f"**Причина:** {reason}\n**Варны:** {level}/3\n**Оплата:** {removable}",
                color=discord.Color.red(),
                timestamp=datetime.now(),
            )
            dm_embed.add_field(name="Модератор", value=interaction.user.mention)
            dm_embed.set_footer(text="MORIARTY", icon_url=_footer(guild.id))
            await member.send(embed=dm_embed)
        except Exception:
            pass

        await interaction.followup.send(f"✅ Варн {level}/3 выдан {member.mention}.", ephemeral=True)


class RecruitCabinetView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Статистика", emoji="📊", style=discord.ButtonStyle.secondary, custom_id="recruit_cabinet_stats", row=0)
    async def btn_stats(self, interaction: discord.Interaction, button: ui.Button):
        stats = get_recruit_stats(interaction.guild_id, interaction.user.id)
        embed = discord.Embed(
            title="📊 Статистика рекрута",
            color=0x2b2d31,
            timestamp=datetime.now(),
        )
        embed.add_field(name="✅ Одобрено заявок", value=str(stats.get("approved", 0)), inline=True)
        embed.add_field(name="❌ Отклонено заявок", value=str(stats.get("rejected", 0)), inline=True)
        embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Варн (баллы/деньги)", emoji="💎", style=discord.ButtonStyle.danger, custom_id="recruit_cabinet_warn_any", row=0)
    async def btn_warn_any(self, interaction: discord.Interaction, button: ui.Button):
        if not is_recruiter(interaction):
            return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
        await interaction.response.send_modal(IssueWarnModal(payment_method="any"))

    @ui.button(label="Варн (только деньги)", emoji="💵", style=discord.ButtonStyle.danger, custom_id="recruit_cabinet_warn_money", row=0)
    async def btn_warn_money(self, interaction: discord.Interaction, button: ui.Button):
        if not is_recruiter(interaction):
            return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
        await interaction.response.send_modal(IssueWarnModal(payment_method="money"))

    @ui.button(label="Снять варн", emoji="✅", style=discord.ButtonStyle.success, custom_id="recruit_cabinet_warn_remove", row=0)
    async def btn_warn_remove(self, interaction: discord.Interaction, button: ui.Button):
        if not is_recruiter(interaction):
            return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
        await interaction.response.send_modal(RemoveWarnModal())


@tree.command(name="кабинет_рекрута", description="Создать панель кабинета рекрута в текущем канале")
async def slash_recruit_cabinet(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)

    gid = interaction.guild_id

    existing = recruit_cabinet_panels.get(gid)
    if existing and existing.get("message_id"):
        try:
            old_ch  = interaction.guild.get_channel(existing["channel_id"])
            old_msg = await old_ch.fetch_message(existing["message_id"])
            await old_msg.delete()
        except Exception:
            pass

    prev  = recruit_cabinet_panels.get(gid, {})
    embed = build_recruit_cabinet_embed(gid)
    msg   = await interaction.channel.send(embed=embed, view=RecruitCabinetView())

    recruit_cabinet_panels[gid] = {
        "channel_id": interaction.channel_id,
        "message_id": msg.id,
        "text":       prev.get("text"),
        "image_url":  prev.get("image_url"),
    }
    save_data()
    await interaction.response.send_message("✅ Кабинет рекрута создан.", ephemeral=True)


@tree.command(name="кабинет_рекрута_фото", description="Изменить фото панели кабинета рекрута")
@app_commands.describe(url="Ссылка на изображение")
async def slash_recruit_cabinet_photo(interaction: discord.Interaction, url: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid = interaction.guild_id
    if gid not in recruit_cabinet_panels:
        recruit_cabinet_panels[gid] = {}
    recruit_cabinet_panels[gid]["image_url"] = url
    save_data()
    await _refresh_recruit_cabinet_panel(interaction.guild)
    await interaction.response.send_message("✅ Фото кабинета рекрута обновлено!", ephemeral=True)


@tree.command(name="кабинет_рекрута_текст", description="Изменить текст описания кабинета рекрута")
@app_commands.describe(текст="Текст под заголовком")
async def slash_recruit_cabinet_text(interaction: discord.Interaction, текст: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid = interaction.guild_id
    if gid not in recruit_cabinet_panels:
        recruit_cabinet_panels[gid] = {}
    recruit_cabinet_panels[gid]["text"] = текст
    save_data()
    await _refresh_recruit_cabinet_panel(interaction.guild)
    await interaction.response.send_message("✅ Текст кабинета рекрута обновлён!", ephemeral=True)


@tree.command(name="канал_варнов", description="Установить канал для логов выдачи варнов рекрутами")
@app_commands.describe(канал="Текстовый канал для логов варнов")
async def slash_warn_log_channel(interaction: discord.Interaction, канал: discord.TextChannel):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    warn_log_channels[interaction.guild_id] = канал.id
    save_data()
    await interaction.response.send_message(f"✅ Канал логов варнов установлен: {канал.mention}", ephemeral=True)
