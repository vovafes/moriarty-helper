"""Run: venv/bin/python -m tests.test_community_c   (reminders, birthdays)"""
import asyncio, datetime, tempfile, time
from pathlib import Path
from types import SimpleNamespace as NS

import discord
from core import db, modules
db.close(); db.init(Path(tempfile.mkdtemp()) / "t.sqlite")
from modules import reminders as rm, birthdays as bd

MSK = rm.MSK
at = lambda *a: datetime.datetime(*a, tzinfo=MSK)
now = at(2026, 10, 5, 12, 0)
ts = lambda d: int(d.timestamp())

# ── parse_when
assert rm.parse_when("10m", now) == ts(now) + 600 and rm.parse_when("2h30m", now) == ts(now) + 9000
assert rm.parse_when("20:30", now) == ts(at(2026, 10, 5, 20, 30))                    # later today
assert rm.parse_when("09:00", now) == ts(at(2026, 10, 6, 9, 0))                      # already passed -> tomorrow
assert rm.parse_when("25.12 18:00", now) == ts(at(2026, 12, 25, 18, 0))
assert rm.parse_when("01.01 10:00", now) == ts(at(2027, 1, 1, 10, 0))                # date passed this year -> next year
assert rm.parse_when("25.12.2027 18:00", now) == ts(at(2027, 12, 25, 18, 0))
for bad in ("", "soon", "25:99", "31.02 10:00", "99.99 10:00", "10"):
    assert rm.parse_when(bad, now) is None, bad

# ── reminder storage + delivery
class User:
    def __init__(self, uid, dm_ok=True): self.id, self.dm_ok, self.dms = uid, dm_ok, []
    async def send(self, text):
        if not self.dm_ok: raise discord.Forbidden(NS(status=403, reason="x"), "closed")
        self.dms.append(text)
class Chan:
    id = 5; 
    def __init__(self): self.sent = []
    async def send(self, text, **kw): self.sent.append(text)
chan = Chan(); users = {1: User(1), 2: User(2, dm_ok=False)}
guild = NS(id=1, get_channel=lambda i: chan if i == 5 else None, get_member=users.get)
bot = NS(get_guild=lambda i: guild, get_user=users.get, guilds=[guild])

