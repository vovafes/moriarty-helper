"""Run: venv/bin/python -m tests.test_team"""
import asyncio, datetime, tempfile, time
from pathlib import Path
from types import SimpleNamespace as NS

from core import db, modules
db.close(); db.init(Path(tempfile.mkdtemp()) / "t.sqlite")
from modules import team as tm
import legacy.state as st

assert tm.met_norm(20, 0, 20, 30) and tm.met_norm(0, 30, 20, 30) and not tm.met_norm(19, 29, 20, 30)
assert tm.met_norm(0, 0, 0, 0) and tm.met_norm(5, 0, 0, 30) is False and tm.met_norm(0, 40, 0, 30)

MSK = tm.MSK
class Role: 
    def __init__(self, i): self.id = i
class Member:
    def __init__(self, uid, rs=(), bot=False, voice=None): self.id, self.roles, self.bot, self.voice, self.mention, self.display_name = uid, [Role(r) for r in rs], bot, voice, f"<@{uid}>", f"u{uid}"
class Chan:
    def __init__(self): self.sent = []
    async def send(self, content=None, embed=None, **kw): self.sent.append((content, embed))
chan = Chan()
ms = [Member(1, [10]), Member(2, [10]), Member(3, [10]), Member(4, [99]), Member(5, [10], bot=True)]
guild = NS(id=1, members=ms, voice_channels=[], get_member=lambda i: next((m for m in ms if m.id == i), None),
           get_channel=lambda i: chan if i == 7 else None, get_role=lambda i: NS(mention="<@&8>") if i == 8 else None)
for m in ms: m.guild = guild
def msg(m): return NS(guild=guild, author=m)

async def main():
    cog = tm.Team(NS(guilds=[guild]))
    # counting only for team members, only when enabled
    await cog.on_message(msg(ms[0])); assert tm.stats_for(1, tm.today_ymd()) == {}
    modules.set_config(1, "activity_check", {"enabled": True, "team_roles": [10], "min_messages": 3, "min_voice": 5,
                                             "report_channel": 7, "hour": 10, "ping_role": 8})
    for _ in range(3): await cog.on_message(msg(ms[0]))
    await cog.on_message(msg(ms[1])); await cog.on_message(msg(ms[3])); await cog.on_message(msg(ms[4]))        # non-team + bot ignored
    assert tm.stats_for(1, tm.today_ymd()) == {1: (3, 0), 2: (1, 0)}
    # voice minutes
    ms[1].voice = NS(self_mute=False, self_deaf=False, afk=False)
    ms[2].voice = NS(self_mute=True, self_deaf=True, afk=False)
    guild.voice_channels = [NS(members=[ms[1], ms[2], ms[3]])]
    await tm.Team.voice_loop.coro(cog)
    assert tm.stats_for(1, tm.today_ymd())[2] == (1, 1) and 3 not in tm.stats_for(1, tm.today_ymd()) and 4 not in tm.stats_for(1, tm.today_ymd())

    # report for a day with data
    yday = "2026-10-04"
    tm.bump(1, 1, yday, msgs=10); tm.bump(1, 2, yday, voice=6); tm.bump(1, 3, yday, msgs=1, voice=1)
    cfg = modules.get_config(1, "activity_check")
    bad, ok, skipped = tm.build_report(guild, cfg, yday)
    assert ok == 2 and skipped == 0 and bad == ["<@3> — 1 сообщ., 1 мин"]
    st.afk_list[1] = {3: {}}; bad, ok, skipped = tm.build_report(guild, cfg, yday); assert bad == [] and skipped == 1   # excused by AFK
    modules.set_config(1, "activity_check", {"skip_absent": False}); cfg = modules.get_config(1, "activity_check")
    assert len(tm.build_report(guild, cfg, yday)[0]) == 1
    st.afk_list.clear()

    # scheduled run: once a day, only after the hour
    at = lambda h, d=5: datetime.datetime(2026, 10, d, h, 0, tzinfo=MSK)
    await cog.run_reports(at(9)); assert chan.sent == []
    await cog.run_reports(at(10))
    assert len(chan.sent) == 1 and chan.sent[0][0] == "<@&8>" and "04.10.2026" in chan.sent[0][1].title
    await cog.run_reports(at(15)); assert len(chan.sent) == 1
    await cog.run_reports(at(11, d=6)); assert len(chan.sent) == 2
    modules.set_config(1, "activity_check", {"enabled": False}); await cog.run_reports(at(12, d=7)); assert len(chan.sent) == 2
    modules.set_config(1, "activity_check", {"enabled": True, "report_channel": None})
    ctx = modules.ActionContext(guild=guild, bot=NS(), user_id=1, user_name="a")
    try: await modules.run_action("activity_check", "report", {"day": "yesterday"}, ctx); assert False
    except modules.ValidationError: pass
    modules.set_config(1, "activity_check", {"report_channel": 7})
    assert "отправлен" in await modules.run_action("activity_check", "report", {"day": "today"}, ctx)
    rows = modules.TABLE_PROVIDERS[("activity_check", "today")](guild)
    assert {r["user"] for r in rows} == {"u1 (1)", "u2 (2)", "u3 (3)"} and rows[0]["today"].count("/") == 1

    # ── rating
    def rate_inter(user, member, stars, comment=""):
        out = []
        async def send_message(text, **kw): out.append(text)
        return NS(guild=guild, guild_id=1, user=user, response=NS(send_message=send_message)), out, (member, stars, comment)
    async def rate(user, member, stars, comment=""):
        i, out, args = rate_inter(user, member, stars, comment); await tm.Team.rate.callback(cog, i, *args); return out[-1]
    guild.get_channel = lambda i: chan if i == 7 else None
    assert "выключены" in await rate(ms[3], ms[0], 5)
    modules.set_config(1, "team_rating", {"enabled": True, "team_roles": [10], "cooldown_hours": 24, "log_channel": 7})
    assert "Спасибо" in await rate(ms[3], ms[0], 5, "Отлично помог")
    assert "снова" in await rate(ms[3], ms[0], 1)                                                    # cooldown per rater/target
    assert "Спасибо" in await rate(ms[1], ms[0], 3)
    assert "себя" in await rate(ms[0], ms[0], 5) and "себя" in await rate(ms[3], ms[4], 5)
    assert "не из команды" in await rate(ms[0], ms[3], 5)
    assert "Новая оценка" in chan.sent[-1][1].title
    s = tm.summary(1); assert s == [{"target_id": 1, "avg": 4.0, "count": 2}]
    assert modules.TABLE_PROVIDERS[("team_rating", "summary")](guild)[0]["avg"] == 4.0
    assert modules.TABLE_PROVIDERS[("team_rating", "recent")](guild)[0]["stars"] == "★★★"
    assert "Удалено оценок: 2" in await modules.run_action("team_rating", "reset", {"user": 1}, ctx)
    try: await modules.run_action("team_rating", "reset", {"user": 1}, ctx); assert False
    except modules.ValidationError: pass
    print("team tests OK")

asyncio.run(main())
