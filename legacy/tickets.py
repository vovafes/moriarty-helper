"""tickets -- split out of main.py."""

import asyncio
from datetime import datetime

import discord
from discord import app_commands, ui
from legacy.app import now_msk, tree
from legacy.helpers import (
    DEFAULT_TICKET_DESC,
    DEFAULT_TICKET_TITLE,
    _approve_gif,
    _footer,
    bump_recruit_stat,
    is_admin,
    is_ticket_manager,
)
from legacy.state import (
    DEFAULT_TICKET_IMAGE,
    admin_roles,
    interview_channels,
    reject_log_channels,
    save_data,
    ticket_counters,
    ticket_manager_roles,
    ticket_panels,
    ticket_ping_role,
    ticket_texts,
    ticket_viewer_roles,
    ticket_voice_channels,
)


class RejectModal(ui.Modal, title="❌ Причина отклонения"):
    reason = ui.TextInput(label="Укажите причину", style=discord.TextStyle.paragraph, required=True)

    def __init__(self, applicant_id: int, original_message: discord.Message, channel: discord.TextChannel):
        super().__init__()
        self.applicant_id     = applicant_id
        self.original_message = original_message
        self.channel          = channel

    async def on_submit(self, interaction: discord.Interaction):
        reason = str(self.reason)

        old_embed = self.original_message.embeds[0]
        new_embed = old_embed.copy()
        new_embed.color = discord.Color.red()
        new_embed.add_field(
            name="❌ Статус",
            value=f"Отклонено — {interaction.user.mention}\n**Причина:** {reason}",
            inline=False,
        )
        await self.original_message.edit(embed=new_embed, view=None)

        try:
            target = await interaction.client.fetch_user(self.applicant_id)
            dm_embed = discord.Embed(
                title="❌ Ваша заявка отклонена",
                description=f"**Причина:**\n> {reason}",
                color=discord.Color.red(),
                timestamp=datetime.now(),
            )
            dm_embed.add_field(name="📅 Дата", value=now_msk().strftime("%d.%m.%Y %H:%M") + " МСК")
            dm_embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
            await target.send(embed=dm_embed)
        except Exception:
            pass

        log_channel_id = reject_log_channels.get(interaction.guild_id)
        if log_channel_id:
            log_channel = interaction.guild.get_channel(log_channel_id)
            if log_channel:
                try:
                    log_embed = discord.Embed(
                        title="❌ Заявка отклонена",
                        color=discord.Color.red(),
                        timestamp=datetime.now(),
                    )
                    log_embed.add_field(name="Заявка от пользователя", value=f"<@{self.applicant_id}>", inline=False)
                    log_embed.add_field(name="Причина", value=reason, inline=False)
                    log_embed.add_field(name="Рассматривал", value=interaction.user.mention, inline=False)
                    log_embed.set_thumbnail(url=_footer(interaction.guild_id))
                    log_embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
                    await log_channel.send(embed=log_embed)
                except Exception:
                    pass

        bump_recruit_stat(interaction.guild_id, interaction.user.id, "rejected")

        await interaction.response.send_message(
            "✅ Заявка отклонена. Канал закроется через 10 секунд.", ephemeral=True
        )
        await asyncio.sleep(10)
        try:
            await self.channel.delete(reason="Заявка отклонена")
        except Exception:
            pass


