"""Run: venv/bin/python -m tests.test_giveaways"""
import asyncio, random, tempfile, time
from pathlib import Path
from types import SimpleNamespace as NS

from core import db, modules
db.close(); db.init(Path(tempfile.mkdtemp()) / "t.sqlite")
from modules import giveaways as gw

# pure: winner picking
assert sorted(gw.pick_winners([1, 2, 3], 5)) == [1, 2, 3]                  # fewer candidates than winners
assert len(gw.pick_winners(list(range(100)), 3)) == 3
assert gw.pick_winners([7, 7, 7], 3) == [7]                                  # duplicates collapse
assert gw.pick_winners([], 2) == []
assert gw.pick_winners([1, 2, 3, 4], 2, random.Random(1)) == gw.pick_winners([1, 2, 3, 4], 2, random.Random(1))

class Msg:
    _n = 100
    def __init__(self): Msg._n += 1; self.id = Msg._n; self.edits = []
    async def edit(self, **kw): self.edits.append(kw)
class Chan:
    id = 5; name = "giveaways"
    def __init__(self): self.sent, self.msgs = [], {}
    async def send(self, content=None, **kw):
        m = Msg(); self.msgs[m.id] = m; self.sent.append((content, kw)); return m
    async def fetch_message(self, i): return self.msgs[i]
class Member:
    def __init__(self, uid, bot=False, roles=()): self.id, self.bot, self.roles = uid, bot, [NS(id=r) for r in roles]
members = {1: Member(1), 2: Member(2), 3: Member(3, bot=True), 4: Member(4, roles=[9]), 5: Member(5, roles=[9])}
chan = Chan()
guild = NS(id=1, get_member=members.get, get_channel=lambda i: chan if i == 5 else None, get_role=lambda i: None, me=Member(99))
host = Member(1)

async def main():
    modules.set_config(1, "giveaways", {"enabled": True})
    gid = await gw.start_giveaway(guild, chan, host, "Nitro", 3600, 2, None)
    row = gw.get_row(gid)
    assert row["status"] == "active" and row["message_id"] in chan.msgs
    assert gw.get_by_message(row["message_id"])["id"] == gid
    emb = chan.sent[0][1]["embed"]
    assert "Nitro" in emb.title and emb.footer.text == "MORIARTY" and "Победителей: **2**" in emb.description

    # button: join / leave / closed / role requirement
    sent = []
    async def respond(text, ephemeral=False): sent.append(text)
    def click(user, message_id):
        return NS(message=NS(id=message_id), user=user, response=NS(send_message=respond))
    btn = gw.GiveawayButton("x")
    await btn.callback(click(members[1], row["message_id"])); assert "участвуете" in sent[-1] and gw.entries_of(gid) == [1]
    await btn.callback(click(members[1], row["message_id"])); assert "вышли" in sent[-1] and gw.entries_of(gid) == []
    for u in (1, 2, 3, 4):
        await btn.callback(click(members[u], row["message_id"]))
    assert sorted(gw.entries_of(gid)) == [1, 2, 3, 4]
    await btn.callback(click(members[1], 424242)); assert "завершён" in sent[-1]       # unknown message

    # finish: bot excluded, 2 winners from {1,2,4}
    winners = await gw.finish_giveaway(guild, gw.get_row(gid))
    assert len(winners) == 2 and 3 not in winners and set(winners) <= {1, 2, 4}
    assert gw.get_row(gid)["status"] == "ended"
    assert chan.msgs[row["message_id"]].edits and chan.msgs[row["message_id"]].edits[-1]["view"] is None
    assert "Победители" in chan.sent[-1][0]
    await btn.callback(click(members[2], row["message_id"])); assert "завершён" in sent[-1]   # can't join after the end

    # reroll
    again = await gw.finish_giveaway(guild, gw.get_row(gid), reroll=True)
    assert len(again) == 2 and "Новые победители" in chan.sent[-1][0]

    # role requirement: only members with role 9 can win / join
    gid2 = await gw.start_giveaway(guild, chan, host, "Role only", 3600, 5, 9)
    r2 = gw.get_row(gid2)
    await btn.callback(click(members[1], r2["message_id"])); assert "нужна роль" in sent[-1].lower() and gw.entries_of(gid2) == []
    for u in (4, 5): await btn.callback(click(members[u], r2["message_id"]))
    assert sorted(await gw.finish_giveaway(guild, gw.get_row(gid2))) == [4, 5]
    # a user who joined and then lost the role is not eligible
    gid3 = await gw.start_giveaway(guild, chan, host, "Lost role", 3600, 5, 9)
    gw.toggle_entry(gid3, 4); gw.toggle_entry(gid3, 1)
    assert await gw.finish_giveaway(guild, gw.get_row(gid3)) == [4]
    # nobody joined
    gid4 = await gw.start_giveaway(guild, chan, host, "Empty", 3600, 1, None)
    assert await gw.finish_giveaway(guild, gw.get_row(gid4)) == [] and "никто не участвовал" in chan.sent[-1][0]

    # due rows + cancel
    gid5 = await gw.start_giveaway(guild, chan, host, "Soon", 3600, 1, None)
    assert [r["id"] for r in gw.due_rows(int(time.time()))] == []
    assert [r["id"] for r in gw.due_rows(int(time.time()) + 7200)] == [gid5]
    gw.mark(gid5, "cancelled"); assert gw.due_rows(int(time.time()) + 7200) == []
    await gw._edit_cancelled(guild, gw.get_row(gid5))
    assert "Отменён" in chan.msgs[gw.get_row(gid5)["message_id"]].edits[-1]["embed"].description

    # panel table + actions
    rows = modules.TABLE_PROVIDERS[("giveaways", "list")](guild)
    assert len(rows) == 5 and rows[0]["id"] == gid5 and {r["status_label"] for r in rows} == {"завершён", "отменён"}
    ctx = modules.ActionContext(guild=guild, bot=None, user_id=1, user_name="admin")
    msg = await modules.run_action("giveaways", "start", {"channel": 5, "prize": "Panel", "duration": "10m", "winners": 1}, ctx)
    assert "запущен" in msg
    for bad in ({"channel": 5, "prize": "x", "duration": "soon", "winners": 1},):
        try: await modules.run_action("giveaways", "start", bad, ctx); assert False
        except modules.ValidationError: pass
    try: await modules.run_action("giveaways", "end", {"id": gid}, ctx); assert False        # already ended
    except modules.ValidationError: pass
    last = gw.list_rows(1)[0]["id"]
    assert "Завершён" in await modules.run_action("giveaways", "end", {"id": last}, ctx)
    assert "Новых" in await modules.run_action("giveaways", "reroll", {"id": last}, ctx)
    print("giveaways tests OK")

asyncio.run(main())
