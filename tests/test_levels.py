"""Run: venv/bin/python -m tests.test_levels"""
import asyncio, random, tempfile, time
from pathlib import Path
from types import SimpleNamespace as NS

from core import db, modules
db.close(); db.init(Path(tempfile.mkdtemp()) / "t.sqlite")
from modules import levels as lv

# curve
assert [lv.xp_to_next(l) for l in range(4)] == [100, 155, 220, 295]
assert lv.total_xp(0) == 0 and lv.total_xp(1) == 100 and lv.total_xp(3) == 475
assert [lv.level_for(x) for x in (0, 99, 100, 254, 255, 474, 475)] == [0, 0, 1, 1, 2, 2, 3]
assert lv.progress(130) == (1, 30, 155) and lv.progress(0) == (0, 0, 100)

# storage
assert lv.add_xp(1, 10, 50, message=True, now=1000) == (0, 0)
assert lv.add_xp(1, 10, 60, message=True, now=1100) == (0, 1) and lv.get_xp(1, 10) == (110, 2, 1100)
assert lv.add_xp(1, 10, 5, message=False, now=2000) == (1, 1) and lv.get_xp(1, 10) == (115, 2, 1100)   # voice xp doesn't reset the cooldown
assert lv.add_xp(1, 10, -9999) == (1, 0) and lv.get_xp(1, 10)[0] == 0                                    # never negative
lv.set_xp(1, 11, 500); lv.set_xp(1, 12, 500); lv.set_xp(1, 13, 10); lv.set_xp(2, 11, 9999)               # other guild
assert lv.rank_of(1, 11) == 1 and lv.rank_of(1, 12) == 1 and lv.rank_of(1, 13) == 3
assert [r["user_id"] for r in lv.top(1)][:2] in ([11, 12], [12, 11]) and lv.top(1)[2]["user_id"] == 13 and len(lv.top(1)) == 3

# reward roles
rw = [{"level": 5, "role_id": 50}, {"level": 10, "role_id": 100}, {"level": 20, "role_id": 200}]
assert lv.roles_for_level(rw, 12, True) == ([50, 100], [200])
assert lv.roles_for_level(rw, 12, False) == ([100], [50, 200])
assert lv.roles_for_level(rw, 2, True) == ([], [50, 100, 200])
assert lv.roles_for_level(rw, 99, False) == ([200], [50, 100])

class Role(NS):
    __hash__ = lambda s: hash(s.id)
    managed = False
    def is_default(self): return False
roles = {50: Role(id=50, name="Lv5"), 100: Role(id=100, name="Lv10")}
class Member:
    def __init__(self, uid, rs=()): self.id, self.bot, self.mention, self.display_name, self.roles = uid, False, f"<@{uid}>", f"u{uid}", list(rs)
    async def add_roles(self, *r, reason=None): self.roles += list(r)
    async def remove_roles(self, *r, reason=None): self.roles = [x for x in self.roles if x not in r]
class Chan:
    def __init__(self, i): self.id, self.sent = i, []
    async def send(self, text, **kw): self.sent.append(text)
c1, c2 = Chan(1), Chan(2)
guild = NS(id=1, get_role=roles.get, get_channel=lambda i: {1: c1, 2: c2}.get(i), voice_channels=[])
Member.guild = guild
def msg(member, ch=c1, bot=False): 
    member.bot = bot; return NS(guild=guild, author=member, channel=ch)

