"""Auto Role -- roles given to every new member (separate lists for people and bots)."""

import asyncio

import discord
from discord.ext import commands

from core import modules

modules.register(modules.Module(
    key="autorole", title="Авто-роли", icon="🎭", category="moderation",
    description="Автоматически выдаёт роли новым участникам и ботам.",
    fields=[
        {"key": "member_roles", "label": "Роли для участников", "type": "roles", "default": []},
        {"key": "bot_roles", "label": "Роли для ботов", "type": "roles", "default": []},
        {"key": "delay_seconds", "label": "Задержка, сек", "type": "number", "default": 0, "min": 0, "max": 3600,
         "help": "Подождать перед выдачей (например, пока человек читает правила)"},
    ],
))


class AutoRole(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        cfg = modules.get_config(member.guild.id, "autorole")
        if not cfg["enabled"]:
            return
        ids = cfg["bot_roles"] if member.bot else cfg["member_roles"]
        if cfg["delay_seconds"]:
            await asyncio.sleep(cfg["delay_seconds"])
            member = member.guild.get_member(member.id)          # may have left meanwhile
            if member is None:
                return
        roles = [r for r in (member.guild.get_role(i) for i in ids or []) if r and not r.managed]
        if roles:
            try:
                await member.add_roles(*roles, reason="Авто-роль")
            except discord.Forbidden:
                pass


async def setup(bot):
    await bot.add_cog(AutoRole(bot))
