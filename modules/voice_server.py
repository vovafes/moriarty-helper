"""
Voice & server utilities:

* AFK room    -- members who sit muted AND deafened in voice for N minutes are
                 moved to an AFK channel.
* Server stats -- channel names that show live numbers ("👥 Участников: 128"),
                 refreshed every 10 minutes (Discord limits renames).
"""

import time

import discord
from discord.ext import commands, tasks

from core import modules

AFK_ROOM = modules.register(modules.Module(
    key="afk_room", title="АФК-комната", icon="😴", category="moderation", default_enabled=False,
    description="Переносит в АФК-канал тех, кто долго сидит в голосовом с выключенными микрофоном и звуком.",
    fields=[
        {"key": "afk_channel", "label": "АФК-канал", "type": "channel", "kind": "voice", "default": None},
        {"key": "minutes", "label": "Через сколько минут бездействия", "type": "number", "default": 15, "min": 1, "max": 600},
        {"key": "ignored_channels", "label": "Не трогать в этих каналах", "type": "channels", "kind": "voice", "default": []},
    ],
))

STATS_FIELDS = []
for key, label, default in (("members", "Всего участников", "👥 Участников: {count}"), ("humans", "Людей (без ботов)", "🧑 Людей: {count}"),
                            ("bots", "Ботов", "🤖 Ботов: {count}"), ("online", "Онлайн", "🟢 Онлайн: {count}"),
                            ("boosts", "Бустов", "💎 Бустов: {count}")):
    STATS_FIELDS += [
        {"key": f"{key}_channel", "label": f"Канал: {label}", "type": "channel", "default": None, "group": label},
        {"key": f"{key}_template", "label": "Шаблон названия", "type": "text", "default": default, "group": label, "help": "{count} — число"},
    ]
SERVER_STATS = modules.register(modules.Module(
    key="server_stats", title="Статистика в каналах", icon="📈", category="community", default_enabled=False,
    description="Названия каналов показывают живые цифры сервера. Лучше использовать голосовые каналы с запретом входа.",
    fields=STATS_FIELDS,
))

STAT_KEYS = ("members", "humans", "bots", "online", "boosts")


def is_idle(voice_state) -> bool:
    return bool(voice_state and voice_state.channel and not voice_state.afk and voice_state.self_mute and voice_state.self_deaf)


def idle_expired(since: dict, key, idle_now: bool, now: float, minutes: int) -> bool:
    """Track continuous idleness per key; True once it has lasted `minutes`."""
    if not idle_now:
        since.pop(key, None)
        return False
    start = since.setdefault(key, now)
    return now - start >= minutes * 60


def compute_stats(guild) -> dict[str, int]:
    members = list(guild.members)
    return {"members": guild.member_count or len(members), "humans": sum(1 for m in members if not m.bot),
            "bots": sum(1 for m in members if m.bot),
            "online": sum(1 for m in members if not m.bot and getattr(m, "status", None) not in (None, discord.Status.offline)),
            "boosts": guild.premium_subscription_count or 0}


def render_name(template: str, count: int) -> str:
    return (template or "{count}").replace("{count}", str(count))[:100]


class VoiceServer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._idle: dict[tuple[int, int], float] = {}

    async def cog_load(self):
        self.afk_loop.start()
        self.stats_loop.start()

    async def cog_unload(self):
        self.afk_loop.cancel()
        self.stats_loop.cancel()

    @tasks.loop(seconds=60)
    async def afk_loop(self):
        now = time.monotonic()
        for guild in self.bot.guilds:
            cfg = modules.get_config(guild.id, "afk_room")
            target = guild.get_channel(cfg["afk_channel"]) if cfg["enabled"] and cfg["afk_channel"] else None
            if target is None:
                continue
            for vc in guild.voice_channels:
                for m in vc.members:
                    key = (guild.id, m.id)
                    skip = m.bot or vc.id == target.id or vc.id in (cfg["ignored_channels"] or [])
                    if idle_expired(self._idle, key, (not skip) and is_idle(m.voice), now, cfg["minutes"]):
                        self._idle.pop(key, None)
                        try:
                            await m.move_to(target, reason="АФК-комната")
                        except (discord.Forbidden, discord.HTTPException):
                            pass
            for key in [k for k in self._idle if k[0] == guild.id and guild.get_member(k[1]) is None]:
                self._idle.pop(key, None)                       # left the server

    async def update_stats(self, guild: discord.Guild) -> int:
        cfg = modules.get_config(guild.id, "server_stats")
        if not cfg["enabled"]:
            return 0
        stats = compute_stats(guild)
        changed = 0
        for k in STAT_KEYS:
            ch = guild.get_channel(cfg[f"{k}_channel"]) if cfg[f"{k}_channel"] else None
            if ch is None:
                continue
            name = render_name(cfg[f"{k}_template"], stats[k])
            if ch.name != name:                                 # renaming is rate-limited: only when it changed
                try:
                    await ch.edit(name=name, reason="Статистика сервера")
                    changed += 1
                except (discord.Forbidden, discord.HTTPException):
                    pass
        return changed

    @tasks.loop(minutes=10)
    async def stats_loop(self):
        for guild in self.bot.guilds:
            await self.update_stats(guild)

    @afk_loop.before_loop
    @stats_loop.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @afk_loop.error
    async def _afk_err(self, error):
        print(f"WARNING: afk_room loop crashed, restarting: {error}")
        if not self.afk_loop.is_running():
            self.afk_loop.start()

    @stats_loop.error
    async def _stats_err(self, error):
        print(f"WARNING: server_stats loop crashed, restarting: {error}")
        if not self.stats_loop.is_running():
            self.stats_loop.start()


async def _stats_now(ctx, p):
    n = await VoiceServer(ctx.bot).update_stats(ctx.guild)
    return f"Обновлено каналов: {n}"


SERVER_STATS.actions.append(modules.Action("now", "Обновить сейчас", _stats_now, description="Бот и сам обновляет раз в 10 минут"))


async def setup(bot):
    await bot.add_cog(VoiceServer(bot))