class ApplicationModal(ui.Modal, title="📋 Подать заявку"):
    nickname = ui.TextInput(label="Ваш ник и статик в игре",  placeholder="Nick Name | 777",     required=True)
    hours_age = ui.TextInput(label="Часов в игре / Возраст",  placeholder="2500 / 18",            required=True)
    families  = ui.TextInput(label="В каких семьях был?",     style=discord.TextStyle.paragraph,  required=True)
    recoil    = ui.TextInput(label="Откат со стрельбой",      placeholder="DM, Архив, YouTube",   required=True)
    content   = ui.TextInput(label="Какой контент симпатизирует?", placeholder="РП / ВЗП",        required=True)

    def __init__(self, category_id: int):
        super().__init__()
        self.category_id = category_id

    async def on_submit(self, interaction: discord.Interaction):
        guild      = interaction.guild
        applicant  = interaction.user
        category   = guild.get_channel(self.category_id)
        admin_role_id = admin_roles.get(guild.id)
        admin_role    = guild.get_role(admin_role_id) if admin_role_id else None
        tm_role_id    = ticket_manager_roles.get(guild.id)
        tm_role       = guild.get_role(tm_role_id) if tm_role_id else None
        ping_role_id  = ticket_ping_role.get(guild.id)
        ping_role     = guild.get_role(ping_role_id) if ping_role_id else tm_role

        ticket_counters[guild.id] = ticket_counters.get(guild.id, 0) + 1
        ticket_num = ticket_counters[guild.id]
        save_data()

        ticket_perms = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            applicant: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
        }
        if admin_role:
            overwrites[admin_role] = ticket_perms
        if tm_role:
            overwrites[tm_role] = ticket_perms
        # Роли с доступом на просмотр
        for rid in ticket_viewer_roles.get(guild.id, []):
            r = guild.get_role(rid)
            if r:
                overwrites[r] = ticket_perms

        try:
            ticket_channel = await guild.create_text_channel(
                name=f"ticket-{str(ticket_num).zfill(4)}",
                category=category,
                overwrites=overwrites,
                reason=f"Заявка от {applicant}",
            )
        except Exception as e:
            return await interaction.response.send_message(
                f"❌ Не удалось создать канал: {e}", ephemeral=True
            )

        embed = discord.Embed(
            title="📋 Новая заявка",
            color=discord.Color.yellow(),
            timestamp=datetime.now(),
        )
        embed.set_thumbnail(url=applicant.display_avatar.url)
        embed.add_field(name="👤 Пользователь",           value=f"{applicant.mention} ({applicant})", inline=True)
        embed.add_field(name="🎮 Ник | Статик",            value=str(self.nickname),   inline=True)
        embed.add_field(name="\u200B",                     value="\u200B",             inline=True)
        embed.add_field(name="⏱️ Часов / Возраст",        value=str(self.hours_age),  inline=True)
        embed.add_field(name="🎯 Откат со стрельбой",     value=str(self.recoil),     inline=True)
        embed.add_field(name="🎮 Контент",                value=str(self.content),    inline=True)
        embed.add_field(name="🏠 Был в семьях",           value=str(self.families),   inline=False)
        embed.set_footer(text=f"MORIARTY • {applicant.id}", icon_url=_footer(guild.id))

        view = ApplicationReviewView(applicant.id)
        pings = ping_role.mention if ping_role else None
        await ticket_channel.send(content=pings, embed=embed, view=view)

        sent_embed = discord.Embed(
            title="📬 Заявка отправлена!",
            description=(
                "Ваша заявка принята. Ожидайте ответа.\n"
                "Результат придёт в личные сообщения."
            ),
            color=discord.Color.yellow(),
            timestamp=datetime.now(),
        )
        sent_embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
        await interaction.response.send_message(embed=sent_embed, ephemeral=True)


class TicketPanelView(ui.View):
    def __init__(self, category_id: int):
        super().__init__(timeout=None)
        self.category_id = category_id

    @ui.button(label="Подать заявку", style=discord.ButtonStyle.secondary, emoji="📋", custom_id="ticket_apply")
    async def apply(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(ApplicationModal(self.category_id))


async def _ensure_ticket_call_voice(
    guild: discord.Guild,
    ticket_channel: discord.abc.GuildChannel,
    applicant_id: int,
    manager: discord.Member,
) -> discord.VoiceChannel:
    """Создаёт (или возвращает существующий) голосовой канал для обзвона по тикету."""
    existing_id = ticket_voice_channels.get(ticket_channel.id)
    if existing_id:
        vc = guild.get_channel(existing_id)
        if isinstance(vc, discord.VoiceChannel):
            return vc

    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, connect=True, manage_channels=True, move_members=True, speak=True
        ),
        manager: discord.PermissionOverwrite(
            view_channel=True, connect=True, speak=True, move_members=True
        ),
    }

    applicant = guild.get_member(applicant_id)
    if applicant is None:
        try:
            applicant = await guild.fetch_member(applicant_id)
        except Exception:
            applicant = None
    if applicant:
        overwrites[applicant] = discord.PermissionOverwrite(
            view_channel=True, connect=True, speak=True
        )

    tm_role_id = ticket_manager_roles.get(guild.id)
    if tm_role_id:
        tm_role = guild.get_role(tm_role_id)
        if tm_role:
            overwrites[tm_role] = discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True, move_members=True
            )

    for rid in ticket_viewer_roles.get(guild.id, []):
        r = guild.get_role(rid)
        if r:
            overwrites[r] = discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True
            )

    name = f"обзвон-{ticket_channel.name}"[:100]
    vc = await guild.create_voice_channel(
        name=name,
        category=getattr(ticket_channel, "category", None),
        overwrites=overwrites,
        reason=f"Обзвон по заявке {ticket_channel.name}",
    )
    ticket_voice_channels[ticket_channel.id] = vc.id
    save_data()
    return vc


