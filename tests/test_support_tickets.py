"""Run: venv/bin/python -m tests.test_support_tickets"""
import asyncio, datetime, tempfile
from pathlib import Path
from types import SimpleNamespace as NS

import discord
from aiohttp.test_utils import TestClient, TestServer
from core import db, modules
db.close(); db.init(Path(tempfile.mkdtemp()) / "t.sqlite")
from modules import support_tickets as st
from dashboard import server

# categories
cats = st.parse_categories("Общий|❓|Любой вопрос\nЖалоба\n\n  |x|y\nТретья|🔥")
assert [c["label"] for c in cats] == ["Общий", "Жалоба", "Третья"]
assert cats[0]["emoji"] == "❓" and cats[1]["emoji"] is None and cats[1]["description"] is None
assert len(st.parse_categories("\n".join(f"c{i}" for i in range(40)))) == 25

# transcript is escaped
now = datetime.datetime(2026, 10, 5, 12, 0, tzinfo=datetime.timezone.utc)
class A(NS):
    def __str__(self): return self.name
msg = NS(author=A(name="<b>evil</b>"), created_at=now, content="<script>alert(1)</script>\nline2",
         attachments=[NS(url="https://x/y.png\"onerror=\"x", filename="y.png")], embeds=[NS(title="T<", description=None)])
ticket = {"id": 7, "category": "Жалоба<", "opener_name": "u<", "created_ts": int(now.timestamp())}
page = st.render_transcript(ticket, [msg])
assert "<script>" not in page and "&lt;script&gt;" in page and "&lt;b&gt;evil" in page
assert 'href="https://x/y.png&quot;onerror=&quot;x"' in page and "line2" in page and "Жалоба&lt;" in page

# fakes
class Member:
    def __init__(self, uid, name, roles=(), admin=False):
        self.id, self.name, self.display_name, self.mention = uid, name, name, f"<@{uid}>"
        self.roles = [NS(id=r) for r in roles]; self.guild_permissions = NS(administrator=admin, manage_guild=False); self.dms = []; self.bot = False
    def __str__(self): return self.name
    async def send(self, text=None, **kw): self.dms.append((text, kw))
class Chan:
    _n = 1000
    def __init__(self, name="x"): Chan._n += 1; self.id, self.name, self.mention, self.sent, self.deleted, self.hist, self.perms = Chan._n, name, f"<#{Chan._n}>", [], False, [], {}
    async def send(self, content=None, **kw): self.sent.append((content, kw))
    async def delete(self, reason=None): self.deleted = True
    def history(self, limit, oldest_first):
        async def gen():
            for m in self.hist: yield m
        return gen()
    async def set_permissions(self, who, **kw): self.perms[who] = kw
opener, staffer, outsider = Member(1, "opener"), Member(2, "staff", roles=[50]), Member(3, "rando")
created, log = [], Chan("log")
category = Chan("tickets"); category.type = discord.ChannelType.category
class H(NS):                                  # SimpleNamespace is unhashable; real roles are hashable
    __hash__ = lambda s: hash(s.id)
staff_role = H(id=50, mention="<@&50>")
class Guild:
    id = 1; name = "Fam"; default_role = H(id=1); me = Member(99, "bot")
    def get_channel(self, i): return {log.id: log, category.id: category}.get(i) or next((c for c in created if c.id == i), None)
    def get_role(self, i): return staff_role if i == 50 else None
    def get_member(self, i): return {1: opener, 2: staffer, 3: outsider}.get(i)
    async def create_text_channel(self, name, **kw):
        c = Chan(name); c.kw = kw; created.append(c); return c
guild = Guild()
def inter(user, channel_id=None, values=None):
    out = []
    async def send_message(text, **kw): out.append(text)
    async def defer(**kw): pass
    async def followup_send(text, **kw): out.append(text)
    i = NS(guild=guild, guild_id=1, user=user, channel_id=channel_id, channel=next((c for c in created if c.id == channel_id), None),
           response=NS(send_message=send_message, defer=defer, edit_message=send_message), followup=NS(send=followup_send))
    return i, out

