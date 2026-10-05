"""obshak -- split out of main.py."""

from datetime import datetime, timedelta

import discord
from discord import app_commands, ui
from legacy.app import bot, tree
from legacy.helpers import (
    _footer,
    format_amount,
    is_admin,
    is_admin_ctx,
)
from legacy.state import (
    obshak_deposits,
    obshak_log_channels,
    obshak_panels,
    obshak_ping_roles,
    save_data,
    save_obshak,
)


DEFAULT_OBSHAK_TEXT = "Система пополнения общака семьи."


def build_obshak_embed(guild_id: int) -> discord.Embed:
    settings = obshak_panels.get(guild_id, {})
    text      = settings.get("text") or DEFAULT_OBSHAK_TEXT
    image_url = settings.get("image_url")

    embed = discord.Embed(
        title="💰 ОБЩАК СЕМЬИ",
        description=text,
        color=0xf1c40f,
    )
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
    return embed


async def _refresh_obshak_panel(guild: discord.Guild):
    settings = obshak_panels.get(guild.id)
    if not settings or not settings.get("message_id"):
        return
    try:
        ch  = guild.get_channel(settings["channel_id"])
        msg = await ch.fetch_message(settings["message_id"])
        await msg.edit(embed=build_obshak_embed(guild.id), view=ObshakView())
    except Exception:
        pass


class ObshakDepositModal(ui.Modal, title="Пополнение общака"):
    amount_input = ui.TextInput(
        label="Сумма пополнения ($)",
        placeholder="50000",
        min_length=1,
        max_length=12,
    )

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.amount_input.value.strip().replace(".", "").replace(",", "").replace(" ", "")
        if not raw.isdigit():
            return await interaction.response.send_message("❌ Введите числовое значение.", ephemeral=True)
        amount = int(raw)
        if amount <= 0:
            return await interaction.response.send_message("❌ Сумма должна быть больше 0.", ephemeral=True)
        if amount > 100_000_000:
            return await interaction.response.send_message(
                "❌ Максимальная сумма одного взноса — **100.000.000$**.", ephemeral=True
            )

        guild_id = interaction.guild_id
        if guild_id not in obshak_deposits:
            obshak_deposits[guild_id] = []

        obshak_deposits[guild_id].append({
            "user_id": interaction.user.id,
            "amount":  amount,
            "date":    datetime.now().isoformat(),
        })
        save_obshak()

        await _refresh_obshak_panel(interaction.guild)

        log_ch_id = obshak_log_channels.get(guild_id)
        if log_ch_id:
            log_ch = interaction.guild.get_channel(log_ch_id)
            if log_ch:
                try:
                    ping_role_id = obshak_ping_roles.get(guild_id)
                    ping_str = f"<@&{ping_role_id}>\n" if ping_role_id else ""
                    await log_ch.send(
                        f"{ping_str}[+{format_amount(amount)}$] {interaction.user.mention} пополнил баланс организации"
                    )
                except Exception:
                    pass

        await interaction.response.send_message(
            f"✅ Пополнение на **{format_amount(amount)}$** зафиксировано!",
            ephemeral=True,
        )


