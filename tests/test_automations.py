"""Run: venv/bin/python -m tests.test_automations"""
import asyncio, datetime, tempfile, time
from pathlib import Path
from types import SimpleNamespace as NS

import discord
from core import db, modules
db.close(); db.init(Path(tempfile.mkdtemp()) / "t.sqlite")
from modules import automations as au

MSK = au.MSK
rule = lambda **o: {"id": 1, "name": "r", "trigger": "message_keyword", "trigger_value": "hello", "trigger_channel": None,
                    "trigger_role": None, "action": "send_message", "target_channel": 5, "role_id": None, "text": "hi {user}",
                    "cooldown_s": 10, "enabled": 1, "last_run_ts": 0, "runs": 0, **o}

# validation of trigger/action combinations
ok = dict(name="n", trigger="member_join", action="send_message", target_channel=5, text="x")
au.validate_rule(ok)
bad = [
    ({**ok, "trigger": "nope"}, "Неизвестный"),
    ({**ok, "trigger": "schedule_daily", "trigger_value": "20:00", "action": "send_dm"}, "нужно участнику"),
    ({**ok, "action": "delete_message"}, "только в ответ на сообщение"),
    ({**ok, "trigger": "message_keyword"}, "слово"),
    ({**ok, "trigger": "role_added"}, "роль для триггера"),
    ({**ok, "trigger": "schedule_daily", "trigger_value": "25:99"}, "ЧЧ:ММ"),
    ({**ok, "trigger": "schedule_interval", "trigger_value": "0"}, "Интервал"),
    ({**ok, "text": ""}, "текст"),
    ({**ok, "target_channel": None}, "канал"),
    ({**ok, "action": "add_role"}, "роль для действия"),
    ({**ok, "trigger": "message_keyword", "trigger_value": "x", "action": "react", "text": ""}, "эмодзи"),
    ({**ok, "text": "x" * 2000}, "длинный"),
]
for f, frag in bad:
    try: au.validate_rule(f); assert False, f
    except modules.ValidationError as e: assert frag.lower() in str(e).lower(), (frag, str(e))
au.validate_rule({**ok, "trigger": "schedule_interval", "trigger_value": "30"})
au.validate_rule({**ok, "trigger": "message_keyword", "trigger_value": "x", "action": "react", "text": "👍"})

# rendering
member = NS(mention="<@5>", display_name="Vova"); guild = NS(name="Fam", member_count=42)
assert au.render("Привет {user} ({name}) в {server}, нас {count}", member=member, guild=guild) == "Привет <@5> (Vova) в Fam, нас 42"
assert au.render("{user}{channel}") == ""

# keyword / cooldown
assert au.keyword_hit(rule(), "Oh HELLO there", 1) and not au.keyword_hit(rule(), "bye", 1)
assert au.keyword_hit(rule(trigger_channel=7), "hello", 7) and not au.keyword_hit(rule(trigger_channel=7), "hello", 8)
assert au.cooled_down(rule(last_run_ts=100), 111) and not au.cooled_down(rule(last_run_ts=100), 105)

# schedules
at = lambda h, m: datetime.datetime(2026, 10, 5, h, m, tzinfo=MSK)
daily = rule(trigger="schedule_daily", trigger_value="20:00")
assert not au.schedule_due(daily, at(19, 59), 0)
assert au.schedule_due(daily, at(20, 0), 0)                                     # never ran -> due at 20:00
ran_today = int(at(20, 0).timestamp()) + 5
assert not au.schedule_due({**daily, "last_run_ts": ran_today}, at(21, 0), 0)   # already ran after today's target
yesterday = int(at(20, 0).timestamp()) - 86400
assert au.schedule_due({**daily, "last_run_ts": yesterday}, at(20, 1), 0)       # ran yesterday -> due today
interval = rule(trigger="schedule_interval", trigger_value="30")
assert au.schedule_due({**interval, "last_run_ts": 0}, at(1, 0), 10_000) and not au.schedule_due({**interval, "last_run_ts": 9_000}, at(1, 0), 10_000)
assert au.describe_rule(daily)[0] == "Каждый день в 20:00 (МСК)" and au.describe_rule(interval)[0] == "Каждые 30 мин"

# engine with fakes
class Chan:
    id = 5; mention = "<#5>"
    def __init__(self): self.sent = []
    async def send(self, text, **kw): self.sent.append(text)
class Role: 
    def __init__(self, i, name): self.id, self.name = i, name
class Mem:
    def __init__(self, uid): self.id, self.mention, self.display_name, self.roles, self.dms, self.bot = uid, f"<@{uid}>", f"u{uid}", [], [], False
    async def add_roles(self, r, reason=None): self.roles.append(r)
    async def remove_roles(self, r, reason=None): self.roles.remove(r)
    async def send(self, text): self.dms.append(text)
class Msg:
    def __init__(self, author, content, ch): self.author, self.content, self.channel, self.guild = author, content, ch, G; self.deleted, self.reactions = False, []
    async def delete(self): self.deleted = True
    async def add_reaction(self, e): self.reactions.append(e)
