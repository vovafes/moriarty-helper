"""Run: venv/bin/python -m tests.test_guards"""
import asyncio, datetime, tempfile
from pathlib import Path
from types import SimpleNamespace as NS

import discord
from core import db, modules
db.close(); db.init(Path(tempfile.mkdtemp()) / "t.sqlite")
from modules import guards as gd

utc = datetime.timezone.utc
now = datetime.datetime(2026, 10, 5, tzinfo=utc)
assert gd.is_account_too_new(now - datetime.timedelta(days=3), 7, now) and not gd.is_account_too_new(now - datetime.timedelta(days=8), 7, now)

class Member:
    def __init__(self, uid, admin=False, manage=False, bot=False, created=None):
        self.__dict__.update(id=uid, bot=bot, mention=f"<@{uid}>", dms=[], actions=[], created_at=created or now - datetime.timedelta(days=400),
                             guild_permissions=NS(administrator=admin, manage_messages=manage))
    async def send(self, text): self.dms.append(text)
    async def kick(self, reason=None): self.actions.append(("kick", reason))
    async def timeout(self, d, reason=None): self.actions.append(("timeout", d))
class Chan:
    def __init__(self, i): self.id, self.sent, self.purged = i, [], []
    async def send(self, content=None, **kw): self.sent.append(kw.get("embed") or content)
    async def purge(self, **kw): self.purged.append(kw); return []
trap, log = Chan(10), Chan(11)
class Guild:
    id = 1; bans = []
    def get_channel(self, i): return {10: trap, 11: log}.get(i)
    async def ban(self, m, reason=None, delete_message_seconds=0): self.bans.append((m.id, delete_message_seconds))
    async def kick(self, m, reason=None): m.actions.append(("kick", reason))
guild = Guild()
class Msg:
    def __init__(self, author, channel=trap, content="spam", mentions=(), role_mentions=(), age=5):
        self.guild, self.author, self.channel, self.content = guild, author, channel, content
        self.mentions, self.role_mentions, self.deleted = list(mentions), list(role_mentions), False
        self.created_at = discord.utils.utcnow() - datetime.timedelta(seconds=age)
    async def delete(self): self.deleted = True

async def main():
    cog = gd.Guards(NS())
    # honeypot: off by default
    u = Member(1); m = Msg(u); await cog.on_message(m); assert not m.deleted
    modules.set_config(1, "honeypot", {"enabled": True, "channel": 10, "action": "timeout", "log_channel": 11})
    await cog.on_message(m)
    assert m.deleted and u.actions[0][0] == "timeout" and u.actions[0][1] == datetime.timedelta(days=1) and trap.purged
    assert log.sent and "таймаут" in log.sent[0].fields[1].value
    for exempt in (Member(2, admin=True), Member(3, manage=True), Member(4, bot=True)):
        x = Msg(exempt); await cog.on_message(x); assert not x.deleted and exempt.actions == []
    other = Msg(Member(5), channel=Chan(99)); await cog.on_message(other); assert not other.deleted        # other channels are fine
    modules.set_config(1, "honeypot", {"action": "ban", "purge_hours": 2}); b = Member(6)
    await cog.on_message(Msg(b)); assert guild.bans == [(6, 7200)]
    modules.set_config(1, "honeypot", {"action": "kick"}); k = Member(7); await cog.on_message(Msg(k)); assert k.actions[0][0] == "kick"

    # failure to punish is reported, not raised
    class Locked(Member):
        async def timeout(self, d, reason=None): raise discord.Forbidden(NS(status=403, reason="x"), "no")
    modules.set_config(1, "honeypot", {"action": "timeout"}); l = Locked(8); await cog.on_message(Msg(l))
    assert "не применено" in log.sent[-1].fields[1].value

    # account age
    young = Member(20, created=datetime.datetime.now(utc) - datetime.timedelta(days=2))
    old = Member(21); young.guild = old.guild = guild
    await cog.on_member_join(young); assert young.actions == []                                           # off by default
    modules.set_config(1, "account_age", {"enabled": True, "min_days": 7, "log_channel": 11})
    await cog.on_member_join(old); assert old.actions == [] and old.dms == []
    await cog.on_member_join(young); assert young.actions == [("kick", "Аккаунт младше 7 дн.")] and "7 дн" in young.dms[0]
    bot_m = Member(22, bot=True, created=datetime.datetime.now(utc)); bot_m.guild = guild
    await cog.on_member_join(bot_m); assert bot_m.actions == []
    assert "кикнут" in log.sent[-1].description

    # ghost ping
    a, victim = Member(30), Member(31)
    role = NS(mention="<@&5>")
    msg = Msg(a, channel=Chan(40), mentions=[victim], role_mentions=[role], age=10)
    assert gd.ghost_targets(msg) == ["<@31>", "<@&5>"]
    assert gd.ghost_targets(Msg(a, mentions=[a])) == [] and gd.ghost_targets(Msg(a, mentions=[Member(32, bot=True)])) == []
    await cog.on_message_delete(msg); assert msg.channel.sent == []                                       # off by default
    modules.set_config(1, "ghost_ping", {"enabled": True, "max_age": 60})
    await cog.on_message_delete(msg); assert msg.channel.sent and "<@31>" in msg.channel.sent[0].description
    old_msg = Msg(a, channel=Chan(41), mentions=[victim], age=3600); await cog.on_message_delete(old_msg); assert old_msg.channel.sent == []
    plain = Msg(a, channel=Chan(42)); await cog.on_message_delete(plain); assert plain.channel.sent == []
    bot_msg = Msg(Member(33, bot=True), channel=Chan(43), mentions=[victim]); await cog.on_message_delete(bot_msg); assert bot_msg.channel.sent == []
    print("guards tests OK")

asyncio.run(main())
