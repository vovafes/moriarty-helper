"""
Verification -- keep newcomers out until they pass a check.
Modes: a plain button, or a small math question. Optional minimum account age.
New members get the "unverified" role on join; passing the check swaps it for
the "verified" role. Off by default.
"""

import datetime
import random

import discord
from discord import ui
from discord.ext import commands

from core import embeds, modules

MODULE = modules.register(modules.Module(
    key="verification", title="Верификация", icon="✅", category="moderation", default_enabled=False,
    description="Проверка новичков кнопкой или математической задачей, с ограничением по возрасту аккаунта.",
    fields=[
        {"key": "mode", "label": "Способ проверки", "type": "select", "default": "button", "group": "Проверка",
         "options": [{"value": "button", "label": "Кнопка"}, {"value": "math", "label": "Математическая задача"}]},
        {"key": "verified_role", "label": "Роль после проверки", "type": "role", "default": None, "group": "Роли",
         "help": "Выдаётся при успешной верификации"},
        {"key": "unverified_role", "label": "Роль до проверки", "type": "role", "default": None, "group": "Роли",
         "help": "Выдаётся при входе и снимается после проверки (необязательно)"},
        {"key": "min_account_age_days", "label": "Минимальный возраст аккаунта, дней", "type": "number", "default": 0,
         "min": 0, "max": 365, "group": "Проверка", "help": "0 — без ограничения"},
        {"key": "log_channel", "label": "Канал логов", "type": "channel", "kind": "text", "default": None, "group": "Логи"},
        {"key": "panel_title", "label": "Заголовок панели", "type": "text", "default": "Верификация", "group": "Панель"},
        {"key": "panel_text", "label": "Текст панели", "type": "longtext", "group": "Панель",
         "default": "Нажмите кнопку ниже, чтобы подтвердить, что вы человек, и получить доступ к серверу."},
    ],
))


def make_math(rng=random) -> tuple[str, int]:
    a, b = rng.randint(2, 20), rng.randint(2, 20)
    if rng.random() < 0.5:
        return f"{a} + {b}", a + b
    a, b = max(a, b), min(a, b)
    return f"{a} − {b}", a - b


def old_enough(created_at: datetime.datetime, days: int, now: datetime.datetime | None = None) -> bool:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return days <= 0 or (now - created_at) >= datetime.timedelta(days=days)


async def grant(member: discord.Member, cfg: dict) -> str | None:
    """Swap roles. Returns an error text or None."""
    guild = member.guild
    verified, unverified = guild.get_role(cfg["verified_role"]), guild.get_role(cfg["unverified_role"] or 0)
    if verified is None:
        return "Роль для верифицированных не настроена — сообщите администрации."
    try:
        await member.add_roles(verified, reason="Верификация пройдена")
        if unverified and unverified in member.roles:
            await member.remove_roles(unverified, reason="Верификация пройдена")
    except discord.Forbidden:
        return "У бота нет прав выдать роль (роль бота должна быть выше)."
    ch = guild.get_channel(cfg["log_channel"]) if cfg["log_channel"] else None
    if ch:
        try:
            await ch.send(embed=embeds.make("✅ Верификация пройдена", f"{member.mention} (`{member.id}`)", 0x3BA55D),
                          allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            pass
    return None


class MathModal(ui.Modal, title="Проверка"):
    def __init__(self, question: str, answer: int, cfg: dict):
        super().__init__()
        self.answer, self.cfg = answer, cfg
        self.field = ui.TextInput(label=f"Сколько будет {question}?", max_length=6)
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            ok = int(str(self.field.value).strip()) == self.answer
        except ValueError:
            ok = False
        if not ok:
            return await interaction.response.send_message("❌ Неверно, нажмите кнопку и попробуйте ещё раз.", ephemeral=True)
        err = await grant(interaction.user, modules.get_config(interaction.guild_id, "verification"))
        await interaction.response.send_message(f"❌ {err}" if err else "✅ Готово, добро пожаловать!", ephemeral=True)


class VerifyView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Пройти проверку", emoji="✅", style=discord.ButtonStyle.success, custom_id="verify:start")
    async def start(self, interaction: discord.Interaction, button: ui.Button):
        cfg = modules.get_config(interaction.guild_id, "verification")
        member = interaction.user
        if not cfg["enabled"]:
            return await interaction.response.send_message("Верификация сейчас выключена.", ephemeral=True)
        if cfg["verified_role"] and any(r.id == cfg["verified_role"] for r in member.roles):
            return await interaction.response.send_message("Вы уже прошли проверку.", ephemeral=True)
        if not old_enough(member.created_at, cfg["min_account_age_days"]):
            return await interaction.response.send_message(
                f"Ваш аккаунт слишком новый — нужно минимум {cfg['min_account_age_days']} дн.", ephemeral=True)
        if cfg["mode"] == "math":
            q, a = make_math()
            return await interaction.response.send_modal(MathModal(q, a, cfg))
        err = await grant(member, cfg)
        await interaction.response.send_message(f"❌ {err}" if err else "✅ Готово, добро пожаловать!", ephemeral=True)


class Verification(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(VerifyView())

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        cfg = modules.get_config(member.guild.id, "verification")
        if not cfg["enabled"] or member.bot or not cfg["unverified_role"]:
            return
        role = member.guild.get_role(cfg["unverified_role"])
        if role:
            try:
                await member.add_roles(role, reason="Ожидает верификации")
            except discord.Forbidden:
                pass


async def _publish(ctx, p):
    cfg = modules.get_config(ctx.guild.id, "verification")
    if not cfg["verified_role"]:
        raise modules.ValidationError("Сначала выберите роль для верифицированных и сохраните")
    ch = ctx.guild.get_channel(p["channel"])
    if ch is None or not hasattr(ch, "send"):
        raise modules.ValidationError("Выберите текстовый канал")
    try:
        await ch.send(embed=embeds.make(cfg["panel_title"], cfg["panel_text"], 0x3BA55D), view=VerifyView())
    except discord.Forbidden:
        raise modules.ValidationError("У бота нет прав писать в этот канал")
    return f"Панель опубликована в #{ch.name}"


MODULE.actions.append(modules.Action("publish", "Опубликовать панель", _publish, params=[
    {"key": "channel", "label": "Канал", "type": "channel", "kind": "text"}]))


async def setup(bot):
    await bot.add_cog(Verification(bot))
