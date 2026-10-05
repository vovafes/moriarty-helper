"""warns -- split out of main.py."""

from datetime import datetime

import discord
from discord import ui
from legacy.app import bot
from legacy.helpers import (
    _footer,
    get_warns,
    is_admin_ctx,
    remove_warn,
    set_warn,
    warn_payment_label,
)
from legacy.recruit import (
    _delete_warn_log_message,
)
from legacy.state import (
    save_data,
    warn_roles,
    warns_db,
)


@bot.command(name="warn")
async def warn_user(ctx, пользователь: discord.Member, количество: int, *, причина: str):
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    """!warn @пользователь <1-3> причина — выдать варн"""
    if количество not in (1, 2, 3):
        return await ctx.send("❌ Укажи количество варнов: 1, 2 или 3. Пример: `!warn @user 2 причина`", delete_after=6)

    set_warn(ctx.guild.id, пользователь.id, количество, причина, ctx.author.id)

    # Убрать все старые варн-роли и назначить новую
    guild_warn_roles = warn_roles.get(ctx.guild.id, {})
    roles_to_remove = [ctx.guild.get_role(rid) for rid in guild_warn_roles.values() if ctx.guild.get_role(rid)]
    new_role = ctx.guild.get_role(guild_warn_roles.get(количество))
    try:
        await пользователь.remove_roles(*[r for r in roles_to_remove if r], reason="Обновление варн-роли")
        if new_role:
            await пользователь.add_roles(new_role, reason=f"Warn {количество}/3")
    except Exception:
        pass

    embed = discord.Embed(
        title="⚠️ WARN",
        description=f"{пользователь.mention} получил warn!",
        color=discord.Color.red(),
        timestamp=datetime.now(),
    )
    embed.add_field(name="Причина", value=причина, inline=False)
    embed.add_field(name="Модератор", value=ctx.author.mention, inline=True)
    embed.add_field(name="Варны", value=f"**{количество}/3**", inline=True)
    if new_role:
        embed.add_field(name="Роль", value=new_role.mention, inline=True)
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed)

    try:
        dm_embed = discord.Embed(
            title="⚠️ Вы получили warn",
            description=f"**Причина:** {причина}\n**Варны:** {количество}/3",
            color=discord.Color.red(),
            timestamp=datetime.now(),
        )
        dm_embed.add_field(name="Модератор", value=ctx.author.mention)
        dm_embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
        await пользователь.send(embed=dm_embed)
    except Exception:
        pass

    try:
        await ctx.message.delete()
    except Exception:
        pass


@bot.command(name="unwarn")
async def admin_remove_warn(ctx, пользователь: discord.Member):
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    """!снять_варн @пользователь — снять варн"""
    if remove_warn(ctx.guild.id, пользователь.id):
        # Убрать все варн-роли
        guild_warn_roles = warn_roles.get(ctx.guild.id, {})
        roles_to_remove = [ctx.guild.get_role(rid) for rid in guild_warn_roles.values() if ctx.guild.get_role(rid)]
        try:
            await пользователь.remove_roles(*[r for r in roles_to_remove if r], reason="Снятие варна")
        except Exception:
            pass
        await _delete_warn_log_message(ctx.guild, пользователь.id)
        embed = discord.Embed(
            title="✅ Warn снят",
            description=f"У {пользователь.mention} снят warn",
            color=discord.Color.green(),
            timestamp=datetime.now(),
        )
        embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
        await ctx.send(embed=embed)
    else:
        await ctx.send("❌ У пользователя нет warn'ов!", delete_after=5)

    try:
        await ctx.message.delete()
    except Exception:
        pass


@bot.command(name="роль_варн")
async def set_warn_role(ctx, номер: int, роль: discord.Role):
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    """!роль_варн <1-3> @роль — привязать роль к уровню варна"""
    if номер not in (1, 2, 3):
        return await ctx.send("❌ Укажи номер 1, 2 или 3. Пример: `!роль_варн 1 @Варн1/3`", delete_after=6)
    if ctx.guild.id not in warn_roles:
        warn_roles[ctx.guild.id] = {}
    warn_roles[ctx.guild.id][номер] = роль.id
    save_data()
    embed = discord.Embed(
        title="✅ Варн-роль настроена",
        description=f"Варн **{номер}/3** → {роль.mention}",
        color=discord.Color.green(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed)
    await ctx.message.delete()


def _build_warnlist_embed(guild: discord.Guild) -> discord.Embed:
    guild_warns = warns_db.get(guild.id, {})
    active = {uid: d for uid, d in guild_warns.items() if d.get("warns", 0) > 0}

    embed = discord.Embed(
        title="⚠️ Список варнов",
        color=discord.Color.red(),
        timestamp=datetime.now(),
    )
    embed.set_footer(
        text=f"MORIARTY • Варнов выдано: {len(active)}",
        icon_url=_footer(guild.id),
    )

    if not active:
        embed.description = "✅ Ни у кого нет варнов!"
        return embed

    WARN_EMOJI = {1: "🟡", 2: "🟠", 3: "🔴"}
    lines = []
    for uid, d in sorted(active.items(), key=lambda x: x[1]["warns"], reverse=True):
        count    = d["warns"]
        emoji    = WARN_EMOJI.get(count, "⚠️")
        ts       = d.get("timestamp")
        date_str = ts.strftime("%d.%m.%Y") if isinstance(ts, datetime) else str(ts)[:10]
        pay_tag  = " 💵" if d.get("payment_method") == "money" else ""
        lines.append(
            f"{emoji} <@{uid}> — **{count}/3**{pay_tag}\n"
            f"└ Причина: {d['reason']} | <@{d['moderator']}> | {date_str}"
        )

    embed.description = "\n\n".join(lines)
    return embed


class WarnListView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🔄 Обновить", style=discord.ButtonStyle.secondary, custom_id="warnlist_refresh")
    async def refresh(self, interaction: discord.Interaction, button: ui.Button):
        embed = _build_warnlist_embed(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    @ui.button(label="👤 Мои варны", style=discord.ButtonStyle.primary, custom_id="warnlist_mine")
    async def my_warns(self, interaction: discord.Interaction, button: ui.Button):
        data = get_warns(interaction.guild_id, interaction.user.id)
        if not data or data.get("warns", 0) == 0:
            return await interaction.response.send_message("✅ У вас нет варнов!", ephemeral=True)
        count = data["warns"]
        WARN_EMOJI = {1: "🟡", 2: "🟠", 3: "🔴"}
        embed = discord.Embed(
            title="⚠️ Ваши варны",
            description=(
                f"{WARN_EMOJI.get(count, '⚠️')} Варнов: **{count}/3**\n"
                f"Причина: {data['reason']}\n"
                f"Модератор: <@{data['moderator']}>\n"
                f"Оплата: {warn_payment_label(data)}"
            ),
            color=discord.Color.orange(),
            timestamp=datetime.now(),
        )
        embed.set_footer(text="MORIARTY", icon_url=_footer(interaction.guild_id))
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.command(name="warnlist")
async def warnlist(ctx):
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    embed = _build_warnlist_embed(ctx.guild)
    await ctx.send(embed=embed, view=WarnListView())
    await ctx.message.delete()
