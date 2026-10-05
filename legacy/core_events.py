"""core_events -- split out of main.py."""

import asyncio
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands
from legacy.app import bot, tree
from legacy.afk import (
    AfkView,
    InactiveView,
    afk_expire_loop,
    inactive_expire_loop,
)
from legacy.cabinet import (
    PersonalCabinetView,
)
from legacy.contracts import (
    ActiveContractView,
    ContractPanelView,
)
from legacy.events import (
    ThreadListView,
    _handle_thread_slot_pick,
)
from legacy.feedback import (
    FeedbackPanelView,
)
from legacy.obshak import (
    ObshakView,
)
from legacy.private_vc import (
    PrivateVCView,
    build_private_vc_embed,
)
from legacy.recruit import (
    RecruitCabinetView,
)
from legacy.roster import (
    _refresh_roster,
)
from legacy.roulette import (
    load_roulette,
)
from legacy.shop import (
    ShopView,
)
from legacy.state import (
    event_lists,
    guild_shop_items,
    load_chips,
    load_data,
    load_obshak,
    load_points,
    message_counts,
    private_vc_settings,
    private_vcs,
    roster_settings,
    ticket_panels,
    voice_join_times,
    voice_minutes,
    voice_presence_settings,
)
from legacy.stats_gta import (
    update_stats,
)
from legacy.tickets import (
    ApplicationReviewView,
    PostCloseView,
    TicketPanelView,
)
from legacy.timers import (
    _voice_presence_ensure,
    backup_scheduler_loop,
    voice_presence_loop,
    vzh_reminder_loop,
)
from legacy.voice_rewards import (
    voice_reward_loop,
)
from legacy.vzp_monitor import (
    vzp_monitor_loop,
)
from legacy.warns import (
    WarnListView,
)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    # ── Трекинг минут в войсе ──
    if not member.bot:
        gid = member.guild.id
        uid = member.id
        now = datetime.now()

        # Вышел из канала или сменил канал
        if before.channel is not None:
            join_time = voice_join_times.get(gid, {}).get(uid)
            if join_time:
                minutes = int((now - join_time).total_seconds() // 60)
                if minutes > 0:
                    if gid not in voice_minutes:
                        voice_minutes[gid] = {}
                    voice_minutes[gid][uid] = voice_minutes[gid].get(uid, 0) + minutes
                voice_join_times.get(gid, {}).pop(uid, None)

        # Зашёл в новый канал
        if after.channel is not None:
            if gid not in voice_join_times:
                voice_join_times[gid] = {}
            voice_join_times[gid][uid] = now

    # ── Войс-присутствие: переподключиться, если бота выкинуло ──
    if member.id == bot.user.id and after.channel is None:
        vp = voice_presence_settings.get(member.guild.id)
        if vp and vp.get("enabled") and vp.get("channel_id"):
            asyncio.create_task(_voice_presence_ensure(member.guild))

    # ── Приватные комнаты ──
    settings = private_vc_settings.get(member.guild.id)
    if not settings:
        return

    create_ch_id = settings.get("create_channel_id")

    # Пользователь зашёл в канал-триггер
    if after.channel and after.channel.id == create_ch_id:
        category = member.guild.get_channel(settings.get("category_id"))
        overwrites = {
            member.guild.default_role: discord.PermissionOverwrite(connect=True, view_channel=True),
            member: discord.PermissionOverwrite(connect=True, view_channel=True, manage_channels=True, move_members=True),
            member.guild.me: discord.PermissionOverwrite(connect=True, view_channel=True, manage_channels=True, move_members=True),
        }
        try:
            vc = await member.guild.create_voice_channel(
                name=f"🔒 {member.display_name}",
                category=category,
                user_limit=10,
                overwrites=overwrites,
            )
            await member.move_to(vc)
        except Exception:
            return

        private_vcs[vc.id] = {
            "owner_id": member.id,
            "guild_id": member.guild.id,
            "panel_msg_id": None,
            "panel_channel_id": None,
        }

        panel_ch_id = settings.get("panel_channel_id")
        if panel_ch_id:
            panel_ch = member.guild.get_channel(panel_ch_id)
            if panel_ch:
                try:
                    msg = await panel_ch.send(
                        embed=build_private_vc_embed(member, vc),
                        view=PrivateVCView(),
                    )
                    private_vcs[vc.id]["panel_msg_id"]     = msg.id
                    private_vcs[vc.id]["panel_channel_id"] = panel_ch_id
                except Exception:
                    pass

    # Пользователь вышел из приватного канала — удаляем если пусто
    if before.channel and before.channel.id in private_vcs:
        vc = before.channel
        if len(vc.members) == 0:
            data = private_vcs.pop(vc.id, {})
            try:
                await vc.delete(reason="Приватный канал опустел")
            except Exception:
                pass
            if data.get("panel_msg_id") and data.get("panel_channel_id"):
                panel_ch = member.guild.get_channel(data["panel_channel_id"])
                if panel_ch:
                    try:
                        msg = await panel_ch.fetch_message(data["panel_msg_id"])
                        await msg.delete()
                    except Exception:
                        pass


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        await bot.process_commands(message)
        return
    gid = message.guild.id
    uid = message.author.id
    if gid not in message_counts:
        message_counts[gid] = {}
    message_counts[gid][uid] = message_counts[gid].get(uid, 0) + 1
    if isinstance(message.channel, discord.Thread):
        await _handle_thread_slot_pick(message)
    await bot.process_commands(message)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.message.delete(delay=0)
    elif isinstance(error, (commands.MemberNotFound, commands.BadArgument)):
        await ctx.send("❌ Неверный аргумент. Пример: `!warn @user причина`", delete_after=6)
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Не хватает аргумента: `{error.param.name}`", delete_after=6)


@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        if not interaction.response.is_done():
            await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    else:
        if not interaction.response.is_done():
            await interaction.response.send_message("❌ Ошибка выполнения команды.", ephemeral=True)


@bot.event
async def on_ready():
    load_data()
    load_obshak()
    load_points()
    load_chips()
    load_roulette()
    bot.add_view(AfkView())
    bot.add_view(InactiveView())
    bot.add_view(PrivateVCView())
    bot.add_view(ApplicationReviewView())
    bot.add_view(PostCloseView())
    bot.add_view(ContractPanelView())
    bot.add_view(ActiveContractView(0))
    bot.add_view(FeedbackPanelView())
    bot.add_view(ObshakView())
    bot.add_view(PersonalCabinetView())
    bot.add_view(RecruitCabinetView())
    bot.add_view(WarnListView())
    # RosterPaginationView — stateful, восстанавливается через _refresh_roster при запуске
    for guild_id in guild_shop_items:
        bot.add_view(ShopView(guild_id))
    for guild_id, panel in ticket_panels.items():
        cat_id = panel.get("category_id")
        if cat_id:
            bot.add_view(TicketPanelView(cat_id))
    for message_id in event_lists:
        bot.add_view(ThreadListView(message_id))
    try:
        await tree.sync()
    except discord.HTTPException as exc:       # e.g. over the 100-command limit: don't abort the rest of startup
        print(f"WARNING: tree.sync не удался, слэш-команды не обновлены: {exc}")
    if not vzp_monitor_loop.is_running():
        vzp_monitor_loop.start()
    print(f"Bot online: {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name="MORIARTY Helper"
    ))
    if not update_stats.is_running():
        update_stats.start()
    if not voice_reward_loop.is_running():
        voice_reward_loop.start()
    if not inactive_expire_loop.is_running():
        inactive_expire_loop.start()
    if not afk_expire_loop.is_running():
        afk_expire_loop.start()
    if not backup_scheduler_loop.is_running():
        backup_scheduler_loop.start()
    if not vzh_reminder_loop.is_running():
        vzh_reminder_loop.start()
    if not voice_presence_loop.is_running():
        voice_presence_loop.start()
    # Пересоздаём панели состава чтобы кнопки снова работали после рестарта
    for gid in list(roster_settings.keys()):
        guild = bot.get_guild(gid)
        if guild:
            await _refresh_roster(guild)


# Авто-обновление при смене роли
@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    cfg             = roster_settings.get(after.guild.id, {})
    member_role_id  = cfg.get("member_role_id")
    academy_role_id = cfg.get("academy_role_id")
    if not member_role_id and not academy_role_id:
        return
    before_ids = {r.id for r in before.roles}
    after_ids  = {r.id for r in after.roles}
    watch_ids  = set(filter(None, [member_role_id, academy_role_id]))
    if watch_ids & (before_ids ^ after_ids):
        await _refresh_roster(after.guild)
