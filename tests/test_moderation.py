"""Run: venv/bin/python -m tests.test_moderation"""
import tempfile, time
from pathlib import Path
from types import SimpleNamespace as NS

from core import db, modules
from modules import moderation as mod

db.close(); db.init(Path(tempfile.mkdtemp()) / "t.sqlite")

# durations
assert mod.parse_duration("10m") == 600 and mod.parse_duration("2h30m") == 9000
assert mod.parse_duration("1d") == 86400 and mod.parse_duration(" 1w ") == 604800
for bad in ("", "abc", "10", "5x", "10m junk", "0m"):
    assert mod.parse_duration(bad) is None, bad
assert mod.human_duration(93784) == "1д 2ч 3м 4с" and mod.human_duration(0) == "0с"

# cases: per-guild numbering, per-user lookup, tempban expiry
class U(NS):
    def __str__(self): return self.name
alice, bob, modr = U(id=1, name="alice"), U(id=2, name="bob"), U(id=9, name="mod")
assert mod.add_case(100, "mute", alice, modr, "spam", 600) == 1
assert mod.add_case(100, "kick", bob, modr, None) == 2
assert mod.add_case(200, "ban", alice, modr, "x") == 1                 # other guild starts at 1
n = mod.add_case(100, "tempban", alice, modr, "raid", 3600, active=True)
assert [c["case_no"] for c in mod.cases_for_user(100, 1)] == [3, 1]
assert mod.due_tempbans(int(time.time())) == []                       # not expired yet
due = mod.due_tempbans(int(time.time()) + 3601)
assert len(due) == 1 and due[0]["case_no"] == n
mod.close_case(100, n); assert mod.due_tempbans(int(time.time()) + 9999) == []
rows = mod.recent_cases(100)
assert rows[0]["type_label"] == "Временный бан" and rows[0]["user_name"] == "alice (1)"

# permissions
def member(gid, perms=(), roles=(), top=1, admin=False, mid=50):
    gp = NS(administrator=admin, **{p: True for p in perms})
    return NS(id=mid, guild=NS(id=gid), guild_permissions=gp, roles=[NS(id=r) for r in roles], top_role=top)
assert mod.may_use(member(100, perms=["moderate_members"]), "mute")
assert not mod.may_use(member(100), "mute")
assert mod.may_use(member(100, admin=True), "ban")
assert not mod.may_use(member(100, perms=["moderate_members"]), "ban")
modules.set_config(100, "moderation", {"enabled": True, "mod_roles": [77]})
assert mod.may_use(member(100, roles=[77]), "mute")                    # mod role works without perms
assert not mod.may_use(member(100, roles=[78]), "mute")

# hierarchy
guild = NS(owner_id=1000, me=NS(id=999, top_role=10))
Mem = lambda i, top: NS(id=i, top_role=top)
modr_m, low, high = Mem(5, 5), Mem(6, 3), Mem(7, 8)
assert mod.hierarchy_problem(guild, modr_m, low) is None
assert mod.hierarchy_problem(guild, modr_m, modr_m)                    # self
assert mod.hierarchy_problem(guild, modr_m, high)                      # higher than mod
assert mod.hierarchy_problem(guild, modr_m, NS(id=1000, top_role=1))   # owner
assert mod.hierarchy_problem(guild, modr_m, Mem(8, 12))           # above the bot
assert mod.hierarchy_problem(guild, modr_m, NS(id=3)) is None          # non-member user (ban by id)
print("moderation tests OK")
