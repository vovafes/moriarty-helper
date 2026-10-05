"""Run: venv/bin/python -m tests.test_community_d   (starboard, sticky)"""
import asyncio, tempfile
from pathlib import Path
from types import SimpleNamespace as NS

from core import db, modules
db.close(); db.init(Path(tempfile.mkdtemp()) / "t.sqlite")
from modules import starboard as sb, sticky as sk

class Msg:
    _n = 800
    def __init__(self, content="", author=None, reactions=(), channel=None, attachments=()):
        Msg._n += 1; self.id, self.content, self.author, self.reactions, self.channel = Msg._n, content, author, list(reactions), channel
        self.attachments, self.jump_url, self.deleted, self.edits = list(attachments), f"http://jump/{self.id}", False, []
    async def delete(self): self.deleted = True
    async def edit(self, **kw): self.edits.append(kw)
class Chan:
    def __init__(self, i, name, nsfw=False): self.id, self.name, self.mention, self.nsfw, self.msgs, self.sent = i, name, f"<#{i}>", nsfw, {}, []
    async def send(self, content=None, **kw):
        m = Msg(content or ""); m.kw = kw; self.msgs[m.id] = m; self.sent.append((content, kw)); return m
    async def fetch_message(self, i):
        import discord
        if i not in self.msgs: raise discord.NotFound(NS(status=404, reason="x"), "gone")
        return self.msgs[i]
class User:
    def __init__(self, uid, bot=False): self.id, self.bot, self.display_name = uid, bot, f"u{uid}"; self.display_avatar = NS(url="http://a")
class Reaction:
    def __init__(self, emoji, users): self.emoji, self._u = emoji, users
    def users(self, limit=None):
        async def gen():
            for u in self._u: yield u
        return gen()
general, board, nsfw = Chan(1, "general"), Chan(2, "board"), Chan(3, "nsfw", nsfw=True)
author = User(10)
chans = {1: general, 2: board, 3: nsfw}
guild = NS(id=1, get_channel=chans.get, get_member=lambda i: author if i == 10 else None, voice_channels=[])

def put(content="hello", stars=(), ch=general, **kw):
    m = Msg(content, author, [Reaction("⭐", list(stars))] if stars is not None else [], ch, **kw); ch.msgs[m.id] = m; return m

