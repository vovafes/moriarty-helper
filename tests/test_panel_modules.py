"""Run: venv/bin/python -m tests.test_panel_modules
Runs in a temp working dir so save_data()/pvp config never touch the real data.json."""
import asyncio, os, tempfile
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
os.chdir(tempfile.mkdtemp())            # legacy save_data() writes data.json relative to the cwd
import sys; sys.path.insert(0, str(ROOT))

import discord
from core import db, modules
db.close(); db.init(Path(tempfile.mkdtemp()) / "t.sqlite")
import main  # noqa  (registers legacy modules + panel modules)
import legacy.state as st
import panel_modules  # noqa

G = 1
cfg = lambda k: modules.get_config(G, k)
setc = lambda k, **kw: modules.set_config(G, k, kw)

# 1. every panel module loads on a completely empty state
assert len(modules.REGISTRY) >= 21
legacy_keys = [k for k, m in modules.REGISTRY.items() if m.loader is not None or m.actions or m.tables]
for k in ("afk", "economy", "roulette"):                      # table/action-only modules have no settings loader
    assert k in legacy_keys and modules.REGISTRY[k].loader is None
for k in legacy_keys:
    c = cfg(k); modules.describe(k)
    assert "enabled" in c

# 2. round trips into the legacy dicts
setc("tickets", manager_role=10, ping_role=11, log_channel=12, interview_channel=13, viewer_roles=[20, 21],
     title="T", desc="D", image="")
assert st.ticket_manager_roles[G] == 10 and st.ticket_ping_role[G] == 11 and st.reject_log_channels[G] == 12
assert st.interview_channels[G] == 13 and st.ticket_viewer_roles[G] == [20, 21]
assert st.ticket_texts[G] == {"title": "T", "desc": "D", "image": ""}
assert cfg("tickets")["viewer_roles"] == [20, 21]
setc("tickets", manager_role=None, viewer_roles=[])
assert G not in st.ticket_manager_roles and G not in st.ticket_viewer_roles          # cleared == unset

setc("events", mp=1, vzp=2, vzh=3, reaki=4, can_vzp=[5], faction="mafia")
assert st.mp_roles[G] == 1 and st.vzp_roles[G] == 2 and st.vzh_roles[G] == 3 and st.event_roles[G] == 4
assert st.event_command_roles[G] == {"vzp": [5]} and st.vzh_schedule_settings[G] == "mafia"
assert cfg("events")["can_vzp"] == [5] and cfg("events")["can_list"] == []

setc("warns", role1=31, role3=33, log_channel=34)
assert st.warn_roles[G] == {1: 31, 3: 33} and st.warn_log_channels[G] == 34
assert cfg("warns")["role2"] is None and cfg("warns")["role3"] == 33

setc("cabinet", text="hello", image_url="http://x/y.png", invite="https://discord.gg/abc")
assert st.cabinet_panels[G]["text"] == "hello" and st.cabinet_invite_links[G] == "https://discord.gg/abc"
setc("obshak", log_channel=40, ping_role=41, text="")
assert st.obshak_log_channels[G] == 40 and st.obshak_ping_roles[G] == 41 and st.obshak_panels[G]["text"] is None
setc("feedback", log_channel=50, ping_role=51, text="fb")
assert st.feedback_settings[G]["log_channel_id"] == 50 and st.feedback_settings[G]["text"] == "fb"
setc("contracts", role=60); assert st.contract_roles[G] == 60

setc("voice_rewards", amount=7, amount_game=9, game_name=" RAGE ", categories=[1], excluded=[2])
vs = st.voice_reward_settings[G]
assert (vs["amount"], vs["amount_game"], vs["game_name"], vs["categories"], vs["excluded_channels"]) == (7, 9, "RAGE", [1], [2])

setc("shop", manager_role=70, log_channel=71)
assert st.shop_manager_roles[G] == 70 and st.shop_log_channels[G] == 71
setc("branding", footer_icon="http://i/f.png"); assert st.guild_branding[G] == {"footer_icon": "http://i/f.png"}
setc("admin_access", admin_role=80, extra_roles=[81]); assert st.admin_roles[G] == 80 and st.extra_admin_roles[G] == [81]
setc("backup", channel=90, interval="6", files=["data.json"])
assert st.backup_settings[G]["interval_hours"] == 6 and st.backup_settings[G]["files"] == ["data.json"]
assert cfg("backup")["interval"] == "6"
try: setc("backup", files=["../etc/passwd"]); assert False
except modules.ValidationError: pass
setc("roster", member_role=1, academy_role=2, channel=3); assert st.roster_settings[G]["academy_role_id"] == 2

try: setc("private_vc", create_channel=1); assert False                       # partial config is rejected
except modules.ValidationError: pass
setc("private_vc", create_channel=1, category=2, panel_channel=3)
assert st.private_vc_settings[G] == {"create_channel_id": 1, "category_id": 2, "panel_channel_id": 3}

try: setc("voice_presence", enabled=True); assert False                       # needs a channel first
except modules.ValidationError: pass
async def vp():
    setc("voice_presence", channel=5, enabled=False)
    assert st.voice_presence_settings[G] == {"channel_id": 5, "enabled": False}
asyncio.run(vp())

