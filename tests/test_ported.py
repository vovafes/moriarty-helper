"""Run: venv/bin/python -m tests.test_ported  -- modules ported from the AlphaCloud panel"""
import asyncio, random, tempfile
from pathlib import Path

import discord
from discord.ext import commands

from core import db, modules

db.close(); db.init(Path(tempfile.mkdtemp()) / "t.sqlite")

from modules import community_tools as ct, server_tools as st, support_team as sp

# rules field: coercion, defaults, limits
f = modules.REGISTRY["auto_responder"].fields[0]
out = modules._coerce(f, [{"trigger": "hi", "response": "yo"}])
assert out == [{"trigger": "hi", "match": "contains", "response": "yo", "cooldown": 30}], out
for bad in ("x", [1], [{"trigger": "a"}] * 51):
    try:
        modules._coerce(f, bad); raise SystemExit(f"accepted {bad!r}")
    except modules.ValidationError:
        pass
modules.set_config(1, "auto_responder", {"enabled": True, "rules": [{"trigger": "a", "response": "b"}]})
assert modules.get_config(1, "auto_responder")["rules"][0]["trigger"] == "a"

# auto slowmode steps
assert st.next_slow_level(0, 30, 20, 30) == 5
assert st.next_slow_level(30, 30, 20, 30) == 30          # capped by the configured maximum
assert st.next_slow_level(15, 2, 20, 30) == 10
assert st.next_slow_level(0, 1, 20, 30) == 0
assert st.next_slow_level(10, 15, 20, 30) == 10          # between half and full threshold: hold

# responder matching
assert st.trigger_matches({"trigger": "Привет"}, "ну привет всем")
assert not st.trigger_matches({"trigger": "привет", "match": "exact"}, "привет всем")
assert st.trigger_matches({"trigger": "привет", "match": "starts"}, "Привет всем")
assert not st.trigger_matches({"trigger": ""}, "x")
assert st.temp_name("Канал {name}", "Вова") == "Канал Вова" and len(st.temp_name("x" * 300, "a")) == 100
assert st.human_slow(0) == "выключен" and st.human_slow(120) == "2 мин"

# blackjack / roulette / counting / coinflip
assert ct.hand_value([1, 13]) == 21 and ct.hand_value([1, 1, 9]) == 21 and ct.hand_value([10, 10, 5]) == 25
assert ct.roulette_payout(0, "green", 10) == 140 and ct.roulette_payout(1, "red", 10) == 10
assert ct.roulette_payout(2, "red", 10) == -10 and ct.roulette_payout(0, "even", 10) == -10
assert ct.roulette_payout(17, "17", 10) == 350 and ct.roulette_payout(17, "odd", 10) == 10
assert ct.counting_step(4, 1, 2, "5") == ("ok", 5)
assert ct.counting_step(4, 2, 2, "5") == ("same_user", 0)
assert ct.counting_step(4, 1, 2, "9") == ("wrong", 0) and ct.counting_step(4, 1, 2, "hello")[0] == "ignore"
won, side = ct.coinflip_win(random.Random(1), "heads"); assert side in ("heads", "tails") and won == (side == "heads")
assert ct.parse_color("#BE1FB6") == 0xBE1FB6 and ct.parse_color("zzz") == ct.COLOR and ct.parse_color(None, 5) == 5

# wallet
w = ct.wallet(1, 2, 500); assert w["balance"] == 500
ct.add_balance(1, 2, -9999); assert ct.wallet(1, 2, 500)["balance"] == 0     # never negative
ct.add_balance(1, 2, 70); assert ct.wallet(1, 2, 500)["balance"] == 70

# votes
db.execute("INSERT INTO vouches (guild_id, target_id, voter_id, positive, ts) VALUES (1,9,1,1,0),(1,9,2,0,0),(1,9,3,1,0)")
assert ct.vouch_counts(1, 9) == (2, 1) and ct.vouch_counts(1, 8) == (0, 0)

# support helpers
q = sp.queue_order([{"user_id": 1, "joined": 5, "vip": False}, {"user_id": 2, "joined": 9, "vip": True}, {"user_id": 3, "joined": 1, "vip": False}])
assert [e["user_id"] for e in q] == [2, 3, 1]
assert sp.conversation_counts(300, 160, 3) and not sp.conversation_counts(300, 100, 3) and not sp.conversation_counts(100, 100, 3)
assert sp.human_wait(75) == "1 мин 15 с" and sp.human_wait(9) == "9 с"
assert sp.msk_ymd(0) == "1970-01-01" and sp.msk_weekday("2026-10-12") == 0
db.execute("INSERT INTO support_hours (guild_id, user_id, ymd, minutes) VALUES (1,5,?,90),(1,6,?,30)", (sp.msk_ymd(), sp.msk_ymd()))
rows = sp.hours_rows(1, 7); assert [r["user_id"] for r in rows] == [5, 6] and sum(sp.weekday_minutes(1, 7)) == 120

# every new module's schema is sane, and the cogs load without command/decorator errors
async def load():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
    for ext in ("modules.server_tools", "modules.community_tools", "modules.support_team"):
        await bot.load_extension(ext)
    assert [c.name for c in bot.tree.get_commands()] == ["tools"]         # one top-level slot only (Discord caps at 100)
    sub = {c.name for c in bot.tree.get_command("tools").commands}
    for need in ("cmd", "todo", "vouch", "blackjack", "queue", "call", "absence", "team_kick", "daily", "changelog"):
        assert need in sub, need
    assert any(p.autocomplete for p in bot.tree.get_command("tools").get_command("cmd")._params.values())
    await bot.close()
asyncio.run(load())

for key in ("auto_slowmode", "temp_voice", "role_dm", "auto_publish", "auto_ping", "auto_responder", "custom_commands", "embed_creator",
            "changelog", "newsletter", "todo", "partnership", "voucher", "minigames", "support_notify", "support_sort", "support_hours",
            "support_call", "voice_activity", "support_entries", "teamlist", "team_updates", "absences", "ap", "frak"):
    d = modules.describe(key)
    assert d["title"] and d["category"] in ("moderation", "community", "support", "team", "tools"), key
    for fld in d["fields"]:
        assert fld["type"] in ("bool", "text", "longtext", "number", "select", "multiselect", "user", "channel", "role", "channels", "roles", "rules"), (key, fld)
        modules._coerce(fld, fld["default"])           # defaults must pass their own validation
    for t in d["tables"]:
        assert (key, t["id"]) in modules.TABLE_PROVIDERS, (key, t["id"])
print("ok")