async def main():
    r1 = rm.add(1, 1, 5, "купить хлеб", 100, True); r2 = rm.add(1, 2, 5, "позвонить", 200, True); rm.add(1, 1, 5, "later", 9999999999, True)
    assert rm.count_for(1, 1) == 2 and [r["id"] for r in rm.due(250)] == [r1, r2] and len(rm.mine(1, 1)) == 2
    cog = rm.Reminders(bot)
    for r in rm.due(250): await rm.deliver(bot, r)
    assert users[1].dms == ["⏰ **Напоминание:** купить хлеб"]
    assert chan.sent == ["<@2> ⏰ **Напоминание:** позвонить"]                       # closed DMs -> falls back to the channel
    modules.set_config(1, "reminders", {"enabled": True})
    await rm.Reminders.loop.coro(cog)                                              # fires + deletes the overdue ones
    assert rm.due(250) == [] and rm.count_for(1, 1) == 1
    assert rm.cancel(rm.mine(1, 1)[0]["id"], 1, user_id=2) is False                # can't cancel someone else's
    assert rm.cancel(rm.mine(1, 1)[0]["id"], 1, user_id=1) is True
    ctx = modules.ActionContext(guild=guild, bot=bot, user_id=9, user_name="a")
    rid = rm.add(1, 1, 5, "x", 9999999999, True)
    assert "удалено" in await modules.run_action("reminders", "cancel", {"id": rid}, ctx)
    try: await modules.run_action("reminders", "cancel", {"id": rid}, ctx); assert False
    except modules.ValidationError: pass
    assert modules.TABLE_PROVIDERS[("reminders", "list")](guild) == []

    # ── birthdays: parsing and calendar logic
    assert bd.parse_date("25.12") == (25, 12, None) and bd.parse_date("25/12/1999") == (25, 12, 1999) and bd.parse_date("29.02") == (29, 2, None)
    for bad in ("", "31.02", "32.01", "1.13", "abc", "1.1.1.1", "25.12.1850", "25.12.2999"):
        assert bd.parse_date(bad) is None, bad
    d = datetime.date
    assert bd.is_today(5, 10, d(2026, 10, 5)) and not bd.is_today(6, 10, d(2026, 10, 5))
    assert bd.is_today(29, 2, d(2028, 2, 29)) and not bd.is_today(29, 2, d(2028, 2, 28))      # leap year: real day
    assert bd.is_today(29, 2, d(2027, 2, 28)) and not bd.is_today(29, 2, d(2027, 3, 1))       # common year: 28.02
    assert bd.next_date(5, 10, d(2026, 10, 5)) == d(2026, 10, 5) and bd.next_date(4, 10, d(2026, 10, 5)) == d(2027, 10, 4)
    assert bd.next_date(29, 2, d(2026, 10, 5)) == d(2027, 2, 28) and bd.next_date(29, 2, d(2027, 3, 1)) == d(2028, 2, 29)

    # ── birthdays: daily run
    role = NS(id=70); roles_log = []
    class M:
        def __init__(self, uid): self.id, self.mention, self.roles = uid, f"<@{uid}>", []
        async def add_roles(self, r, reason=None): self.roles.append(r)
        async def remove_roles(self, r, reason=None): self.roles.remove(r)
    ms = {10: M(10), 11: M(11), 12: M(12)}
    bchan = Chan()
    g2 = NS(id=2, get_channel=lambda i: bchan if i == 8 else None, get_role=lambda i: role if i == 70 else None, get_member=ms.get)
    bd.save(2, 10, 5, 10, 2000); bd.save(2, 11, 5, 10, None); bd.save(2, 12, 6, 10, 1990); bd.save(2, 99, 5, 10, None)   # 99 left the server
    modules.set_config(2, "birthdays", {"enabled": True, "channel": 8, "role": 70, "hour": 9, "message": "🎂 {user} ({age})"})
    cogb = bd.Birthdays(NS(guilds=[g2]))
    await cogb.run_guild(g2, at(2026, 10, 5, 8, 0)); assert bchan.sent == []                    # too early
    await cogb.run_guild(g2, at(2026, 10, 5, 9, 0))
    assert sorted(bchan.sent) == ["🎂 <@10> (26)", "🎂 <@11> ()"] and ms[10].roles == [role] and ms[11].roles == [role] and ms[12].roles == []
    await cogb.run_guild(g2, at(2026, 10, 5, 15, 0)); assert len(bchan.sent) == 2             # once per day
    await cogb.run_guild(g2, at(2026, 10, 6, 9, 30))                                           # next day: role removed, 12 congratulated
    assert ms[10].roles == [] and ms[11].roles == [] and ms[12].roles == [role] and bchan.sent[-1] == "🎂 <@12> (36)"
    modules.set_config(2, "birthdays", {"enabled": False}); n = len(bchan.sent)
    await cogb.run_guild(g2, at(2026, 10, 7, 12, 0)); assert len(bchan.sent) == n
    # panel
    ctx2 = modules.ActionContext(guild=g2, bot=None, user_id=1, user_name="a")
    assert "Сохранено" in await modules.run_action("birthdays", "set", {"user": 10, "date": "01.02.1999"}, ctx2)
    for bad in ({"user": 10, "date": "99.99"}, {"user": 555, "date": "01.02"}):
        try: await modules.run_action("birthdays", "set", bad, ctx2); assert False
        except modules.ValidationError: pass
    assert "Удалено" in await modules.run_action("birthdays", "remove", {"user": 10}, ctx2)
    try: await modules.run_action("birthdays", "remove", {"user": 10}, ctx2); assert False
    except modules.ValidationError: pass
    print("community batch C tests OK")

asyncio.run(main())