class ObshakView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="💳 Пополнить", style=discord.ButtonStyle.success, custom_id="obshak_deposit")
    async def deposit_btn(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(ObshakDepositModal())


@bot.command(name="общак")
async def obshak_panel_cmd(ctx):
    """!общак — создать/обновить панель общака в этом канале"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()

    guild_id = ctx.guild.id

    # Удалить старую панель если есть
    existing = obshak_panels.get(guild_id)
    if existing and existing.get("message_id"):
        try:
            old_ch  = ctx.guild.get_channel(existing["channel_id"])
            old_msg = await old_ch.fetch_message(existing["message_id"])
            await old_msg.delete()
        except Exception:
            pass

    # Сохраняем text/image_url если они уже были
    prev = obshak_panels.get(guild_id, {})
    embed = build_obshak_embed(guild_id)
    msg   = await ctx.send(embed=embed, view=ObshakView())

    obshak_panels[guild_id] = {
        "channel_id": ctx.channel.id,
        "message_id": msg.id,
        "text":       prev.get("text"),
        "image_url":  prev.get("image_url"),
    }
    save_data()
    try:
        await ctx.message.delete()
    except Exception:
        pass


@tree.command(name="общак_фото", description="Изменить фото панели общака")
@app_commands.describe(url="Ссылка на изображение")
async def slash_obshak_photo(interaction: discord.Interaction, url: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid = interaction.guild_id
    if gid not in obshak_panels:
        obshak_panels[gid] = {}
    obshak_panels[gid]["image_url"] = url
    save_data()
    await _refresh_obshak_panel(interaction.guild)
    await interaction.response.send_message("✅ Фото панели обновлено!", ephemeral=True)


@tree.command(name="общак_текст", description="Изменить текст описания панели общака")
@app_commands.describe(текст="Текст под заголовком")
async def slash_obshak_text(interaction: discord.Interaction, текст: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid = interaction.guild_id
    if gid not in obshak_panels:
        obshak_panels[gid] = {}
    obshak_panels[gid]["text"] = текст
    save_data()
    await _refresh_obshak_panel(interaction.guild)
    await interaction.response.send_message("✅ Текст панели обновлён!", ephemeral=True)


@tree.command(name="общак_логи", description="Канал для логов пополнений общака")
@app_commands.describe(канал="Текстовый канал")
async def slash_obshak_logs(interaction: discord.Interaction, канал: discord.TextChannel):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    obshak_log_channels[interaction.guild_id] = канал.id
    save_data()
    await interaction.response.send_message(
        f"✅ Логи пополнений общака будут отправляться в {канал.mention}.", ephemeral=True
    )


@tree.command(name="общак_пинг", description="Роль для тега в логах пополнений общака")
@app_commands.describe(роль="Роль которую тегать при каждом пополнении (None — убрать тег)")
async def slash_obshak_ping(interaction: discord.Interaction, роль: discord.Role = None):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    if роль:
        obshak_ping_roles[interaction.guild_id] = роль.id
        save_data()
        await interaction.response.send_message(
            f"✅ При каждом пополнении будет тегаться {роль.mention}.", ephemeral=True
        )
    else:
        obshak_ping_roles.pop(interaction.guild_id, None)
        save_data()
        await interaction.response.send_message("✅ Тег роли в логах общака убран.", ephemeral=True)


def _build_obshak_stats_embed(
    guild_id: int,
    deposits: list,
    title: str,
    period_label: str,
) -> discord.Embed:
    totals: dict[int, int] = {}
    for d in deposits:
        uid = d["user_id"]
        totals[uid] = totals.get(uid, 0) + d["amount"]

    sorted_users = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    total_sum = sum(totals.values())

    embed = discord.Embed(
        title=title,
        description=f"**Период:** {period_label}\n**Итого:** {format_amount(total_sum)}$\n\u200b",
        color=0xf1c40f,
        timestamp=datetime.now(),
    )

    if not sorted_users:
        embed.add_field(name="—", value="Пополнений нет.", inline=False)
    else:
        lines = [
            f"**{i}.** <@{uid}> — **{format_amount(amt)}$**"
            for i, (uid, amt) in enumerate(sorted_users, 1)
        ]
        # Разбиваем на chunks если текст больше 1024 символов
        chunk: list[str] = []
        chunks: list[str] = []
        for line in lines:
            if sum(len(l) + 1 for l in chunk) + len(line) > 1000:
                chunks.append("\n".join(chunk))
                chunk = [line]
            else:
                chunk.append(line)
        if chunk:
            chunks.append("\n".join(chunk))
        for idx, text in enumerate(chunks):
            embed.add_field(
                name="Участники" if idx == 0 else "\u200b",
                value=text,
                inline=False,
            )

    embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
    return embed


@tree.command(name="общак_неделя", description="Статистика пополнений общака за текущую неделю")
async def slash_obshak_week(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    now        = datetime.now()
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end   = week_start + timedelta(days=6)
    deposits   = [
        d for d in obshak_deposits.get(interaction.guild_id, [])
        if datetime.fromisoformat(d["date"]) >= week_start
    ]
    label = f"{week_start.strftime('%d.%m.%Y')} — {week_end.strftime('%d.%m.%Y')}"
    embed = _build_obshak_stats_embed(interaction.guild_id, deposits, "📊 Общак — Неделя", label)
    await interaction.response.send_message(embed=embed)


@tree.command(name="общак_месяц", description="Статистика пополнений общака за текущий месяц")
async def slash_obshak_month(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    import calendar
    now         = datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_day    = calendar.monthrange(now.year, now.month)[1]
    month_end   = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=0)
    deposits    = [
        d for d in obshak_deposits.get(interaction.guild_id, [])
        if datetime.fromisoformat(d["date"]) >= month_start
    ]
    label = f"{month_start.strftime('%d.%m.%Y')} — {month_end.strftime('%d.%m.%Y')}"
    embed = _build_obshak_stats_embed(interaction.guild_id, deposits, "📊 Общак — Месяц", label)
    await interaction.response.send_message(embed=embed)


@tree.command(name="общак_полный", description="Полная статистика пополнений общака за всё время")
async def slash_obshak_all(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    deposits = obshak_deposits.get(interaction.guild_id, [])
    embed = _build_obshak_stats_embed(
        interaction.guild_id, deposits,
        "📊 Общак — Всё время", "С начала ведения учёта",
    )
    await interaction.response.send_message(embed=embed)