try: setc("vzp_monitor", server_id=1); assert False                           # no family bound yet
except modules.ValidationError: pass
st.vzp_monitor_config[G] = {"familyId": 7, "familyName": "Fam", "serverId": None, "alertChannelId": None,
                            "resultsChannelId": None, "mentionRoles": [], "mentionUsers": [], "pollInterval": 20,
                            "monitoringEnabled": False}
setc("vzp_monitor", server_id=20, alert_channel=1, results_channel=2, mention_users="<@5> 6,7", enabled=True)
c = st.vzp_monitor_config[G]
assert c["serverId"] == 20 and c["mentionUsers"] == [5, 6, 7] and c["monitoringEnabled"] is True and c["familyId"] == 7
assert cfg("vzp_monitor")["family"] == "Fam (ID 7)" and cfg("vzp_monitor")["enabled"] is True

setc("pvp", channel=99, events=["airdrop"], image_url="")
import pvp_module
g = pvp_module.guild_config(pvp_module.load_config(), G)
assert g["channel_id"] == 99 and g["events"]["airdrop"] is True and not all(g["events"].values())
assert cfg("pvp")["events"] == ["airdrop"]

# 3. actions + tables with a fake guild
class Member:
    def __init__(self, uid): self.id, self.display_name, self.roles, self.sent = uid, f"user{uid}", [], []
    async def remove_roles(self, *r, reason=None): self.removed = list(r)
    async def add_roles(self, *r, reason=None): self.added = list(r)
    async def send(self, **kw): self.sent.append(kw)
members = {5: Member(5), 6: Member(6)}
roles = {31: NS(id=31, name="Warn1", mention="<@&31>"), 33: NS(id=33, name="Warn3", mention="<@&33>")}
class Chan:
    def __init__(self): self.sent = []; self.id = 77; self.name = "panel"
    async def send(self, **kw): self.sent.append(kw); return NS(id=555)
chan = Chan()
guild = NS(id=G, name="Fake", get_member=members.get, get_role=roles.get, get_channel=lambda i: chan if i == 77 else None)
ctx = modules.ActionContext(guild=guild, bot=main.bot, user_id=1, user_name="admin")
run = lambda key, act, **p: asyncio.run(modules.run_action(key, act, p, ctx))

assert "5" in run("economy", "points_give", user=5, amount=50) and st.points_db[G][5] == 50
assert "Снято 50" in run("economy", "points_take", user=5, amount=999) and st.points_db[G][5] == 0   # clamps at 0
run("economy", "chips_give", user=6, amount=10)
rows = modules.TABLE_PROVIDERS[("economy", "balances")](guild)
assert {r["user"] for r in rows} == {"user5 (5)", "user6 (6)"} and next(r for r in rows if "6" in r["user"])["chips"] == 10
try: run("economy", "points_give", user=999, amount=1); assert False             # not on the server
except modules.ValidationError: pass

run("warns", "issue", user=5, level="2", reason="spam")
assert st.warns_db[G][5]["warns"] == 2 and st.warns_db[G][5]["reason"] == "spam" and members[5].sent
assert modules.TABLE_PROVIDERS[("warns", "list")](guild)[0]["warns"] == "2/3"
run("warns", "remove", user=5)
assert modules.TABLE_PROVIDERS[("warns", "list")](guild) == []
try: run("warns", "remove", user=5); assert False
except modules.ValidationError: pass

run("shop", "add", name="Снять варн", price=500, action="remove_warn", emoji="", description="d")
iid = next(iter(st.guild_shop_items[G]))
assert st.guild_shop_items[G][iid]["price"] == 500 and st.guild_shop_items[G][iid]["emoji"] == "🛒"
try: run("shop", "add", name="Role", price=1, action="give_role"); assert False   # give_role needs a role
except modules.ValidationError: pass
assert modules.TABLE_PROVIDERS[("shop", "items")](guild)[0]["id"] == iid
run("shop", "remove", id=iid); assert st.guild_shop_items[G] == {}

run("roulette", "set", role=31, mult=2.5); assert st.casino_role_luck[G] == {31: 2.5}
assert modules.TABLE_PROVIDERS[("roulette", "luck")](guild)[0]["mult"] == "2.5x"
run("roulette", "reset", role=31); assert st.casino_role_luck[G] == {}
try: run("roulette", "set", role=31, mult=11); assert False
except modules.ValidationError: pass

st.afk_list[G] = {5: {"reason": "r", "return_time": "20:00", "since": __import__("datetime").datetime(2026, 1, 1, 12, 0)}}
assert modules.TABLE_PROVIDERS[("afk", "afk")](guild)[0]["since"] > 1_700_000_000
run("afk", "remove_afk", user=5); assert st.afk_list[G] == {}
try: run("afk", "remove_afk", user=5); assert False
except modules.ValidationError: pass

async def publish():
    res = await modules.run_action("shop", "publish", {"channel": 77}, ctx)
    assert "panel" in res and st.shop_panels[G]["message_id"] == 555 and chan.sent
    try: await modules.run_action("shop", "publish", {"channel": 12345}, ctx); assert False
    except modules.ValidationError: pass
asyncio.run(publish())

# audit trail written for actions
assert any(a["action"] == "action:points_give" for a in db.audit_page(G, 200))
print("panel module tests OK")
