"""Run: venv/bin/python -m tests.test_community_a   (verification, autorole, reaction roles, welcome)"""
import asyncio, datetime, random, tempfile
from pathlib import Path
from types import SimpleNamespace as NS

import discord
from core import db, modules
db.close(); db.init(Path(tempfile.mkdtemp()) / "t.sqlite")
from modules import verification as ve, autorole as ar, reaction_roles as rr, welcome as wl

class H(NS):
    __hash__ = lambda s: hash(s.id)
class Role(H):
    managed = False
    def is_default(self): return False
class Member:
    def __init__(self, uid, name="u", bot=False, roles=(), created=None):
        self.id, self.display_name, self.mention, self.bot = uid, name, f"<@{uid}>", bot
        self.roles = list(roles); self.dms = []; self.created_at = created or datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        self.display_avatar = NS(url="http://a"); self.guild = None
    async def add_roles(self, *r, reason=None): self.roles += [x for x in r if x not in self.roles]
    async def remove_roles(self, *r, reason=None): self.roles = [x for x in self.roles if x not in r]
    async def send(self, text=None, **kw): self.dms.append(text)
class Chan:
    def __init__(self, i=7): self.id, self.name, self.mention, self.sent = i, "chan", f"<#{i}>", []
    async def send(self, content=None, **kw): self.sent.append((content, kw)); return NS(id=900 + len(self.sent))
    async def fetch_message(self, i):
        async def delete(): self.deleted = getattr(self, "deleted", []) + [i]
        return NS(delete=delete)
verified, unverified, extra = Role(id=10, name="Verified"), Role(id=11, name="Unverified"), Role(id=12, name="Extra")
chan = Chan(); ROLES = {10: verified, 11: unverified, 12: extra}
class Guild:
    id = 1; name = "Fam"; member_count = 5; me = Member(99, "bot"); default_role = H(id=1)
    def get_role(self, i): return ROLES.get(i)
    def get_channel(self, i): return chan if i == 7 else None
    def get_member(self, i): return members.get(i)
guild = Guild()
members = {}
def mk(uid, **kw):
    m = Member(uid, **kw); m.guild = guild; members[uid] = m; return m

# ── pure bits
rng = random.Random(3)
for _ in range(50):
    q, a = ve.make_math(rng); x, op, y = q.split(); assert eval(f"{x}{'-' if op == '−' else op}{y}") == a and a >= 0
now = datetime.datetime(2026, 10, 5, tzinfo=datetime.timezone.utc)
assert ve.old_enough(now - datetime.timedelta(days=10), 7, now) and not ve.old_enough(now - datetime.timedelta(days=3), 7, now)
assert ve.old_enough(now, 0, now)
assert rr.decide("toggle", [1, 2, 3], {2}, 1) == ([1], [])
assert rr.decide("toggle", [1, 2, 3], {2}, 2) == ([], [2])                       # clicking an owned role removes it
assert rr.decide("unique", [1, 2, 3], {2}, 1) == ([1], [2])                      # swaps within the panel
assert rr.decide("unique", [1, 2, 3], {2, 99}, 1) == ([1], [2])                  # never touches roles outside the panel
assert rr.decide("unique", [1, 2], set(), 2) == ([2], [])

def inter(user, **extra):
    out = []
    async def send_message(text=None, **kw): out.append(text)
    async def send_modal(m): out.append(m)
    return NS(guild=guild, guild_id=1, user=user, response=NS(send_message=send_message, send_modal=send_modal), **extra), out

