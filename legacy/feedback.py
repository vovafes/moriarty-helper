"""feedback -- split out of main.py."""

from datetime import datetime

import discord
from discord import ui
from legacy.app import bot
from legacy.helpers import (
    _footer,
    is_admin_ctx,
)
from legacy.state import (
    feedback_settings,
    save_data,
)


DEFAULT_FEEDBACK_TEXT = (
    "**💬 Предложения и обратная связь**\n\n"
    "Есть идея или замечание? Нажми кнопку ниже и заполни форму — "
    "твоё сообщение уйдёт старшему составу."
)


def build_feedback_panel_embed(guild_id: int) -> discord.Embed:
    cfg = feedback_settings.get(guild_id, {})
    text = cfg.get("text", DEFAULT_FEEDBACK_TEXT)
    image_url = cfg.get("image_url")
    embed = discord.Embed(
        title="📝 Обратная связь",
        description=text,
        color=discord.Color.blurple(),
        timestamp=datetime.now(),
    )
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
    return embed


class FeedbackModal(ui.Modal, title="📝 Оставить предложение"):
    message = ui.TextInput(
        label="Ваше предложение",
        style=discord.TextStyle.paragraph,
        placeholder="Напиши своё предложение или замечание...",
        required=True,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        cfg = feedback_settings.get(guild_id, {})
        log_channel_id = cfg.get("log_channel_id")

        if not log_channel_id:
            return await interaction.response.send_message(
                "❌ Канал для фидбеков не настроен. Обратитесь к администратору.",
                ephemeral=True,
            )

        log_channel = interaction.guild.get_channel(log_channel_id)
        if not log_channel:
            return await interaction.response.send_message(
                "❌ Канал для фидбеков не найден.", ephemeral=True
            )

        ping_role_id = cfg.get("ping_role_id")
        content = f"<@&{ping_role_id}>" if ping_role_id else None

        embed = discord.Embed(
            title="📝 Новое предложение",
            description=str(self.message),
            color=discord.Color.blurple(),
            timestamp=datetime.now(),
        )
        embed.set_author(
            name=str(interaction.user.display_name),
            icon_url=interaction.user.display_avatar.url,
        )
        embed.add_field(name="👤 От", value=interaction.user.mention, inline=True)
        embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))

        msg = await log_channel.send(content=content, embed=embed)

        # Создаём тред для обсуждения
        try:
            short = str(self.message)[:50].strip()
            thread_name = f"💬 {interaction.user.display_name}: {short}"
            await msg.create_thread(name=thread_name, auto_archive_duration=1440)
        except Exception:
            pass

        await interaction.response.send_message(
            "✅ Спасибо! Твоё предложение отправлено.", ephemeral=True
        )


class FeedbackPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Оставить предложение", style=discord.ButtonStyle.primary,
               emoji="📝", custom_id="feedback_submit")
    async def submit(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(FeedbackModal())


async def _refresh_feedback_panel(guild: discord.Guild):
    cfg = feedback_settings.get(guild.id, {})
    if not cfg.get("panel_message_id"):
        return
    try:
        channel = guild.get_channel(cfg["panel_channel_id"])
        if not channel:
            return
        msg = await channel.fetch_message(cfg["panel_message_id"])
        await msg.edit(embed=build_feedback_panel_embed(guild.id))
    except Exception:
        pass


@bot.command(name="feedback")
async def feedback_panel_cmd(ctx):
    """!feedback — создать панель обратной связи в этом канале"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()

    guild_id = ctx.guild.id
    if guild_id not in feedback_settings:
        feedback_settings[guild_id] = {}

    embed = build_feedback_panel_embed(guild_id)
    view  = FeedbackPanelView()
    msg   = await ctx.send(embed=embed, view=view)

    feedback_settings[guild_id]["panel_channel_id"]  = ctx.channel.id
    feedback_settings[guild_id]["panel_message_id"]  = msg.id
    save_data()

    try:
        await ctx.message.delete()
    except Exception:
        pass


@bot.command(name="feedback_канал")
async def feedback_channel_cmd(ctx, channel: discord.TextChannel):
    """!feedback_канал #канал — куда будут приходить фидбеки"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()

    guild_id = ctx.guild.id
    if guild_id not in feedback_settings:
        feedback_settings[guild_id] = {}

    feedback_settings[guild_id]["log_channel_id"] = channel.id
    save_data()
    await ctx.send(f"✅ Фидбеки будут приходить в {channel.mention}", delete_after=5)
    try:
        await ctx.message.delete()
    except Exception:
        pass


@bot.command(name="feedback_роль")
async def feedback_role_cmd(ctx, role: discord.Role):
    """!feedback_роль @роль — тег при новом фидбеке"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()

    guild_id = ctx.guild.id
    if guild_id not in feedback_settings:
        feedback_settings[guild_id] = {}

    feedback_settings[guild_id]["ping_role_id"] = role.id
    save_data()
    await ctx.send(f"✅ Роль для фидбеков: {role.mention}", delete_after=5)
    try:
        await ctx.message.delete()
    except Exception:
        pass


@bot.command(name="feedback_текст")
async def feedback_text_cmd(ctx, *, text: str):
    """!feedback_текст <текст> — изменить текст панели фидбеков"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()

    guild_id = ctx.guild.id
    if guild_id not in feedback_settings:
        feedback_settings[guild_id] = {}

    feedback_settings[guild_id]["text"] = text
    save_data()
    await _refresh_feedback_panel(ctx.guild)
    await ctx.send("✅ Текст панели обновлён!", delete_after=5)
    try:
        await ctx.message.delete()
    except Exception:
        pass


@bot.command(name="feedback_фото")
async def feedback_photo_cmd(ctx, url: str):
    """!feedback_фото <url> — изменить фото панели фидбеков"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()

    guild_id = ctx.guild.id
    if guild_id not in feedback_settings:
        feedback_settings[guild_id] = {}

    feedback_settings[guild_id]["image_url"] = url
    save_data()
    await _refresh_feedback_panel(ctx.guild)
    await ctx.send("✅ Фото панели обновлено!", delete_after=5)
    try:
        await ctx.message.delete()
    except Exception:
        pass
