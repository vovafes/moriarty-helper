"""Run: venv/bin/python -m tests.test_anti_nuke"""
import asyncio, tempfile
from pathlib import Path
from types import SimpleNamespace as NS

import discord
from core import db, modules
from modules import anti_nuke as an

db.close(); db.init(Path(tempfile.mkdtemp()) / "t.sqlite")
A = discord.AuditLogAction

# tracker: window, per-actor isolation, reset
t = an.ThreatTracker()
assert t.add(1, 5, 3, now=0) == 3 and t.add(1, 5, 3, now=10) == 6
assert t.add(1, 5, 3, now=100, window=60) == 3          # the first two fell out of the window
assert t.add(1, 6, 2, now=100) == 2                     # other actor unaffected
t.reset(1, 5); assert t.add(1, 5, 1, now=101) == 1

assert an.whitelisted_ids("12 abc\n34") == {12, 34}

# entry weighting
perms = lambda admin: NS(administrator=admin)
mk = lambda action, **kw: NS(action=action, before=kw.get("before", NS()), after=kw.get("after", NS()))
assert an.AntiNuke.entry_weight(mk(A.channel_delete)) == (3, A.channel_delete)
assert an.AntiNuke.entry_weight(mk(A.ban))[0] == 2
assert an.AntiNuke.entry_weight(mk(A.message_delete)) is None
assert an.AntiNuke.entry_weight(mk(A.role_update, before=NS(permissions=perms(False)), after=NS(permissions=perms(True))))[0] == 6
assert an.AntiNuke.entry_weight(mk(A.role_update, before=NS(permissions=perms(True)), after=NS(permissions=perms(True)))) is None
assert an.AntiNuke.entry_weight(mk(A.member_role_update, after=NS(roles=[NS(permissions=perms(True))])))[0] == 6
assert an.AntiNuke.entry_weight(mk(A.member_role_update, after=NS(roles=[NS(permissions=perms(False))]))) is None

# trust rules
cfg = {f["key"]: f["default"] for f in modules.REGISTRY["anti_nuke"].fields}
guild = NS(owner_id=1)
assert an.is_trusted(guild, NS(id=1), cfg, 99) and an.is_trusted(guild, NS(id=99), cfg, 99)
assert not an.is_trusted(guild, NS(id=2, roles=[]), cfg, 99)
assert an.is_trusted(guild, NS(id=2, roles=[]), {**cfg, "whitelist_users": "2"}, 99)
assert an.is_trusted(guild, NS(id=2, roles=[NS(id=7)]), {**cfg, "whitelist_roles": [7]}, 99)

# full flow with fakes
class Role:
    def __init__(self, i, admin=False, managed=False, pos=1):
        self.id, self.managed, self.pos = i, managed, pos
        self.permissions = NS(administrator=admin, manage_guild=False, manage_roles=False, manage_channels=False,
                              ban_members=False, kick_members=False, manage_webhooks=False, mention_everyone=False)
    def is_default(self): return False
    def __lt__(self, o): return self.pos < o.pos

class Member:
    def __init__(self, i, roles): self.id, self.roles, self.removed = i, roles, []; self.mention = f"<@{i}>"
    def __str__(self): return f"m{i}" if False else "attacker"
    async def remove_roles(self, *r, reason=None): self.removed += list(r)

class Chan:
    def __init__(self): self.sent = []
    async def send(self, content=None, embed=None, **kw): self.sent.append(embed)

def entry(guild, actor, action, target_id=None, name=None):
    tgt = NS(id=target_id, name=name) if target_id else None
    return NS(guild=guild, user=actor, action=action, target=tgt, before=NS(), after=NS())

async def flow():
    alert = Chan()
    admin_role, plain = Role(10, admin=True, pos=2), Role(11, pos=1)
    attacker = Member(50, [admin_role, plain])
    created = []
    async def create_text_channel(name, **kw): created.append(("text", name))
    async def create_role(name, **kw): created.append(("role", name))
    guild = NS(id=1, owner_id=1, me=NS(top_role=Role(0, pos=99)), get_member=lambda i: attacker if i == 50 else None,
               get_channel=lambda i: alert if i == 777 else None, get_role=lambda i: None,
               create_text_channel=create_text_channel, create_role=create_role,
               channels=[], roles=[])
    bot = NS(user=NS(id=99), guilds=[guild])
    cog = an.AntiNuke(bot)
    cog.channels[1] = {201: {"name": "general", "type": discord.ChannelType.text, "position": 0, "category_id": None,
                             "topic": None, "nsfw": False, "slowmode": 0, "bitrate": None, "user_limit": None, "overwrites": {}}}
    cog.roles[1] = {301: {"name": "VIP", "color": discord.Color.default(), "permissions": discord.Permissions.none(),
                          "hoist": False, "mentionable": False}}

    # disabled by default -> ignored even if it would trip
    for _ in range(4): await cog.on_audit_log_entry_create(entry(guild, attacker, A.channel_delete, 201, "general"))
    assert not attacker.removed and not alert.sent

    modules.set_config(1, "anti_nuke", {"enabled": True, "alert_channel": 777, "threshold": 8, "action": "strip", "restore": True})
    await cog.on_audit_log_entry_create(entry(guild, attacker, A.channel_delete, 201, "general"))     # 3
    await cog.on_audit_log_entry_create(entry(guild, attacker, A.role_delete, 301, "VIP"))            # 6
    assert not attacker.removed and not alert.sent                                                    # below threshold
    await cog.on_audit_log_entry_create(entry(guild, attacker, A.ban, 60))                            # 8 -> trip
    assert attacker.removed == [admin_role]                                                           # only the dangerous role
    assert ("text", "general") in created and ("role", "VIP") in created                              # rebuilt
    assert len(alert.sent) == 1 and "остановлен" in alert.sent[0].title
    assert attacker.id not in [k[1] for k in cog._tripped]

    # trusted actor never counts
    owner = Member(1, [admin_role]); guild.get_member = lambda i: owner if i == 1 else attacker
    for _ in range(5): await cog.on_audit_log_entry_create(entry(guild, owner, A.channel_delete, 201, "general"))
    assert not owner.removed and len(alert.sent) == 1

    # alert-only mode touches nothing
    modules.set_config(1, "anti_nuke", {"action": "alert", "restore": False})
    created.clear(); attacker.removed.clear()
    for tid in (1, 2, 3): await cog.on_audit_log_entry_create(entry(guild, attacker, A.channel_delete, 201, "general"))
    assert not attacker.removed and not created and len(alert.sent) == 2
asyncio.run(flow())
print("anti_nuke tests OK")
