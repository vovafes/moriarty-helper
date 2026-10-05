"""Run: venv/bin/python -m tests.test_roles_extra"""
import asyncio, tempfile, time
from pathlib import Path
from types import SimpleNamespace as NS

from core import db, modules
db.close(); db.init(Path(tempfile.mkdtemp()) / "t.sqlite")
from modules import roles_extra as rx

# nickname logic
known = ["[АДМ]", "[МОД]"]
assert rx.strip_prefix("[АДМ] Вова", known, " ") == "Вова" and rx.strip_prefix("[АДМ] [МОД] Вова", known, " ") == "Вова"
assert rx.strip_prefix("[АДМ]Вова", known, " ") == "[АДМ]Вова" and rx.strip_prefix("Вова", known, " ") == "Вова"
assert rx.wanted_nick("vova", None, "[АДМ]", known, " ") == "[АДМ] vova"
assert rx.wanted_nick("vova", "[МОД] Вова", "[АДМ]", known, " ") == "[АДМ] Вова"            # swaps the old prefix
assert rx.wanted_nick("vova", "[АДМ] Вова", None, known, " ") == "Вова"                      # prefix removed, custom nick kept
assert rx.wanted_nick("vova", "[АДМ] vova", None, known, " ") is None                        # back to the account name -> clear nick
assert rx.wanted_nick("vova", None, None, known, " ") is None
assert len(rx.wanted_nick("x" * 40, None, "[АДМ]", known, " ")) == 32

class Role:
    def __init__(self, i, pos, managed=False): self.id, self.position, self.managed, self.name, self.mention = i, pos, managed, f"r{i}", f"<@&{i}>"
    def is_default(self): return False
    def __lt__(self, o): return self.position < o.position
    def __ge__(self, o): return self.position >= o.position
    def __hash__(self): return self.id
roles = {1: Role(1, 1), 2: Role(2, 2), 3: Role(3, 3), 90: Role(90, 90, managed=True), 50: Role(50, 50)}
class Member:
    def __init__(self, uid, rs=(), bot=False, nick=None, name="vova"):
        self.id, self.bot, self.roles, self.nick, self.name, self.mention, self.display_name = uid, bot, list(rs), nick, name, f"<@{uid}>", nick or name
        self.guild = guild; self.edits = []
    async def add_roles(self, *r, reason=None): self.roles += list(r)
    async def remove_roles(self, *r, reason=None): self.roles = [x for x in self.roles if x not in r]
    async def edit(self, nick=None, reason=None): self.nick = nick; self.edits.append(nick)
members = {}
guild = NS(id=1, owner_id=999, me=NS(top_role=Role(0, 10)), get_role=roles.get, get_member=lambda i: members.get(i), get_channel=lambda i: None)
def mk(uid, **kw): m = Member(uid, **kw); members[uid] = m; return m