async def main():
    cog = sb.Starboard(NS(user=User(99), get_guild=lambda i: guild, guilds=[guild]))
    modules.set_config(1, "starboard", {"enabled": True, "channel": 2, "threshold": 3})
    # counting
    m = put(stars=[User(1), User(2), User(3), User(10), User(4, bot=True), User(1)])
    assert await sb.count_stars(m, "⭐", False) == 3 and await sb.count_stars(m, "⭐", True) == 4          # author/bot/dupes excluded
    assert await sb.count_stars(m, "🔥", False) == 0 and sb.should_post(3, 3) and not sb.should_post(2, 3)

    # below threshold -> nothing; reaching it -> one post; more stars -> same post edited
    m = put(stars=[User(1), User(2)])
    await cog.refresh(guild, general, m.id); assert board.sent == [] and sb.get(1, m.id) is None
    m.reactions = [Reaction("⭐", [User(1), User(2), User(3)])]
    await cog.refresh(guild, general, m.id)
    assert len(board.sent) == 1 and board.sent[0][0] == "⭐ **3** · <#1>" and sb.get(1, m.id)["stars"] == 3
    assert board.sent[0][1]["embed"].fields[0].value.endswith(f"{m.jump_url})")
    m.reactions = [Reaction("⭐", [User(i) for i in range(1, 7)])]
    await cog.refresh(guild, general, m.id)
    star_msg = next(iter(board.msgs.values()))
    assert len(board.sent) == 1 and star_msg.edits[-1]["content"] == "⭐ **6** · <#1>" and sb.get(1, m.id)["stars"] == 6
    # falls below threshold -> copy removed
    m.reactions = [Reaction("⭐", [User(1)])]
    await cog.refresh(guild, general, m.id); assert star_msg.deleted and sb.get(1, m.id) is None
    # copy deleted by hand -> posted again instead of crashing
    m.reactions = [Reaction("⭐", [User(i) for i in range(1, 5)])]
    await cog.refresh(guild, general, m.id); first = sb.get(1, m.id)["star_message_id"]
    del board.msgs[first]
    await cog.refresh(guild, general, m.id); assert sb.get(1, m.id)["star_message_id"] != first and len(board.sent) == 3

    # exclusions
    n = len(board.sent)
    m2 = put(stars=[User(i) for i in range(1, 5)], ch=general)
    modules.set_config(1, "starboard", {"ignored_channels": [1]}); await cog.refresh(guild, general, m2.id); assert len(board.sent) == n
    modules.set_config(1, "starboard", {"ignored_channels": []})
    m3 = put(stars=[User(i) for i in range(1, 5)], ch=nsfw); await cog.refresh(guild, nsfw, m3.id); assert len(board.sent) == n     # nsfw -> sfw board
    m4 = put(stars=[User(i) for i in range(1, 5)], ch=board); await cog.refresh(guild, board, m4.id); assert len(board.sent) == n   # never star the board itself
    modules.set_config(1, "starboard", {"enabled": False}); await cog.refresh(guild, general, m2.id); assert len(board.sent) == n
    modules.set_config(1, "starboard", {"enabled": True, "channel": None}); await cog.refresh(guild, general, m2.id); assert len(board.sent) == n
    # raw reaction events: only the configured emoji, never the bot's own
    modules.set_config(1, "starboard", {"channel": 2})
    calls = []
    async def fake_refresh(g, ch, mid): calls.append(mid)
    cog.refresh = fake_refresh
    mk = lambda emoji, uid, gid=1: NS(guild_id=gid, user_id=uid, emoji=emoji, channel_id=1, message_id=5)
    await cog.on_raw_reaction_add(mk("⭐", 1)); await cog.on_raw_reaction_remove(mk("⭐", 1))
    await cog.on_raw_reaction_add(mk("🔥", 1)); await cog.on_raw_reaction_add(mk("⭐", 99)); await cog.on_raw_reaction_add(mk("⭐", 1, gid=None))
    assert calls == [5, 5]
    rows = modules.TABLE_PROVIDERS[("starboard", "top")](guild)
    assert rows and rows[0]["link"].startswith("https://discord.com/channels/1/1/")

    # ── sticky
    schan = Chan(5, "rules"); chans[5] = schan
    sbot = NS(user=User(99), guilds=[guild])
    cogs = sk.Sticky(sbot)
    ctx = modules.ActionContext(guild=guild, bot=sbot, user_id=1, user_name="a")
    modules.set_config(1, "sticky", {"enabled": True, "delay": 1})
    assert "закреплено" in await modules.run_action("sticky", "set", {"channel": 5, "text": "Читайте правила"}, ctx)
    first = sk.get_for(1, 5)["last_msg_id"]; assert schan.sent[0][1]["embed"].description == "Читайте правила"
    # a burst of messages -> a single repost after silence, old copy deleted
    def say(ch, uid=10): return NS(guild=guild, channel=ch, author=User(uid))
    for _ in range(5): await cogs.on_message(say(schan))
    assert len(schan.sent) == 1                                                            # nothing yet (debounced)
    await asyncio.sleep(1.4)
    assert len(schan.sent) == 2 and schan.msgs[first].deleted and sk.get_for(1, 5)["last_msg_id"] != first
    await cogs.on_message(NS(guild=guild, channel=schan, author=User(99))); await asyncio.sleep(1.3); assert len(schan.sent) == 2   # own messages ignored
    await cogs.on_message(say(general)); await asyncio.sleep(1.3); assert general.sent == [] or all(s[1].get("embed") is None for s in general.sent)  # other channels untouched
    modules.set_config(1, "sticky", {"enabled": False}); await cogs.on_message(say(schan)); await asyncio.sleep(1.3); assert len(schan.sent) == 2
    assert modules.TABLE_PROVIDERS[("sticky", "list")](guild)[0]["channel"] == "#rules"
    assert "убрано" in await modules.run_action("sticky", "remove", {"channel": 5}, ctx) and sk.get_for(1, 5) is None
    try: await modules.run_action("sticky", "remove", {"channel": 5}, ctx); assert False
    except modules.ValidationError: pass
    print("community batch D tests OK")

asyncio.run(main())
