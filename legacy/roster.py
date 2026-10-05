"""roster -- split out of main.py."""

from datetime import datetime

import discord
from discord import app_commands, ui
from legacy.app import tree
from legacy.helpers import (
    _footer,
    is_admin,
)
from legacy.state import (
    roster_settings,
    save_data,
)


# Все фракции: ключ = короткое имя (= имя кастомного эмодзи на сервере)
def _chunk_lines(lines: list, chunk_size: int = 10) -> list[list]:
    if not lines:
        return [[]]
    return [lines[i:i+chunk_size] for i in range(0, len(lines), chunk_size)]


async def _collect_roster_lines(guild: discord.Guild):
    """Собирает (full_lines, academy_lines) по ролям."""
    cfg             = roster_settings.get(guild.id, {})
    member_role_id  = cfg.get("member_role_id")
    academy_role_id = cfg.get("academy_role_id")

    members_list = guild.members
    if len(members_list) < 2:
        try:
            members_list = [m async for m in guild.fetch_members(limit=None)]
        except Exception:
            members_list = guild.members

    full_lines    = []
    academy_lines = []

    for member in members_list:
        if member.bot:
            continue
        role_ids    = {r.id for r in member.roles}
        has_member  = member_role_id  and (member_role_id  in role_ids)
        has_academy = academy_role_id and (academy_role_id in role_ids)
        if not has_member and not has_academy:
            continue

        line = f"• {member.mention}"

        if has_academy and not has_member:
            academy_lines.append(line)
        else:
            full_lines.append(line)

    return full_lines, academy_lines


async def _refresh_roster(guild: discord.Guild):
    cfg   = roster_settings.get(guild.id, {})
    ch_id = cfg.get("channel_id")
    if not ch_id:
        return
    try:
        ch = guild.get_channel(ch_id)

        # Удаляем старое сообщение
        old_id = cfg.get("message_id")
        if old_id:
            try:
                await (await ch.fetch_message(old_id)).delete()
            except Exception:
                pass

        full_lines, academy_lines = await _collect_roster_lines(guild)
        view = RosterPaginationView(guild, full_lines, academy_lines)
        msg  = await ch.send(embed=view.current_embed(), view=view)

        cfg["message_id"] = msg.id
        save_data()
    except Exception:
        pass


class RosterPaginationView(ui.View):
    """Одно сообщение: кнопки ◀ Назад / Вперёд ▶ + счётчик страниц."""

    PAGE = 10

    def __init__(self, guild: discord.Guild, full_lines: list, academy_lines: list, idx: int = 0):
        super().__init__(timeout=None)
        self.guild         = guild
        self.full_lines    = full_lines
        self.academy_lines = academy_lines
        self.full_pages    = _chunk_lines(full_lines,    self.PAGE)
        self.acad_pages    = _chunk_lines(academy_lines, self.PAGE)
        self.total         = len(self.full_pages) + len(self.acad_pages)
        self.idx           = max(0, min(idx, self.total - 1))

        self._add_nav()

    def _add_nav(self):
        """Удаляет старые навигационные кнопки и добавляет новые."""
        for item in list(self.children):
            if isinstance(item, ui.Button):
                self.remove_item(item)

        fp = len(self.full_pages)
        ap = len(self.acad_pages)

        # Определяем подпись счётчика
        if self.idx < fp:
            if fp > 1:
                counter = f"{self.idx + 1} / {fp}  (Участники)"
            else:
                counter = "Участники"
        else:
            ai = self.idx - fp
            if ap > 1:
                counter = f"{ai + 1} / {ap}  (Академия)"
            else:
                counter = "Академия"

        prev_btn = ui.Button(
            label="◀ Назад",
            style=discord.ButtonStyle.secondary,
            disabled=self.idx == 0,
            row=0,
        )
        page_btn = ui.Button(
            label=counter,
            style=discord.ButtonStyle.primary,
            disabled=True,
            row=0,
        )
        next_btn = ui.Button(
            label="Вперёд ▶",
            style=discord.ButtonStyle.secondary,
            disabled=self.idx >= self.total - 1,
            row=0,
        )
        prev_btn.callback = self._go_prev
        next_btn.callback = self._go_next
        self.add_item(prev_btn)
        self.add_item(page_btn)
        self.add_item(next_btn)

    def current_embed(self) -> discord.Embed:
        footer_icon = _footer(self.guild.id)
        fp = len(self.full_pages)

        if self.idx < fp:
            page  = self.full_pages[self.idx]
            title = f"🏅 Участники  [{len(self.full_lines)}]"
            color = discord.Color.dark_gold()
            empty = "*Нет участников*"
        else:
            ai    = self.idx - fp
            page  = self.acad_pages[ai]
            title = f"🎓 Академия  [{len(self.academy_lines)}]"
            color = discord.Color.blue()
            empty = "*Нет академиков*"

        embed = discord.Embed(
            title=title,
            description="\n\n".join(page) if page else empty,
            color=color,
            timestamp=datetime.now(),
        )
        embed.set_footer(
            text=f"MORIARTY • Участников: {len(self.full_lines)} | Академиков: {len(self.academy_lines)}",
            icon_url=footer_icon,
        )
        return embed

    async def _go_prev(self, interaction: discord.Interaction):
        self.idx = max(0, self.idx - 1)
        self._add_nav()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def _go_next(self, interaction: discord.Interaction):
        self.idx = min(self.total - 1, self.idx + 1)
        self._add_nav()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)


@tree.command(name="состав_настройка", description="Настроить роли и канал для состава семьи")
@app_commands.describe(
    роль_участника="Роль полноценных участников (Moriarty)",
    роль_академии="Роль академиков (Moriarty Academy)",
    канал="Канал где будет жить состав",
)
@app_commands.default_permissions(administrator=True)
async def slash_roster_setup(
    interaction: discord.Interaction,
    роль_участника: discord.Role,
    роль_академии: discord.Role,
    канал: discord.TextChannel,
):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    gid = interaction.guild_id
    cfg = roster_settings.setdefault(gid, {})
    cfg["member_role_id"]  = роль_участника.id
    cfg["academy_role_id"] = роль_академии.id
    cfg["channel_id"]      = канал.id
    await _refresh_roster(interaction.guild)
    await interaction.followup.send(
        f"✅ Состав настроен! Панель отправлена в {канал.mention}", ephemeral=True
    )


@tree.command(name="состав", description="Показать состав семьи")
async def slash_roster(interaction: discord.Interaction):
    await interaction.response.defer()
    full_lines, academy_lines = await _collect_roster_lines(interaction.guild)
    view  = RosterPaginationView(interaction.guild, full_lines, academy_lines)
    embed = view.current_embed()
    await interaction.followup.send(embed=embed, view=view)
