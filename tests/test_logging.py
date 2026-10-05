"""Run: venv/bin/python -m tests.test_logging"""
import asyncio, datetime, tempfile
from pathlib import Path
from types import SimpleNamespace as NS

from core import db, modules
from modules import server_logging as sl


class FakeUser(NS):
    def __str__(self): return "user#0"


class FakeChan:
    def __init__(self, i): self.id, self.sent = i, []; self.mention = f"<#{i}>"
    async def send(self, **kw): self.sent.append(kw["embed"])


async def main():
    db.close(); db.init(Path(tempfile.mkdtemp()) / "t.sqlite")
    msgs, mem = FakeChan(10), FakeChan(11)
    guild = NS(id=1, get_channel=lambda i: {10: msgs, 11: mem}.get(i))
    cog = sl.ServerLogging(NS())
    now = datetime.datetime.now(datetime.timezone.utc)
    u = FakeUser(id=5, mention="<@5>", bot=False, guild=guild, display_avatar=NS(url="x"), roles=[], created_at=now)
    msg = NS(guild=guild, author=u, channel=NS(id=77, mention="<#77>"), content="hello", attachments=[], jump_url="u")

    # module disabled by default -> nothing is sent
    await cog.on_message_delete(msg); assert not msgs.sent

    modules.set_config(1, "logging", {"enabled": True, "messages_channel": 10, "members_channel": 11})
    await cog.on_message_delete(msg); assert len(msgs.sent) == 1 and "удалено" in msgs.sent[0].title
    assert msgs.sent[0].footer.text == "MORIARTY"

    bot_msg = NS(**{**msg.__dict__, "author": FakeUser(**{**u.__dict__, "bot": True})})
    await cog.on_message_delete(bot_msg); assert len(msgs.sent) == 1          # bots ignored

    modules.set_config(1, "logging", {"ignore_channels": [77]})
    await cog.on_message_delete(msg); assert len(msgs.sent) == 1              # ignored channel

    modules.set_config(1, "logging", {"ignore_channels": []})
    after = NS(**{**msg.__dict__, "content": "bye"})
    await cog.on_message_edit(msg, after); assert len(msgs.sent) == 2         # edit logged
    await cog.on_message_edit(msg, msg); assert len(msgs.sent) == 2           # no text change -> skipped

    modules.set_config(1, "logging", {"log_message_edit": False})
    await cog.on_message_edit(msg, after); assert len(msgs.sent) == 2         # event toggle off

    await cog.on_member_join(u); assert len(mem.sent) == 1                    # members channel
    modules.set_config(1, "logging", {"voice_channel": None})
    await cog.on_voice_state_update(u, NS(channel=None), NS(channel=NS(mention="<#9>")))   # no channel -> silent, no crash
    print("logging tests OK")

asyncio.run(main())
