"""cabinet -- split out of main.py."""

from datetime import datetime

import discord
from discord import app_commands, ui
from legacy.app import tree
from legacy.feedback import (
    FeedbackModal,
)
from legacy.helpers import (
    _footer,
    get_chips,
    get_points,
    get_warns,
    is_admin,
    warn_payment_label,
)
from legacy.state import (
    cabinet_invite_links,
    cabinet_panels,
    message_counts,
    save_data,
    voice_join_times,
    voice_minutes,
)


DEFAULT_CABINET_TEXT = "Здесь ты можешь посмотреть свою статистику, баланс и оставить предложение."


def build_cabinet_embed(guild_id: int) -> discord.Embed:
    settings  = cabinet_panels.get(guild_id, {})
    text      = settings.get("text") or DEFAULT_CABINET_TEXT
    image_url = settings.get("image_url")

    embed = discord.Embed(
        title="🪪 Личный кабинет",
        description=text,
        color=0x2b2d31,
    )
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
    return embed


async def _refresh_cabinet_panel(guild: discord.Guild):
    settings = cabinet_panels.get(guild.id)
    if not settings or not settings.get("message_id"):
        return
    try:
        ch  = guild.get_channel(settings["channel_id"])
        msg = await ch.fetch_message(settings["message_id"])
        await msg.edit(embed=build_cabinet_embed(guild.id), view=PersonalCabinetView())
    except Exception:
        pass