async def _delete_ticket_call_voice(guild: discord.Guild, ticket_channel_id: int):
    """Удаляет голосовой канал обзвона, привязанный к тикету."""
    vc_id = ticket_voice_channels.pop(ticket_channel_id, None)
    if not vc_id:
        return
    save_data()
    vc = guild.get_channel(vc_id)
    if isinstance(vc, discord.VoiceChannel):
        try:
            await vc.delete(reason="Тикет закрыт — обзвон завершён")
        except Exception:
            pass


class ApplicationReviewView(ui.View):
    def __init__(self, applicant_id: int = 0):
        super().__init__(timeout=None)
        self.applicant_id = applicant_id

    def _get_applicant_id(self, message: discord.Message) -> int:
        if self.applicant_id:
            return self.applicant_id
        try:
            footer = message.embeds[0].footer.text  # "MORIARTY • 123..."
            return int(footer.split("• ")[-1].strip())
        except Exception:
            return 0

    @ui.button(label="✅ Одобрить", style=discord.ButtonStyle.success, custom_id="ticket_approve")
    async def approve(self, interaction: discord.Interaction, button: ui.Button):
        if not is_ticket_manager(interaction):
            return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)

        channel = interaction.channel
        await interaction.response.defer(ephemeral=True)

        applicant_id = self._get_applicant_id(interaction.message)

        old_embed = interaction.message.embeds[0]
        new_embed = old_embed.copy()
        new_embed.color = discord.Color.green()
        new_embed.add_field(
            name="✅ Статус",
            value=f"Одобрено — {interaction.user.mention} ({now_msk().strftime('%d.%m.%Y %H:%M')} МСК)",
            inline=False,
        )
        await interaction.message.edit(embed=new_embed, view=None)

        try:
            target = await interaction.client.fetch_user(applicant_id)
            dm_embed = discord.Embed(
                title="🏆 Добро пожаловать в семью MORIARTY!",
                description=(
                    "Твоя заявка была **одобрена**!\n\n"
                    "Добро пожаловать! 🖤"
                ),
                color=0x2B2D31,
                timestamp=datetime.now(),
            )
            dm_embed.add_field(name="📅 Дата принятия", value=now_msk().strftime("%d.%m.%Y %H:%M") + " МСК", inline=True)
            dm_embed.add_field(name="👮 Одобрил", value=interaction.user.mention, inline=True)
            dm_embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
            if _approve_gif(interaction.guild_id):
                dm_embed.set_image(url=_approve_gif(interaction.guild_id))
            await target.send(embed=dm_embed)
        except Exception:
            pass

        log_channel_id = reject_log_channels.get(interaction.guild_id)
        if log_channel_id:
            log_channel = interaction.client.get_channel(log_channel_id)
            if log_channel:
                try:
                    log_embed = discord.Embed(
                        title="✅ Заявка одобрена",
                        color=discord.Color.green(),
                        timestamp=datetime.now(),
                    )
                    log_embed.add_field(name="Заявка от пользователя", value=f"<@{applicant_id}>", inline=False)
                    log_embed.add_field(name="Одобрил", value=interaction.user.mention, inline=False)
                    log_embed.set_thumbnail(url=_footer(interaction.guild_id))
                    log_embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
                    await log_channel.send(embed=log_embed)
                except Exception:
                    pass

        bump_recruit_stat(interaction.guild_id, interaction.user.id, "approved")

        await interaction.followup.send("✅ Заявка одобрена.", ephemeral=True)
        close_embed = discord.Embed(
            title="🔒 Тикет закрыт",
            description=f"Заявка **одобрена** — {interaction.user.mention}\nВыберите следующее действие:",
            color=discord.Color.green(),
            timestamp=datetime.now(),
        )
        close_embed.set_footer(text=f"MORIARTY • {applicant_id}", icon_url=_footer(interaction.guild_id))
        await channel.send(embed=close_embed, view=PostCloseView())

    @ui.button(label="Пригласить на обзвон", style=discord.ButtonStyle.primary, emoji="🎤", custom_id="ticket_invite_call")
    async def invite_call(self, interaction: discord.Interaction, button: ui.Button):
        if not is_ticket_manager(interaction):
            return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)

        applicant_id = self._get_applicant_id(interaction.message)
        if not applicant_id:
            return await interaction.response.send_message("❌ Не удалось определить заявителя.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        ticket_ch = interaction.channel

        try:
            vc = await _ensure_ticket_call_voice(guild, ticket_ch, applicant_id, interaction.user)
        except Exception as e:
            return await interaction.followup.send(
                f"❌ Не удалось создать голосовой канал: {e}", ephemeral=True
            )

        link = f"https://discord.com/channels/{guild.id}/{vc.id}"
        dm_ok = False
        try:
            target = await interaction.client.fetch_user(applicant_id)
            dm_embed = discord.Embed(
                title="🎤 Приглашение на обзвон",
                description=(
                    f"Тебя пригласили на **обзвон** по заявке в **{guild.name}**.\n\n"
                    f"Зайди в голосовой канал: **{vc.name}**\n"
                    f"→ {link}"
                ),
                color=discord.Color.blue(),
                timestamp=datetime.now(),
            )
            dm_embed.add_field(name="👮 Пригласил", value=interaction.user.mention, inline=True)
            dm_embed.set_footer(text="MORIARTY", icon_url=_footer(guild.id))
            await target.send(embed=dm_embed)
            dm_ok = True
        except Exception:
            pass

        note = discord.Embed(
            title="🎤 Обзвон",
            description=(
                f"{interaction.user.mention} пригласил <@{applicant_id}> на обзвон.\n"
                f"Канал: {vc.mention}"
            ),
            color=discord.Color.blue(),
            timestamp=datetime.now(),
        )
        note.set_footer(text="MORIARTY", icon_url=_footer(guild.id))
        try:
            await ticket_ch.send(embed=note)
        except Exception:
            pass

        if dm_ok:
            await interaction.followup.send(
                f"✅ Приглашение отправлено в ЛС. Канал: {vc.mention}", ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"⚠️ Канал создан ({vc.mention}), но ЛС закрыты — напиши заявителю вручную.",
                ephemeral=True,
            )

    @ui.button(label="❌ Отклонить", style=discord.ButtonStyle.danger, custom_id="ticket_reject")
    async def reject(self, interaction: discord.Interaction, button: ui.Button):
        if not is_ticket_manager(interaction):
            return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
        applicant_id = self._get_applicant_id(interaction.message)
        await interaction.response.send_modal(RejectModal(applicant_id, interaction.message, interaction.channel))


class PostCloseView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    def _get_applicant_id(self, message: discord.Message) -> int:
        try:
            return int(message.embeds[0].footer.text.split("• ")[-1].strip())
        except Exception:
            return 0

    @ui.button(label="🔓 Открыть тикет", style=discord.ButtonStyle.success, custom_id="ticket_reopen")
    async def reopen(self, interaction: discord.Interaction, button: ui.Button):
        if not is_ticket_manager(interaction):
            return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
        applicant_id = self._get_applicant_id(interaction.message)
        await interaction.response.defer(ephemeral=True)
        await interaction.message.delete()
        reopen_embed = discord.Embed(
            title="🔓 Тикет переоткрыт",
            description=f"Переоткрыт — {interaction.user.mention}",
            color=discord.Color.yellow(),
            timestamp=datetime.now(),
        )
        reopen_embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
        await interaction.channel.send(embed=reopen_embed, view=ApplicationReviewView(applicant_id))
        await interaction.followup.send("✅ Тикет переоткрыт.", ephemeral=True)

    @ui.button(label="Пригласить на обзвон", style=discord.ButtonStyle.primary, emoji="🎙", custom_id="ticket_invite_voice")
    async def invite_voice(self, interaction: discord.Interaction, button: ui.Button):
        if not is_ticket_manager(interaction):
            return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        applicant_id = self._get_applicant_id(interaction.message)
        guild = interaction.guild

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(connect=False),
            interaction.user: discord.PermissionOverwrite(connect=True, manage_channels=True),
        }
        tm_role_id = ticket_manager_roles.get(guild.id)
        if tm_role_id:
            tm_role = guild.get_role(tm_role_id)
            if tm_role:
                overwrites[tm_role] = discord.PermissionOverwrite(connect=True)

        try:
            # Используем настроенный канал обзвона, если он есть
            interview_channel_id = interview_channels.get(guild.id)
            voice_channel = guild.get_channel(interview_channel_id) if interview_channel_id else None
            if voice_channel is None:
                voice_channel = await guild.create_voice_channel(
                    name=f"Обзвон-тикет-{applicant_id}",
                    overwrites=overwrites,
                )

            ticket_voice_channels[interaction.channel.id] = voice_channel.id
            save_data()

            try:
                target = await interaction.client.fetch_user(applicant_id)
                invite = await voice_channel.create_invite(max_age=3600)
                dm_embed = discord.Embed(
                    title="🎙 Приглашение на обзвон",
                    description=f"Вас приглашают на обзвон в канале {voice_channel.mention}!\n\nСсылка: {invite.url}",
                    color=discord.Color.blue(),
                    timestamp=datetime.now(),
                )
                dm_embed.set_footer(text="MORIARTY", icon_url=_footer(guild.id))
                await target.send(embed=dm_embed)
            except Exception as e:
                await interaction.followup.send(f"❌ Не удалось отправить DM пользователю: {e}", ephemeral=True)
            else:
                await interaction.followup.send(f"✅ Голосовой канал готов, приглашение отправлено в DM <@{applicant_id}>.", ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка при создании голосового канала: {e}", ephemeral=True)

    @ui.button(label="🗑️ Удалить канал", style=discord.ButtonStyle.danger, custom_id="ticket_delete_channel")
    async def delete_channel(self, interaction: discord.Interaction, button: ui.Button):
        if not is_ticket_manager(interaction):
            return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await _delete_ticket_call_voice(interaction.guild, interaction.channel.id)
        await asyncio.sleep(3)
        try:
            await interaction.channel.delete(reason=f"Тикет удалён — {interaction.user}")
        except Exception:
            pass


@tree.command(name="тикет", description="Создать панель заявок")
@app_commands.describe(
    канал_панели="Канал, куда отправить кнопку заявки",
    категория="Категория, где будут создаваться каналы-тикеты",
)
async def slash_ticket(interaction: discord.Interaction, канал_панели: discord.TextChannel, категория: discord.CategoryChannel):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    tt = ticket_texts.get(interaction.guild_id, {})
    embed = discord.Embed(
        title=tt.get("title", DEFAULT_TICKET_TITLE),
        description=tt.get("desc", DEFAULT_TICKET_DESC),
        color=discord.Color.red(),
    )
    img = tt.get("image", DEFAULT_TICKET_IMAGE)
    if img:
        embed.set_image(url=img)
    embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))

    view = TicketPanelView(категория.id)
    msg  = await канал_панели.send(embed=embed, view=view)

    ticket_panels[interaction.guild_id] = {
        "panel_channel_id": канал_панели.id,
        "category_id":      категория.id,
        "message_id":       msg.id,
    }
    save_data()

    await interaction.response.send_message(
        f"✅ Панель отправлена в {канал_панели.mention}. Тикеты будут создаваться в **{категория.name}**",
        ephemeral=True,
    )


