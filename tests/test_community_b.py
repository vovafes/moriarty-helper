"""Run: venv/bin/python -m tests.test_community_b   (suggestions, polls)"""
import asyncio, tempfile, time
from pathlib import Path
from types import SimpleNamespace as NS

from core import db, modules
db.close(); db.init(Path(tempfile.mkdtemp()) / "t.sqlite")
from modules import suggestions as sg, polls as pl

class Msg:
    _n = 500
    def __init__(self): Msg._n += 1; self.id = Msg._n; self.edits = []; self.deleted = False
    async def edit(self, **kw): self.edits.append(kw)
    async def delete(self): self.deleted = True
class Chan:
    id = 5; name = "ideas"
    def __init__(self): self.msgs = {}; self.sent = []
    async def send(self, content=None, **kw):
        m = Msg(); m.kw = kw; self.msgs[m.id] = m; self.sent.append((content, kw)); return m
    async def fetch_message(self, i): return self.msgs[i]
class Member:
    def __init__(self, uid, name=None): self.id, self.mention, self.dms = uid, f"<@{uid}>", []; self._n = name or f"user{uid}"
    def __str__(self): return self._n
    async def send(self, text): self.dms.append(text)
chan = Chan(); members = {1: Member(1), 2: Member(2), 3: Member(3)}
guild = NS(id=1, name="Fam", get_channel=lambda i: chan if i == 5 else None, get_member=members.get)

def inter(user, message=None):
    out = []
    async def send_message(text=None, **kw): out.append(text)
    return NS(guild=guild, guild_id=1, user=user, message=message, response=NS(send_message=send_message)), out

