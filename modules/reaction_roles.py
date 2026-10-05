"""
Reaction Roles -- self-service role panels. One message with a button per
role; "toggle" lets people pick any number, "unique" keeps at most one role
from the panel. Buttons are discord.py DynamicItems, so a single handler
serves every panel and they survive restarts.
"""

import json
import re

import discord
from discord import ui
from discord.ext import commands

from core import db, embeds, modules

db.register_schema("""
CREATE TABLE IF NOT EXISTS rr_panels (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    title      TEXT,
    mode       TEXT NOT NULL DEFAULT 'toggle',
    roles      TEXT NOT NULL
);
""")

MODULE = modules.register(modules.Module(
    key="reaction_roles", title="Роли по кнопкам", icon="🏷", category="moderation", default_enabled=True,
    description="Панели, где участники сами берут и снимают роли кнопками.",
    fields=[],
    tables=[{"id": "panels", "title": "Панели", "columns": [
        {"key": "id", "label": "ID"}, {"key": "title", "label": "Заголовок"}, {"key": "mode", "label": "Режим"},
        {"key": "roles_text", "label": "Роли"}, {"key": "channel", "label": "Канал"}]}],
))


def create_panel(guild_id, channel_id, title, mode, role_ids) -> int:
    return db.execute("INSERT INTO rr_panels (guild_id, channel_id, title, mode, roles) VALUES (?,?,?,?,?)",
                      (guild_id, channel_id, title, mode, json.dumps(role_ids))).lastrowid


def get_panel(panel_id: int, guild_id: int | None = None) -> dict | None:
    q, p = "SELECT * FROM rr_panels WHERE id=?", [panel_id]
    if guild_id is not None:
        q += " AND guild_id=?"; p.append(guild_id)
    rows = db.query(q, tuple(p))
    if not rows:
        return None
    d = dict(rows[0]); d["roles"] = json.loads(d["roles"])
    return d


def decide(mode: str, panel_roles: list[int], member_roles: set[int], clicked: int) -> tuple[list[int], list[int]]:
    """(roles to add, roles to remove) for a click. Pure so it's easy to test."""
    if clicked in member_roles:
        return [], [clicked]
    if mode == "unique":
        return [clicked], [r for r in panel_roles if r in member_roles and r != clicked]
    return [clicked], []


class RoleButton(ui.DynamicItem[ui.Button], template=r"rr:(?P<panel>\d+):(?P<role>\d+)"):
    def __init__(self, panel_id: int, role_id: int, label: str = "роль"):
        super().__init__(ui.Button(label=label[:80], style=discord.ButtonStyle.secondary, custom_id=f"rr:{panel_id}:{role_id}"))
        self.panel_id, self.role_id = panel_id, role_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["panel"]), int(match["role"]))

    async def callback(self, interaction: discord.Interaction):
        if not modules.is_enabled(interaction.guild_id, "reaction_roles"):
            return await interaction.response.send_message("Панель ролей выключена.", ephemeral=True)
        panel = await db.run(get_panel, self.panel_id, interaction.guild_id)
        role = interaction.guild.get_role(self.role_id)
        if panel is None or role is None or self.role_id not in panel["roles"]:
            return await interaction.response.send_message("Эта роль больше недоступна.", ephemeral=True)
        member = interaction.user
        add, remove = decide(panel["mode"], panel["roles"], {r.id for r in member.roles}, self.role_id)
        try:
            if remove:
                await member.remove_roles(*[r for r in (interaction.guild.get_role(i) for i in remove) if r], reason="Роли по кнопкам")
            if add:
                await member.add_roles(role, reason="Роли по кнопкам")
        except discord.Forbidden:
            return await interaction.response.send_message("❌ У бота нет прав выдать эту роль (она выше роли бота).", ephemeral=True)
        await interaction.response.send_message(f"{'✅ Выдана' if add else '➖ Снята'} роль **{role.name}**", ephemeral=True)


def build_view(panel_id: int, roles: list[discord.Role]) -> ui.View:
    view = ui.View(timeout=None)
    for i, r in enumerate(roles[:25]):
        item = RoleButton(panel_id, r.id, r.name)
        item.item.row = i // 5
        view.add_item(item)
    return view


class ReactionRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_dynamic_items(RoleButton)


def _table(guild):
    out = []
    for r in db.query("SELECT * FROM rr_panels WHERE guild_id=? ORDER BY id DESC", (guild.id,)):
        ids = json.loads(r["roles"])
        ch = guild.get_channel(r["channel_id"])
        out.append({"id": r["id"], "title": r["title"], "mode": "одна роль" if r["mode"] == "unique" else "любые",
                    "roles_text": ", ".join(getattr(guild.get_role(i), "name", f"[{i}]") for i in ids),
                    "channel": f"#{ch.name}" if ch else "—"})
    return out


modules.register_table("reaction_roles", "panels", _table)


async def _create(ctx, p):
    ch = ctx.guild.get_channel(p["channel"])
    if ch is None or not hasattr(ch, "send"):
        raise modules.ValidationError("Выберите текстовый канал")
    roles = [r for r in (ctx.guild.get_role(i) for i in p["roles"]) if r]
    if not roles or len(roles) > 25:
        raise modules.ValidationError("Выберите от 1 до 25 ролей")
    if any(r.managed or r.is_default() for r in roles):
        raise modules.ValidationError("Нельзя использовать служебные роли (интеграций и @everyone)")
    pid = await db.run(create_panel, ctx.guild.id, ch.id, p["title"], p["mode"], [r.id for r in roles])
    e = embeds.make(p["title"], p.get("text") or "Нажмите на кнопку, чтобы получить или снять роль.", 0x5865F2)
    try:
        msg = await ch.send(embed=e, view=build_view(pid, roles))
    except discord.Forbidden:
        await db.run(db.execute, "DELETE FROM rr_panels WHERE id=?", (pid,))
        raise modules.ValidationError("У бота нет прав писать в этот канал")
    await db.run(db.execute, "UPDATE rr_panels SET message_id=? WHERE id=?", (msg.id, pid))
    return f"Панель #{pid} опубликована в #{ch.name}"


async def _delete(ctx, p):
    panel = await db.run(get_panel, int(p["id"]), ctx.guild.id)
    if panel is None:
        raise modules.ValidationError("Панель с таким ID не найдена")
    ch = ctx.guild.get_channel(panel["channel_id"])
    if ch and panel["message_id"]:
        try:
            await (await ch.fetch_message(panel["message_id"])).delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
    await db.run(db.execute, "DELETE FROM rr_panels WHERE id=?", (panel["id"],))
    return "Панель удалена"


MODULE.actions.extend([
    modules.Action("create", "Создать панель", _create, params=[
        {"key": "channel", "label": "Канал", "type": "channel", "kind": "text"},
        {"key": "title", "label": "Заголовок", "type": "text", "default": "Выберите роли"},
        {"key": "text", "label": "Описание", "type": "longtext", "required": False},
        {"key": "roles", "label": "Роли (до 25)", "type": "roles"},
        {"key": "mode", "label": "Режим", "type": "select", "default": "toggle",
         "options": [{"value": "toggle", "label": "Любое количество ролей"}, {"value": "unique", "label": "Только одна роль из панели"}]}]),
    modules.Action("delete", "Удалить панель", _delete, params=[{"key": "id", "label": "ID панели", "type": "number", "min": 1}], danger=True),
])


async def setup(bot):
    await bot.add_cog(ReactionRoles(bot))