async def main():
    cog = lv.Levels(NS(guilds=[guild]))
    u = Member(7)
    await cog.on_message(msg(u)); assert lv.get_xp(1, 7)[0] == 0                              # module off by default
    modules.set_config(1, "levels", {"enabled": True, "xp_min": 100, "xp_max": 100, "cooldown": 60,
                                     "announce_text": "GG {user} lvl {level}"})
    await cog.on_message(msg(u)); assert lv.get_xp(1, 7)[0] == 100                          # exactly 100 -> level 1
    assert c1.sent == ["GG <@7> lvl 1"]
    await cog.on_message(msg(u)); assert lv.get_xp(1, 7)[0] == 100                          # cooldown
    await cog.on_message(msg(Member(8), bot=True)); assert lv.get_xp(1, 8)[0] == 0          # bots ignored
    modules.set_config(1, "levels", {"ignored_channels": [1]})
    await cog.on_message(msg(Member(9))); assert lv.get_xp(1, 9)[0] == 0                    # ignored channel
    modules.set_config(1, "levels", {"ignored_channels": [], "ignored_roles": [50]})
    await cog.on_message(msg(Member(9, rs=[roles[50]]))); assert lv.get_xp(1, 9)[0] == 0    # ignored role
    modules.set_config(1, "levels", {"ignored_roles": [], "announce": "channel", "announce_channel": 2})
    lv.set_xp(1, 20, 99); n1 = len(c1.sent)
    await cog.on_message(msg(Member(20))); assert c2.sent and len(c1.sent) == n1            # announced in the dedicated channel
    modules.set_config(1, "levels", {"announce": "off"}); n2 = len(c2.sent)
    lv.set_xp(1, 21, 99); await cog.on_message(msg(Member(21))); assert len(c2.sent) == n2 and lv.get_xp(1, 21)[0] == 199

    # rewards: granted on level-up, removed when XP is lowered
    ctx = modules.ActionContext(guild=guild, bot=NS(), user_id=1, user_name="a")
    assert "Lv5" in await modules.run_action("levels", "reward_add", {"level": 1, "role": 50}, ctx)
    await modules.run_action("levels", "reward_add", {"level": 3, "role": 100}, ctx)
    try: await modules.run_action("levels", "reward_add", {"level": 2, "role": 999}, ctx); assert False
    except modules.ValidationError: pass
    w = Member(30); guild.get_member = lambda i: w if i == 30 else None
    lv.set_xp(1, 30, 99); await cog.on_message(msg(w)); assert roles[50] in w.roles          # crossed level 1
    await modules.run_action("levels", "set_xp", {"user": 30, "xp": 475}, ctx); assert roles[100] in w.roles
    await modules.run_action("levels", "set_xp", {"user": 30, "xp": 0}, ctx); assert w.roles == []   # demoted -> roles taken away
    assert [r["level"] for r in modules.TABLE_PROVIDERS[("levels", "rewards")](guild)] == [1, 3]
    assert "удалена" in await modules.run_action("levels", "reward_remove", {"level": 1}, ctx)
    try: await modules.run_action("levels", "reward_remove", {"level": 1}, ctx); assert False
    except modules.ValidationError: pass

    # voice xp: needs company, skips fully-muted members and ignored channels
    a, b, d = Member(40), Member(41), Member(42)
    for m, vs in ((a, NS(self_mute=False, self_deaf=False, afk=False)), (b, NS(self_mute=True, self_deaf=True, afk=False)),
                  (d, NS(self_mute=False, self_deaf=False, afk=False))):
        m.voice = vs
    guild.voice_channels = [NS(id=60, members=[a, b, d]), NS(id=61, members=[Member(43)])]
    guild.voice_channels[1].members[0].voice = NS(self_mute=False, self_deaf=False, afk=False)
    modules.set_config(1, "levels", {"voice_xp": 0})
    await lv.Levels.voice_loop.coro(cog); assert lv.get_xp(1, 40)[0] == 0                     # voice xp off
    modules.set_config(1, "levels", {"voice_xp": 10})
    await lv.Levels.voice_loop.coro(cog)
    assert lv.get_xp(1, 40)[0] == 10 and lv.get_xp(1, 42)[0] == 10 and lv.get_xp(1, 41)[0] == 0 and lv.get_xp(1, 43)[0] == 0
    modules.set_config(1, "levels", {"ignored_channels": [60]})
    await lv.Levels.voice_loop.coro(cog); assert lv.get_xp(1, 40)[0] == 10

    board = modules.TABLE_PROVIDERS[("levels", "board")](guild)
    assert board[0]["rank"] == 1 and board[0]["xp"] >= board[-1]["xp"] and all("level" in r for r in board)
    assert "сброшен" in await modules.run_action("levels", "reset_all", {}, ctx) and lv.top(1) == [] and lv.get_xp(2, 11)[0] == 9999
    print("levels tests OK")

asyncio.run(main())