async def main():
    ctx = modules.ActionContext(guild=guild, bot=None, user_id=9, user_name="admin")

    # ── pure: poll helpers
    assert pl.parse_options("A; B\nC;;a\n  ") == ["A", "B", "C"]                      # trims, drops empties, case-insensitive dupes
    assert len(pl.parse_options(";".join(f"o{i}" for i in range(30)))) == 10
    assert pl.bar(0, 0) == "░" * 12 and pl.bar(6, 12) == "█" * 6 + "░" * 6 and pl.bar(12, 12) == "█" * 12

    # ── suggestions
    modules.set_config(1, "suggestions", {"enabled": True})
    try: await sg.post_suggestion(guild, members[1], "x"); assert False                  # no channel yet
    except modules.ValidationError: pass
    modules.set_config(1, "suggestions", {"channel": 5, "cooldown": 300, "staff_roles": []})
    sid = await sg.post_suggestion(guild, members[1], "Добавить ночной режим")
    row = sg.get_row(sid); mid = row["message_id"]
    emb = chan.msgs[mid].kw["embed"]
    assert "Добавить ночной режим" in emb.description and "<@1>" in emb.fields[0].value and "На рассмотрении" in emb.fields[1].value
    view = sg.VoteView()
    i, out = inter(members[2], chan.msgs[mid]); await view.up.callback(i)
    assert sg.votes(sid) == (1, 0) and "за" in out[-1] and "👍 1 · 👎 0" in chan.msgs[mid].edits[-1]["embed"].fields[2].value
    i, out = inter(members[2], chan.msgs[mid]); await view.up.callback(i); assert sg.votes(sid) == (0, 0) and "снят" in out[-1]   # same click removes
    i, out = inter(members[2], chan.msgs[mid]); await view.up.callback(i)
    i, out = inter(members[2], chan.msgs[mid]); await view.down.callback(i); assert sg.votes(sid) == (0, 1)                    # switch sides
    i, out = inter(members[3], chan.msgs[mid]); await view.up.callback(i); assert sg.votes(sid) == (1, 1)
    modules.set_config(1, "suggestions", {"anonymous": True})
    i, out = inter(members[3], chan.msgs[mid]); await view.down.callback(i)
    assert chan.msgs[mid].edits[-1]["embed"].fields[0].value == "Скрыт"
    assert sg.last_by(1, 1) >= int(time.time()) - 5 and sg.last_by(1, 99) == 0

    await sg.decide(guild, sid, "approved", "admin", "Хорошая идея")
    assert sg.get_row(sid)["status"] == "approved" and chan.msgs[mid].edits[-1]["view"] is None
    assert "Принято" in chan.msgs[mid].edits[-1]["embed"].fields[1].value and "Хорошая идея" in chan.msgs[mid].edits[-1]["embed"].fields[-1].value
    assert members[1].dms and "принято" in members[1].dms[0]
    try: await sg.decide(guild, sid, "denied", "admin", None); assert False
    except modules.ValidationError: pass
    i, out = inter(members[2], chan.msgs[mid]); await view.up.callback(i); assert "закрыто" in out[-1]                         # no votes after a decision
    sid2 = await sg.post_suggestion(guild, members[2], "Ещё идея")
    assert "отклонена" in await modules.run_action("suggestions", "deny", {"id": sid2, "reason": "Нет"}, ctx)
    rows = modules.TABLE_PROVIDERS[("suggestions", "list")](guild)
    assert [r["status_label"] for r in rows] == ["Отклонено", "Принято"] and rows[1]["votes"] == "0/2"
    assert "удалена" in await modules.run_action("suggestions", "delete", {"id": sid2}, ctx) and sg.get_row(sid2) is None
    try: await modules.run_action("suggestions", "approve", {"id": 999}, ctx); assert False
    except modules.ValidationError: pass
    assert sg.can_decide(NS(guild=guild, guild_permissions=NS(manage_guild=True, administrator=False), roles=[]))
    assert not sg.can_decide(NS(guild=guild, guild_permissions=NS(manage_guild=False, administrator=False), roles=[]))

    # ── polls
    pid = await pl.create_poll(guild, chan, 9, "Любимый цвет?", ["Красный", "Синий", "Зелёный"], False, 3600)
    poll = pl.get_poll(pid); pmsg = chan.msgs[poll["message_id"]]
    assert len(pmsg.kw["view"].children) == 3 and pmsg.kw["embed"].title == "📊 Любимый цвет?"
    try: await pl.create_poll(guild, chan, 9, "q", ["один"], False, None); assert False
    except modules.ValidationError: pass
    btn = lambda idx: pl.OptionButton(pid, idx, "x")
    assert btn(1).item.custom_id == f"poll:{pid}:1"
    assert pl.OptionButton.__discord_ui_compiled_template__.match(f"poll:{pid}:2")["idx"] == "2"
    async def click(user, idx):
        i, out = inter(user, pmsg); await btn(idx).callback(i); return out[-1]
    assert "принят" in await click(members[1], 0) and pl.tally(pid, 3) == [1, 0, 0]
    assert "принят" in await click(members[1], 1) and pl.tally(pid, 3) == [0, 1, 0]            # single choice: switches
    assert "снят" in await click(members[1], 1) and pl.tally(pid, 3) == [0, 0, 0]
    await click(members[2], 0); await click(members[3], 0); await click(members[1], 2)
    assert pl.tally(pid, 3) == [2, 0, 1] and pl.voters(pid) == 3
    desc = pmsg.edits[-1]["embed"].description
    assert "2 (67%)" in desc and "1 (33%)" in desc and "Проголосовало: 3" in pmsg.edits[-1]["embed"].fields[0].value
    # multi-choice poll
    pid2 = await pl.create_poll(guild, chan, 9, "Что любите?", ["A", "B"], True, None)
    pm2 = chan.msgs[pl.get_poll(pid2)["message_id"]]
    for idx in (0, 1):
        i, out = inter(members[1], pm2); await pl.OptionButton(pid2, idx, "x").callback(i)
    assert pl.tally(pid2, 2) == [1, 1] and pl.voters(pid2) == 1
    # closing
    await pl.close_poll(guild, pl.get_poll(pid))
    assert pl.get_poll(pid)["status"] == "closed" and pmsg.edits[-1]["view"] is None and "опрос закрыт" in pmsg.edits[-1]["embed"].fields[0].value
    assert "закрыт" in await click(members[2], 1) and pl.tally(pid, 3) == [2, 0, 1]
    rows = modules.TABLE_PROVIDERS[("polls", "list")](guild); assert {r["status_label"] for r in rows} == {"закрыт", "идёт"}
    assert "создан" in await modules.run_action("polls", "create", {"channel": 5, "question": "Q", "options": "a\nb", "duration": "1h", "multi": False}, ctx)
    for bad in ({"channel": 5, "question": "Q", "options": "only", "duration": "", "multi": False},
                {"channel": 5, "question": "Q", "options": "a\nb", "duration": "soon", "multi": False}):
        try: await modules.run_action("polls", "create", bad, ctx); assert False
        except modules.ValidationError: pass
    last = pl.get_poll(db.query("SELECT MAX(id) AS m FROM polls")[0]["m"])
    assert "закрыт" in await modules.run_action("polls", "close", {"id": last["id"]}, ctx)
    try: await modules.run_action("polls", "close", {"id": last["id"]}, ctx); assert False
    except modules.ValidationError: pass
    # overdue polls get closed by the loop
    pid3 = await pl.create_poll(guild, chan, 9, "Old", ["a", "b"], False, 3600)
    db.execute("UPDATE polls SET ends_ts=? WHERE id=?", (int(time.time()) - 5, pid3))
    cog = pl.Polls(NS(get_guild=lambda i: guild))
    await pl.Polls.check_loop.coro(cog); assert pl.get_poll(pid3)["status"] == "closed"
    print("community batch B tests OK")

asyncio.run(main())