async def main():
    cog = rx.RolesExtra(NS(guilds=[guild], get_guild=lambda i: guild))
    # ── sticky roles
    m = mk(1, rs=[roles[1], roles[2], roles[90]]); await cog.on_member_remove(m)
    assert db.query("SELECT 1 FROM sticky_roles") == []                                            # off by default
    modules.set_config(1, "sticky_roles", {"enabled": True, "ignored_roles": [2], "expire_days": 30})
    await cog.on_member_remove(m); assert rx.pop_roles(1, 1, 30) == [1]                            # managed + ignored dropped
    await cog.on_member_remove(m)
    back = mk(1); await cog.on_member_join(back); assert back.roles == [roles[1]]
    await cog.on_member_join(back); assert back.roles == [roles[1]]                                # record consumed
    await cog.on_member_remove(m); db.execute("UPDATE sticky_roles SET left_ts=?", (int(time.time()) - 40 * 86400,))
    late = mk(1); await cog.on_member_join(late); assert late.roles == []                          # expired
    roles[50].position = 50; await cog.on_member_remove(Member(5, rs=[roles[50], roles[1]]))
    hi = mk(5); await cog.on_member_join(hi); assert hi.roles == [roles[1]]                        # role above the bot is skipped
    bot_m = mk(6, bot=True); await cog.on_member_remove(bot_m); assert rx.pop_roles(1, 6, 0) == []

    # ── temp roles
    ctx = modules.ActionContext(guild=guild, bot=cog.bot, user_id=1, user_name="a")
    tm = mk(10)
    assert "на 1ч" in await modules.run_action("temp_roles", "add", {"user": 10, "role": 2, "duration": "1h"}, ctx) and roles[2] in tm.roles
    for bad in ({"user": 10, "role": 90, "duration": "1h"}, {"user": 10, "role": 50, "duration": "1h"},
                {"user": 10, "role": 2, "duration": "soon"}, {"user": 999, "role": 2, "duration": "1h"}):
        try: await modules.run_action("temp_roles", "add", bad, ctx); assert False, bad
        except modules.ValidationError: pass
    assert rx.due_temp(int(time.time())) == [] and len(rx.due_temp(int(time.time()) + 4000)) == 1
    db.execute("UPDATE temp_roles SET expires_ts=?", (int(time.time()) - 1,))
    await rx.RolesExtra.temp_loop.coro(cog); assert roles[2] not in tm.roles and rx.due_temp(int(time.time()) + 9999) == []
    rx.add_temp(1, 10, 2, int(time.time()) + 100); tm.roles.append(roles[2])
    assert modules.TABLE_PROVIDERS[("temp_roles", "active")](guild)[0]["role"] == "r2"
    assert "снята" in await modules.run_action("temp_roles", "remove", {"user": 10, "role": 2}, ctx) and roles[2] not in tm.roles
    try: await modules.run_action("temp_roles", "remove", {"user": 10, "role": 2}, ctx); assert False
    except modules.ValidationError: pass
    rx.add_temp(1, 777, 2, 1); await rx.RolesExtra.temp_loop.coro(cog); assert rx.due_temp(10**10) == []   # member left: row just disappears

    # ── prefixes
    assert "→" in await modules.run_action("role_prefixes", "add", {"role": 3, "prefix": "[АДМ]"}, ctx)
    await modules.run_action("role_prefixes", "add", {"role": 1, "prefix": "[ИГР]"}, ctx)
    for bad in ({"role": 90, "prefix": "x"}, {"role": 3, "prefix": ""}, {"role": 3, "prefix": "x" * 13}):
        try: await modules.run_action("role_prefixes", "add", bad, ctx); assert False
        except modules.ValidationError: pass
    assert [r["prefix"] for r in modules.TABLE_PROVIDERS[("role_prefixes", "rules")](guild)] == ["[АДМ]", "[ИГР]"]   # by role position
    p = mk(20, rs=[roles[1], roles[3]]); before = NS(roles=[roles[1]])
    await cog.on_member_update(before, p); assert p.edits == []                                    # module disabled
    modules.set_config(1, "role_prefixes", {"enabled": True})
    await cog.on_member_update(before, p); assert p.nick == "[АДМ] vova"                           # highest role wins
    p.roles = [roles[1]]; await cog.on_member_update(NS(roles=[roles[1], roles[3]]), p); assert p.nick == "[ИГР] vova"
    p.roles = []; await cog.on_member_update(NS(roles=[roles[1]]), p); assert p.nick is None
    owner = mk(999, rs=[roles[3]]); await cog.on_member_update(NS(roles=[]), owner); assert owner.edits == []   # owner nick can't be edited
    nochange = mk(21, rs=[roles[1]]); await cog.on_member_update(NS(roles=[roles[1]]), nochange); assert nochange.edits == []
    assert "удалено" in await modules.run_action("role_prefixes", "remove", {"role": 1}, ctx)
    try: await modules.run_action("role_prefixes", "remove", {"role": 1}, ctx); assert False
    except modules.ValidationError: pass
    # sticky panel bits
    rx.save_roles(1, 30, [1]); assert modules.TABLE_PROVIDERS[("sticky_roles", "saved")](guild)[0]["count"] == 1
    assert "удалены" in await modules.run_action("sticky_roles", "clear", {"user": 30}, ctx)
    try: await modules.run_action("sticky_roles", "clear", {"user": 30}, ctx); assert False
    except modules.ValidationError: pass
    print("roles_extra tests OK")

asyncio.run(main())
