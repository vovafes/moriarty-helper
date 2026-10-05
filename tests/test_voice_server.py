"""Run: venv/bin/python -m tests.test_voice_server"""
import asyncio, tempfile
from pathlib import Path
from types import SimpleNamespace as NS

import discord
from core import db, modules
db.close(); db.init(Path(tempfile.mkdtemp()) / "t.sqlite")
from modules import voice_server as vs

VS = lambda **kw: NS(**{"channel": NS(id=1), "afk": False, "self_mute": True, "self_deaf": True, **kw})
assert vs.is_idle(VS()) and not vs.is_idle(VS(self_mute=False)) and not vs.is_idle(VS(self_deaf=False))
assert not vs.is_idle(VS(afk=True)) and not vs.is_idle(VS(channel=None)) and not vs.is_idle(None)
since = {}
assert [vs.idle_expired(since, "a", True, t, 1) for t in (0, 30, 59, 60, 61)] == [False, False, False, True, True]
assert not vs.idle_expired(since, "a", False, 100, 1) and "a" not in since                       # activity resets the timer
assert not vs.idle_expired(since, "a", True, 101, 1)                                              # ...and it starts over
assert vs.render_name("👥 Участников: {count}", 128) == "👥 Участников: 128" and len(vs.render_name("x" * 200, 1)) == 100

class Member:
    def __init__(self, uid, voice, bot=False, status=discord.Status.online):
        self.id, self.voice, self.bot, self.status, self.moved = uid, voice, bot, status, []
    async def move_to(self, ch, reason=None): self.moved.append(ch.id)
class VC:
    def __init__(self, i, members): self.id, self.members = i, members
afk = VC(9, [])
idle, active, bot_m, ignored = Member(1, VS()), Member(2, VS(self_mute=False)), Member(3, VS(), bot=True), Member(4, VS())
rooms = [VC(1, [idle, active, bot_m]), VC(2, [ignored]), afk]
class Chan:
    def __init__(self, i, name): self.id, self.name, self.edits = i, name, []
    async def edit(self, name=None, reason=None): self.name = name; self.edits.append(name)
stat_ch = {20: Chan(20, "old"), 21: Chan(21, "🧑 Людей: 2")}
allm = [Member(1, None), Member(2, None), Member(3, None, bot=True), Member(4, None, status=discord.Status.offline)]
guild = NS(id=1, voice_channels=rooms, members=allm, member_count=4, premium_subscription_count=None,
           get_channel=lambda i: afk if i == 9 else stat_ch.get(i), get_member=lambda i: next((m for m in allm if m.id == i), None))

async def main():
    cog = vs.VoiceServer(NS(guilds=[guild]))
    now = [0.0]
    import time; real = time.monotonic; vs.time.monotonic = lambda: now[0]
    try:
        await vs.VoiceServer.afk_loop.coro(cog); assert idle.moved == []                          # module off
        modules.set_config(1, "afk_room", {"enabled": True, "afk_channel": 9, "minutes": 2, "ignored_channels": [2]})
        await vs.VoiceServer.afk_loop.coro(cog); now[0] = 119; await vs.VoiceServer.afk_loop.coro(cog); assert idle.moved == []
        now[0] = 121; await vs.VoiceServer.afk_loop.coro(cog)
        assert idle.moved == [9] and active.moved == [] and bot_m.moved == [] and ignored.moved == []
        now[0] = 300; await vs.VoiceServer.afk_loop.coro(cog); assert idle.moved == [9]            # timer restarted after the move
    finally:
        vs.time.monotonic = real

    assert vs.compute_stats(guild) == {"members": 4, "humans": 3, "bots": 1, "online": 2, "boosts": 0}
    assert await cog.update_stats(guild) == 0                                                      # disabled
    modules.set_config(1, "server_stats", {"enabled": True, "humans_channel": 21, "members_channel": 20, "bots_channel": 777})
    assert await cog.update_stats(guild) == 2 and stat_ch[20].name == "👥 Участников: 4" and stat_ch[21].name == "🧑 Людей: 3"
    assert await cog.update_stats(guild) == 0 and len(stat_ch[20].edits) == 1                      # unchanged -> no rename (rate limits)
    ctx = modules.ActionContext(guild=guild, bot=NS(), user_id=1, user_name="a")
    guild.premium_subscription_count = 5; modules.set_config(1, "server_stats", {"boosts_channel": 20, "members_channel": None})
    assert "1" in await modules.run_action("server_stats", "now", {}, ctx) and stat_ch[20].name == "💎 Бустов: 5"
    print("voice_server tests OK")

asyncio.run(main())