async def main():
    modules.set_config(1, "support_tickets", {"enabled": True, "category_channel": category.id, "transcript_channel": log.id,
                                              "staff_roles": [50], "max_open": 1})
    # open
    i, out = inter(opener); await st.open_ticket(i, "Жалоба")
    assert len(created) == 1 and "Тикет создан" in out[-1]
    chan = created[0]; assert chan.kw["overwrites"][guild.default_role].view_channel is False
    assert chan.kw["overwrites"][opener].view_channel and chan.kw["overwrites"][staff_role].view_channel
    assert "<@1>" in chan.sent[0][0] and "<@&50>" in chan.sent[0][0]
    t = st.ticket_by_channel(chan.id); assert t["opener_id"] == 1 and t["category"] == "Жалоба"
    i, out = inter(opener); await st.open_ticket(i, "Жалоба"); assert "уже есть" in out[-1] and len(created) == 1   # limit

    # claim: only staff, only once
    ctrl = st.TicketControls()
    i, out = inter(outsider, chan.id); await ctrl.claim.callback(i); assert "Только поддержка" in out[-1]
    i, out = inter(staffer, chan.id); await ctrl.claim.callback(i); assert "взял" in out[-1]
    i, out = inter(staffer, chan.id); await ctrl.claim.callback(i); assert "уже взят" in out[-1]

    # permission to close
    i, out = inter(outsider, chan.id); await st.close_from_interaction(i); assert "автор или поддержка" in out[-1] and not chan.deleted
    # close: transcript saved, posted to the log, rating DM, channel deleted
    chan.hist = [NS(author=opener, created_at=now, content="help me", attachments=[], embeds=[])]
    i, out = inter(opener, chan.id); await st.close_from_interaction(i)
    assert chan.deleted and st.get_ticket(t["id"])["status"] == "closed"
    assert "help me" in st.transcript_html(t["id"], 1) and st.transcript_html(t["id"], 2) is None   # scoped to the guild
    assert log.sent and log.sent[0][1]["file"].filename == f"ticket-{t['id']}.html"
    assert opener.dms and isinstance(opener.dms[0][1]["view"], st.RatingView)
    assert st.ticket_by_channel(chan.id) is None
    i, out = inter(opener, chan.id); await st.close_from_interaction(i); assert "не активный" in out[-1]

    # rating: once, only by the opener
    assert not st.rate(t["id"], 3, 5) and st.rate(t["id"], 1, 4) and not st.rate(t["id"], 1, 2)
    assert st.get_ticket(t["id"])["rating"] == 4

    # a closed ticket frees the slot
    i, out = inter(opener); await st.open_ticket(i, "Общий"); assert len(created) == 2

    # panel table + close action + publish
    rows = modules.TABLE_PROVIDERS[("support_tickets", "tickets")](guild)
    assert rows[1]["transcript"] == f"/transcripts/1/{t['id']}" and rows[1]["rating"] == "★★★★" and rows[0]["transcript"] is None
    ctx = modules.ActionContext(guild=guild, bot=None, user_id=2, user_name="staff")
    log_before = len(log.sent)
    assert "сохранена" in await modules.run_action("support_tickets", "close", {"id": rows[0]["id"]}, ctx)
    assert created[1].deleted and len(log.sent) == log_before + 1
    try: await modules.run_action("support_tickets", "close", {"id": rows[0]["id"]}, ctx); assert False
    except modules.ValidationError: pass
    assert "опубликована" in await modules.run_action("support_tickets", "publish", {"channel": log.id}, ctx)
    assert isinstance(log.sent[-1][1]["view"], st.PanelView)
    modules.set_config(1, "support_tickets", {"categories": ""})
    try: await modules.run_action("support_tickets", "publish", {"channel": log.id}, ctx); assert False
    except modules.ValidationError: pass

    # transcript route: needs login + guild access, serves with a locked-down CSP
    bot = NS(get_guild=lambda i: guild if i == 1 else None, guilds=[guild])
    async with TestClient(TestServer(server.create_app(bot))) as c:
        url = f"/transcripts/1/{t['id']}"
        assert (await c.get(url)).status == 401
        tok = server._new_session({"user": {"id": 5, "name": "x", "avatar": None},
                                   "guilds": {1: {"id": "1", "name": "Fam", "permissions": "32", "owner": False}}})
        c.session.cookie_jar.update_cookies({server.COOKIE: tok})
        r = await c.get(url); body = await r.text()
        assert r.status == 200 and "help me" in body and "default-src 'none'" in r.headers["Content-Security-Policy"]
        assert (await c.get("/transcripts/1/9999")).status == 404
        assert (await c.get("/transcripts/2/1")).status == 403
    print("support ticket tests OK")

asyncio.run(main())