@tree.command(name="тикет_менеджер", description="Назначить роль тикет-менеджера")
@app_commands.describe(роль="Роль, которая тегается в тикетах и может их рассматривать")
async def slash_ticket_manager(interaction: discord.Interaction, роль: discord.Role):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    ticket_manager_roles[interaction.guild_id] = роль.id
    save_data()
    await interaction.response.send_message(
        f"✅ Роль тикет-менеджера установлена: {роль.mention}",
        ephemeral=True,
    )


@tree.command(name="тикет_доступ", description="Добавить роль с доступом к тикетам (видит канал)")
@app_commands.describe(роль="Роль, которая будет видеть канал тикета")
async def slash_ticket_viewer_add(interaction: discord.Interaction, роль: discord.Role):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid = interaction.guild_id
    if gid not in ticket_viewer_roles:
        ticket_viewer_roles[gid] = []
    if роль.id not in ticket_viewer_roles[gid]:
        ticket_viewer_roles[gid].append(роль.id)
    save_data()
    roles_list = ", ".join(f"<@&{rid}>" for rid in ticket_viewer_roles[gid])
    await interaction.response.send_message(
        f"✅ {роль.mention} добавлена к тикетам.\nВсе роли с доступом: {roles_list}",
        ephemeral=True,
    )