async def main():
    # ── verification (off by default)
    ctrl = ve.VerifyView()
    u = mk(1)
    i, out = inter(u); await ctrl.start.callback(i); assert "выключена" in out[-1]
    modules.set_config(1, "verification", {"enabled": True, "verified_role": 10, "unverified_role": 11, "log_channel": 7})
    cog = ve.Verification(NS())
    u.roles = []; await cog.on_member_join(u); assert unverified in u.roles                 # gets the waiting role on join
    bot_m = mk(2, bot=True); await cog.on_member_join(bot_m); assert bot_m.roles == []
    i, out = inter(u); await ctrl.start.callback(i)
    assert verified in u.roles and unverified not in u.roles and "Готово" in out[-1] and chan.sent   # button mode
    i, out = inter(u); await ctrl.start.callback(i); assert "уже прошли" in out[-1]
    young = mk(3, created=datetime.datetime.now(datetime.timezone.utc))
    modules.set_config(1, "verification", {"min_account_age_days": 7})
    i, out = inter(young); await ctrl.start.callback(i); assert "слишком новый" in out[-1] and verified not in young.roles
    modules.set_config(1, "verification", {"min_account_age_days": 0, "mode": "math"})
    i, out = inter(young); await ctrl.start.callback(i); modal = out[-1]
    assert isinstance(modal, ve.MathModal) and verified not in young.roles
    async def submit(value):
        modal.field._value = str(value); o = []
        async def sm(t, **kw): o.append(t)
        await modal.on_submit(NS(guild_id=1, user=young, response=NS(send_message=sm))); return o[-1]
    assert "Неверно" in await submit(modal.answer + 1) and verified not in young.roles
    assert "Неверно" in await submit("abc")
    assert "Готово" in await submit(modal.answer) and verified in young.roles
    modules.set_config(1, "verification", {"verified_role": None})
    z = mk(4); i, out = inter(z); modules.set_config(1, "verification", {"mode": "button"}); await ctrl.start.callback(i)
    assert "не настроена" in out[-1]
    ctx = modules.ActionContext(guild=guild, bot=None, user_id=1, user_name="a")
    try: await modules.run_action("verification", "publish", {"channel": 7}, ctx); assert False
    except modules.ValidationError: pass
    modules.set_config(1, "verification", {"verified_role": 10})
    assert "опубликована" in await modules.run_action("verification", "publish", {"channel": 7}, ctx)

    # ── autorole
    cog = ar.AutoRole(NS())
    m = mk(5); await cog.on_member_join(m); assert m.roles == []                                  # disabled by default? (enabled=False)
    modules.set_config(1, "autorole", {"enabled": True, "member_roles": [12], "bot_roles": [10, 12]})
    m = mk(6); await cog.on_member_join(m); assert m.roles == [extra]
    b = mk(7, bot=True); await cog.on_member_join(b); assert set(b.roles) == {verified, extra}
    ROLES[13] = Role(id=13, name="Managed"); ROLES[13].managed = True
    modules.set_config(1, "autorole", {"member_roles": [13]}); m = mk(8); await cog.on_member_join(m); assert m.roles == []   # managed roles skipped

    # ── reaction roles
    pid = rr.create_panel(1, 7, "Roles", "unique", [10, 12])
    btn = rr.RoleButton(pid, 10, "Verified")
    assert btn.item.custom_id == f"rr:{pid}:10"
    class _M: pass
    m2 = rr.RoleButton.__discord_ui_compiled_template__.match(f"rr:{pid}:12"); assert m2["panel"] == str(pid) and m2["role"] == "12"
    u = mk(20); modules.set_config(1, "reaction_roles", {"enabled": True})
    i, out = inter(u); await btn.callback(i); assert verified in u.roles and "Выдана" in out[-1]
    i, out = inter(u); await rr.RoleButton(pid, 12).callback(i); assert extra in u.roles and verified not in u.roles    # unique swap
    i, out = inter(u); await rr.RoleButton(pid, 12).callback(i); assert extra not in u.roles and "Снята" in out[-1]
    i, out = inter(u); await rr.RoleButton(pid + 5, 10).callback(i); assert "недоступна" in out[-1]                     # unknown panel
    i, out = inter(u); await rr.RoleButton(pid, 77).callback(i); assert "недоступна" in out[-1]                         # role not in panel
    modules.set_config(1, "reaction_roles", {"enabled": False})
    i, out = inter(u); await btn.callback(i); assert "выключена" in out[-1]
    modules.set_config(1, "reaction_roles", {"enabled": True})
    chan.sent.clear()
    res = await modules.run_action("reaction_roles", "create", {"channel": 7, "title": "Games", "roles": [10, 12], "mode": "toggle"}, ctx)
    assert "опубликована" in res and len(chan.sent[0][1]["view"].children) == 2
    for bad in ({"channel": 7, "title": "x", "roles": [13], "mode": "toggle"}, {"channel": 7, "title": "x", "roles": [999], "mode": "toggle"}):
        try: await modules.run_action("reaction_roles", "create", bad, ctx); assert False
        except modules.ValidationError: pass
    rows = modules.TABLE_PROVIDERS[("reaction_roles", "panels")](guild)
    assert len(rows) == 2 and rows[0]["roles_text"] == "Verified, Extra" and rows[0]["mode"] == "любые"
    assert "удалена" in await modules.run_action("reaction_roles", "delete", {"id": rows[0]["id"]}, ctx)
    try: await modules.run_action("reaction_roles", "delete", {"id": rows[0]["id"]}, ctx); assert False
    except modules.ValidationError: pass

    # ── welcome
    cog = wl.Welcome(NS())
    chan.sent.clear(); w = mk(30, name="Newbie")
    await cog.on_member_join(w); assert chan.sent == []                                         # disabled by default
    modules.set_config(1, "welcome", {"enabled": True, "welcome_channel": 7, "farewell_channel": 7, "boost_channel": 7,
                                      "dm_message": "Привет в {server}, {name}!"})
    await cog.on_member_join(w)
    assert chan.sent and chan.sent[0][1]["embed"].description == "Добро пожаловать, <@30>! Нас уже 5." and w.dms == ["Привет в Fam, Newbie!"]
    n = len(chan.sent); await cog.on_member_join(mk(31, bot=True)); assert len(chan.sent) == n          # bots ignored
    modules.set_config(1, "welcome", {"welcome_embed": False}); await cog.on_member_join(w)
    assert chan.sent[-1][0] == "Добро пожаловать, <@30>! Нас уже 5."
    await cog.on_member_remove(w); assert chan.sent[-1][0] == "Newbie покинул сервер."
    boost = NS(guild=guild, type=discord.MessageType.premium_guild_subscription, author=w)
    await cog.on_message(boost); assert "забустил" in chan.sent[-1][0]
    await cog.on_message(NS(guild=guild, type=discord.MessageType.default, author=w)); n = len(chan.sent); assert chan.sent[-1][0].startswith("💎")
    modules.set_config(1, "welcome", {"farewell_message": "   "}); await cog.on_member_remove(w); assert len(chan.sent) == n   # empty text -> silent
    ctx2 = modules.ActionContext(guild=guild, bot=NS(), user_id=30, user_name="a")
    assert "Пробное" in await modules.run_action("welcome", "test", {"kind": "boost"}, ctx2)
    modules.set_config(1, "welcome", {"boost_channel": None})
    try: await modules.run_action("welcome", "test", {"kind": "boost"}, ctx2); assert False
    except modules.ValidationError: pass
    print("community batch A tests OK")

asyncio.run(main())