chan, vip = Chan(), Role(9, "VIP")
users = {1: Mem(1), 2: Mem(2)}
G = NS(id=1, name="Fam", member_count=3, get_channel=lambda i: chan if i == 5 else None, get_role=lambda i: vip if i == 9 else None,
       get_member=users.get, guilds=[], bots=[])

for _u in users.values(): _u.guild = G

async def main():
    modules.set_config(1, "automations", {"enabled": True})
    cog = au.Automations(NS(guilds=[G]))
    ctx = modules.ActionContext(guild=G, bot=None, user_id=1, user_name="admin")
    add = lambda **p: modules.run_action("automations", "add", p, ctx)

    assert "создано" in await add(name="welcome", trigger="member_join", action="send_message", channel=5, text="Привет {user}!")
    await add(name="autorole", trigger="member_join", action="add_role", role=9)
    await add(name="dm", trigger="member_join", action="send_dm", text="Добро пожаловать, {name}")
    await add(name="kw", trigger="message_keyword", value="реклама", action="delete_message")
    await add(name="react", trigger="message_keyword", value="спасибо", action="react", text="🙏", cooldown=0)
    await add(name="bye", trigger="member_leave", action="send_message", channel=5, text="{name} вышел")
    try: await add(name="bad", trigger="member_join", action="react", text="x"); assert False
    except modules.ValidationError: pass
    try: await add(name="", trigger="member_join", action="send_message", channel=5, text="x"); assert False
    except modules.ValidationError: pass

    await cog.on_member_join(users[2])
    assert chan.sent == ["Привет <@2>!"] and users[2].roles == [vip] and users[2].dms == ["Добро пожаловать, u2"]
    await cog.on_member_remove(users[2]); assert chan.sent[-1] == "u2 вышел"

    m = Msg(users[1], "всем РЕКЛАМА тут", chan); await cog.on_message(m); assert m.deleted
    m = Msg(users[1], "обычный текст", chan); await cog.on_message(m); assert not m.deleted
    bot_msg = Msg(NS(bot=True, id=9), "реклама", chan); await cog.on_message(bot_msg); assert not bot_msg.deleted
    m1 = Msg(users[1], "спасибо", chan); await cog.on_message(m1); assert m1.reactions == ["🙏"]

    # cooldown: second "реклама" within 10 s is ignored
    m2 = Msg(users[1], "реклама", chan); await cog.on_message(m2); assert not m2.deleted

    # role triggers
    await add(name="vipwelcome", trigger="role_added", trigger_role=9, action="send_message", channel=5, text="{name} теперь VIP")
    before = NS(roles=[], guild=G); after = NS(roles=[vip], guild=G, id=1, mention="<@1>", display_name="u1")
    n = len(chan.sent); await cog.on_member_update(before, after); assert chan.sent[n:] == ["u1 теперь VIP"]
    await cog.on_member_update(after, after); assert len(chan.sent) == n + 1                  # no change -> nothing

    # panel table + toggle/remove + disabled module
    rows = modules.TABLE_PROVIDERS[("automations", "rules")](G)
    assert len(rows) == 7 and rows[0]["name"] == "welcome" and rows[0]["runs"] == 1 and rows[3]["when"] == "Сообщение содержит «реклама»"
    assert "выключено" in await modules.run_action("automations", "toggle", {"id": rows[0]["id"]}, ctx)
    n = len(chan.sent); users[2].roles.clear(); users[2].dms.clear()
    await cog.on_member_join(users[2]); assert chan.sent[n:] == [] and users[2].roles == [vip]   # welcome off, autorole still on
    assert "включено" in await modules.run_action("automations", "toggle", {"id": rows[0]["id"]}, ctx)
    assert "удалено" in await modules.run_action("automations", "remove", {"id": rows[0]["id"]}, ctx)
    try: await modules.run_action("automations", "remove", {"id": 999}, ctx); assert False
    except modules.ValidationError: pass
    modules.set_config(1, "automations", {"enabled": False})
    n = len(chan.sent); await cog.on_member_join(users[2]); assert len(chan.sent) == n         # module off -> silent

    # schedule loop: interval rule fires once, then waits
    modules.set_config(1, "automations", {"enabled": True})
    await add(name="tick", trigger="schedule_interval", value="1", action="send_message", channel=5, text="tick")
    G.guilds = [G]; cog.bot = NS(guilds=[G])
    n = len(chan.sent)
    await au.Automations.schedule_loop.coro(cog); assert chan.sent[n:] == ["tick"]
    await au.Automations.schedule_loop.coro(cog); assert chan.sent[n:] == ["tick"]            # not due again for a minute

    # rule limit
    for i in range(au.MAX_RULES): 
        try: au.add_rule(1, name=f"x{i}", trigger="member_join", action="send_message", target_channel=5, text="x")
        except modules.ValidationError: break
    try: au.add_rule(1, name="over", trigger="member_join", action="send_message", target_channel=5, text="x"); assert False
    except modules.ValidationError as e: assert "Максимум" in str(e)
    print("automations tests OK")

asyncio.run(main())