@tree.command(name="тикет_доступ_убрать", description="Убрать роль из доступа к тикетам")
@app_commands.describe(роль="Роль, которую нужно убрать")
async def slash_ticket_viewer_remove(interaction: discord.Interaction, роль: discord.Role):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid = interaction.guild_id
    viewers = ticket_viewer_roles.get(gid, [])
    if роль.id not in viewers:
        return await interaction.response.send_message(f"❌ {роль.mention} и так не в списке доступа.", ephemeral=True)
    viewers.remove(роль.id)
    ticket_viewer_roles[gid] = viewers
    save_data()
    await interaction.response.send_message(f"✅ {роль.mention} убрана из доступа к тикетам.", ephemeral=True)


@tree.command(name="канал_обзвона", description="Установить голосовой канал для приглашений на обзвон")
@app_commands.describe(канал="Голосовой канал для обзвонов")
async def slash_interview_channel(interaction: discord.Interaction, канал: discord.VoiceChannel):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    interview_channels[interaction.guild_id] = канал.id
    save_data()
    await interaction.response.send_message(f"✅ Канал для обзвонов установлен: {канал.mention}", ephemeral=True)


@app_commands.describe(роль="Роль для тега (если не задана — тегается тикет-менеджер)")
async def slash_ticket_ping(interaction: discord.Interaction, роль: discord.Role):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    ticket_ping_role[interaction.guild_id] = роль.id
    save_data()
    await interaction.response.send_message(
        f"✅ В тикетах будет тегаться: {роль.mention}",
        ephemeral=True,
    )