class PersonalCabinetView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # ── Ряд 1 ──

    @ui.button(label="Баланс", emoji="💎", style=discord.ButtonStyle.secondary, custom_id="cabinet_balance", row=0)
    async def btn_balance(self, interaction: discord.Interaction, button: ui.Button):
        gid = interaction.guild_id
        uid = interaction.user.id
        pts   = get_points(gid, uid)
        chips = get_chips(gid, uid)
        embed = discord.Embed(
            title="💰 Твой баланс",
            color=0x2b2d31,
            timestamp=datetime.now(),
        )
        embed.add_field(name="Алмазы", value=f"**{pts:,}** 💎".replace(",", "."), inline=True)
        embed.add_field(name="Фишки", value=f"**{chips:,}** 🎰".replace(",", "."), inline=True)
        embed.set_footer(text="MORIARTY", icon_url=_footer(gid))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Варны", emoji="⚠️", style=discord.ButtonStyle.secondary, custom_id="cabinet_warns", row=0)
    async def btn_warns(self, interaction: discord.Interaction, button: ui.Button):
        warn_data = get_warns(interaction.guild_id, interaction.user.id)
        if not warn_data:
            embed = discord.Embed(
                title="✅ Варны",
                description="У тебя нет варнов.",
                color=discord.Color.green(),
                timestamp=datetime.now(),
            )
        else:
            embed = discord.Embed(
                title="⚠️ Варны",
                color=discord.Color.orange(),
                timestamp=datetime.now(),
            )
            embed.add_field(name="Количество", value=f"{warn_data['warns']}/3", inline=True)
            embed.add_field(name="Причина", value=warn_data.get("reason", "—"), inline=True)
            embed.add_field(name="Оплата", value=warn_payment_label(warn_data), inline=True)
        embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Фидбек", emoji="💬", style=discord.ButtonStyle.danger, custom_id="cabinet_feedback", row=0)
    async def btn_feedback(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(FeedbackModal())

    # ── Ряд 2 ──

    @ui.button(label="Статистика", style=discord.ButtonStyle.secondary, custom_id="cabinet_stats", row=1)
    async def btn_stats(self, interaction: discord.Interaction, button: ui.Button):
        member   = interaction.user
        gid, uid = interaction.guild_id, member.id

        msgs    = message_counts.get(gid, {}).get(uid, 0)
        v_mins  = voice_minutes.get(gid, {}).get(uid, 0)
        # Добавляем текущую сессию если сейчас в войсе
        join_t = voice_join_times.get(gid, {}).get(uid)
        if join_t:
            v_mins += int((datetime.now() - join_t).total_seconds() // 60)

        joined = member.joined_at.strftime("%d.%m.%Y") if member.joined_at else "—"

        embed = discord.Embed(
            title="📊 Статистика",
            color=0x2b2d31,
            timestamp=datetime.now(),
        )
        embed.add_field(name="👤 Имя", value=str(member.display_name), inline=True)
        embed.add_field(name="🆔 User ID", value=str(uid), inline=True)
        embed.add_field(name="📅 Вступил", value=joined, inline=True)
        embed.add_field(name="💬 Сообщений", value=str(msgs), inline=True)
        embed.add_field(name="🎙 Минут в войсе", value=str(v_mins), inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="MORIARTY", icon_url=_footer(gid))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Пригласить друга", style=discord.ButtonStyle.primary, custom_id="cabinet_invite", row=1)
    async def btn_invite(self, interaction: discord.Interaction, button: ui.Button):
        link = cabinet_invite_links.get(interaction.guild_id)
        if not link:
            return await interaction.response.send_message(
                "❌ Пригласительная ссылка ещё не настроена администратором.", ephemeral=True
            )
        text = (
            "**Чтобы пригласить друга, скопируй ссылку ниже**\n"
            f"```\n{link}\n```\n"
            "После вступления, друг должен заполнить тикет в канале "
            "<#1466567658601189481>\n"
            "__За каждого приглашенного человека в семью, полагается вознаграждение__"
        )
        await interaction.response.send_message(text, ephemeral=True)


@tree.command(name="личный_кабинет", description="Создать панель личного кабинета в текущем канале")
async def slash_cabinet(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)

    gid = interaction.guild_id

    # Удалить старую панель если есть
    existing = cabinet_panels.get(gid)
    if existing and existing.get("message_id"):
        try:
            old_ch  = interaction.guild.get_channel(existing["channel_id"])
            old_msg = await old_ch.fetch_message(existing["message_id"])
            await old_msg.delete()
        except Exception:
            pass

    prev  = cabinet_panels.get(gid, {})
    embed = build_cabinet_embed(gid)
    msg   = await interaction.channel.send(embed=embed, view=PersonalCabinetView())

    cabinet_panels[gid] = {
        "channel_id": interaction.channel_id,
        "message_id": msg.id,
        "text":       prev.get("text"),
        "image_url":  prev.get("image_url"),
    }
    save_data()
    await interaction.response.send_message("✅ Личный кабинет создан.", ephemeral=True)


@tree.command(name="личный_кабинет_фото", description="Изменить фото панели личного кабинета")
@app_commands.describe(url="Ссылка на изображение")
async def slash_cabinet_photo(interaction: discord.Interaction, url: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid = interaction.guild_id
    if gid not in cabinet_panels:
        cabinet_panels[gid] = {}
    cabinet_panels[gid]["image_url"] = url
    save_data()
    await _refresh_cabinet_panel(interaction.guild)
    await interaction.response.send_message("✅ Фото кабинета обновлено!", ephemeral=True)


@tree.command(name="личный_кабинет_текст", description="Изменить текст описания личного кабинета")
@app_commands.describe(текст="Текст под заголовком")
async def slash_cabinet_text(interaction: discord.Interaction, текст: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid = interaction.guild_id
    if gid not in cabinet_panels:
        cabinet_panels[gid] = {}
    cabinet_panels[gid]["text"] = текст
    save_data()
    await _refresh_cabinet_panel(interaction.guild)
    await interaction.response.send_message("✅ Текст кабинета обновлён!", ephemeral=True)


@tree.command(name="пригласительная_ссылка", description="Установить пригласительную ссылку для кнопки в личном кабинете")
@app_commands.describe(ссылка="Ссылка-приглашение на сервер")
async def slash_cabinet_invite(interaction: discord.Interaction, ссылка: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    cabinet_invite_links[interaction.guild_id] = ссылка
    save_data()
    await interaction.response.send_message(f"✅ Пригласительная ссылка установлена: `{ссылка}`", ephemeral=True)
