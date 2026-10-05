"""Run: venv/bin/python -m tests.test_automod"""
import asyncio, tempfile
from pathlib import Path
from types import SimpleNamespace as NS

from core import db, modules
from modules import automod as am

db.close(); db.init(Path(tempfile.mkdtemp()) / "t.sqlite")

assert am.has_invite("зайди discord.gg/abc123") and am.has_invite("https://discord.com/invite/xyz")
assert not am.has_invite("discord is fun") and not am.has_invite("gg wp")

allowed = "youtube.com\nexample.org"
assert am.disallowed_link("глянь https://youtube.com/watch?v=1", allowed) is None
assert am.disallowed_link("https://music.youtube.com/x", allowed) is None           # subdomain of allowed
assert am.disallowed_link("https://evil-youtube.com/x", allowed) == "evil-youtube.com"   # not a suffix match
assert am.disallowed_link("www.scam.ru/free", allowed) == "scam.ru"
assert am.disallowed_link("https://discord.gg/abc", allowed) is None                # invite filter's job
assert am.disallowed_link("нет ссылок тут", allowed) is None

words = "плохо\nкрас*\n*ность"
assert am.banned_word("это плохо!", words) == "плохо"
assert am.banned_word("неплохо", words) is None                                    # whole-word
assert am.banned_word("Красивый", words) == "крас"                                  # wildcard suffix, case-insens.
assert am.banned_word("скромность", words) == "ность"
assert am.banned_word("ничего", words) is None and am.banned_word("x", "") is None

assert am.caps_hit("ПРИВЕТ ВСЕМ КАК ДЕЛА", 70, 12) and not am.caps_hit("Привет всем как дела", 70, 12)
assert not am.caps_hit("ПРИВЕТ", 70, 12)                                           # too short to judge
assert am.emoji_count("😀😀 <:a:123> <a:b:456>") == 4 and am.emoji_count("текст") == 0

s = am.SpamTracker()
assert [s.hit(1, 1, 3, 5, now=t) for t in (0, 1, 2)] == [False, False, True]        # 3 in 5s
assert s.hit(1, 1, 3, 5, now=2.1) is False                                          # window reset after a hit
assert [s.hit(1, 2, 3, 5, now=t) for t in (0, 10, 20)] == [False, False, False]     # slow -> fine
assert s.hit(1, 3, 2, 5, now=0) is False and s.hit(2, 3, 2, 5, now=0) is False      # per-guild

cfg = {**{f["key"]: f["default"] for f in modules.REGISTRY["automod"].fields}}
chk = lambda text, m=0, **o: am.check_message(text, m, {**cfg, **o}, am.SpamTracker(), 1, 9)   # fresh tracker per call
assert chk("всё нормально") is None
assert "Приглашение" in chk("discord.gg/aaa")
assert chk("https://x.ru") is None and "x.ru" in chk("https://x.ru", links_on=True)
assert "Массовые" in chk("hi", m=6) and chk("hi", m=5) is None
assert chk("СЛИШКОМ ГРОМКОЕ СООБЩЕНИЕ", caps_on=True) and chk("СЛИШКОМ ГРОМКОЕ СООБЩЕНИЕ") is None
assert chk("😀" * 11, emoji_on=True) and chk("😀" * 10, emoji_on=True) is None
assert chk("мат", words_on=True, banned_words="мат") and chk("мат", words_on=False, banned_words="мат") is None

# cog flow: delete + notice + case + timeout after N strikes; exemptions
class Chan:
    id = 5; mention = "<#5>"
    def __init__(self): self.sent = []
    async def send(self, content=None, **kw): self.sent.append(content or kw.get("embed"))
class Msg:
    def __init__(self, author, content, chan, guild):
        self.author, self.content, self.channel, self.guild = author, content, chan, guild
        self.raw_mentions, self.raw_role_mentions, self.mention_everyone = [], [], False
        self.deleted = False
    async def delete(self): self.deleted = True
class Author(am.discord.Member):
    pass
def mk_member(uid, admin=False, manage=False, roles=()):
    a = NS(id=uid, bot=False, mention=f"<@{uid}>", top_role=1, roles=[NS(id=r) for r in roles],
           guild_permissions=NS(administrator=admin, manage_messages=manage), timeouts=[])
    async def timeout(d, reason=None): a.timeouts.append(d)
    a.timeout = timeout
    return a

async def flow():
    chan = Chan()
    guild = NS(id=1, owner_id=0, me=NS(id=99, top_role=10), get_channel=lambda i: None)
    cog = am.AutoMod(NS())
    # AutoMod._exempt wants real Member instances; patch isinstance check target
    real = am.discord.Member
    am.discord.Member = NS
    try:
        user = mk_member(7)
        modules.set_config(1, "automod", {"enabled": True, "strikes_for_timeout": 2, "timeout_minutes": 10})
        m1 = Msg(user, "discord.gg/aaa", chan, guild); await cog._scan(m1)
        assert m1.deleted and chan.sent and "удалено" in chan.sent[-1]
        assert not user.timeouts
        m2 = Msg(user, "discord.gg/bbb", chan, guild); await cog._scan(m2)
        assert m2.deleted and len(user.timeouts) == 1                                  # 2nd strike -> timeout
        assert len(db.query("SELECT 1 FROM mod_cases WHERE type='automod'")) == 2
        assert len(db.query("SELECT 1 FROM mod_cases WHERE type='mute'")) == 1

        for exempt in (mk_member(8, admin=True), mk_member(9, manage=True)):
            m = Msg(exempt, "discord.gg/zzz", chan, guild); await cog._scan(m); assert not m.deleted
        modules.set_config(1, "automod", {"exempt_roles": [55]})
        m = Msg(mk_member(10, roles=[55]), "discord.gg/zzz", chan, guild); await cog._scan(m); assert not m.deleted
        bot_user = mk_member(11); bot_user.bot = True
        m = Msg(bot_user, "discord.gg/zzz", chan, guild); await cog._scan(m); assert not m.deleted
        modules.set_config(1, "automod", {"enabled": False})
        m = Msg(mk_member(12), "discord.gg/zzz", chan, guild); await cog._scan(m); assert not m.deleted
    finally:
        am.discord.Member = real
asyncio.run(flow())
print("automod tests OK")