class TicketTextModal(ui.Modal, title="✏️ Текст панели заявок"):
    title_input = ui.TextInput(
        label="Заголовок",
        default="📋 Вступление в MORIARTY",
        max_length=256,
        required=True,
    )
    desc_input = ui.TextInput(
        label="Описание",
        style=discord.TextStyle.paragraph,
        max_length=4000,
        required=True,
    )
    image_input = ui.TextInput(
        label="Ссылка на картинку (оставь пустым — без картинки)",
        required=False,
        placeholder="https://...",
    )

    def __init__(self, guild_id: int):
        super().__init__()
        tt = ticket_texts.get(guild_id, {})
        self.title_input.default = tt.get("title", DEFAULT_TICKET_TITLE)
        self.desc_input.default  = tt.get("desc",  DEFAULT_TICKET_DESC)
        self.image_input.default = tt.get("image", DEFAULT_TICKET_IMAGE)
        self._guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        ticket_texts[self._guild_id] = {
            "title": str(self.title_input),
            "desc":  str(self.desc_input),
            "image": str(self.image_input).strip(),
        }
        save_data()

        # Обновить существующую панель если есть
        panel = ticket_panels.get(self._guild_id, {})
        if panel.get("message_id") and panel.get("panel_channel_id"):
            try:
                ch  = interaction.guild.get_channel(panel["panel_channel_id"])
                msg = await ch.fetch_message(panel["message_id"])
                tt  = ticket_texts[self._guild_id]
                embed = discord.Embed(title=tt["title"], description=tt["desc"], color=discord.Color.red())
                if tt["image"]:
                    embed.set_image(url=tt["image"])
                embed.set_footer(text="MORIARTY", icon_url=_footer(self._guild_id))
                await msg.edit(embed=embed)
            except Exception:
                pass

        await interaction.response.send_message("✅ Текст панели обновлён!", ephemeral=True)


@tree.command(name="тикет_текст", description="Изменить заголовок и текст панели заявок")
async def slash_ticket_text(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    await interaction.response.send_modal(TicketTextModal(interaction.guild_id))


@tree.command(name="лог_отказов", description="Настроить канал для логов одобрений и отказов по заявкам")
@app_commands.describe(канал="Канал, куда будут дублироваться отказы")
async def slash_reject_log(interaction: discord.Interaction, канал: discord.TextChannel):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    reject_log_channels[interaction.guild_id] = канал.id
    save_data()
    await interaction.response.send_message(
        f"✅ Отказы по заявкам теперь дублируются в {канал.mention}",
        ephemeral=True,
    )
