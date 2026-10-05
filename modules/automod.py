"""
AutoMod -- seven message filters (invites, links, banned words, spam, mass
mentions, caps, emoji). A hit deletes the message, posts a short notice,
logs it and records a case; enough hits in a short window time the author out.

Detection lives in small pure functions (easy to test); the cog only wires
them to messages and applies the action. Members with Administrator or
Manage Messages, exempt roles/channels and bots are never touched.
"""

import collections
import datetime
import re
import time

import discord
from discord.ext import commands

from core import db, embeds, modules
from modules import moderation

modules.register(modules.Module(
    key="automod", title="AutoMod", icon="🤖", category="moderation",
    description="Автоматическая модерация сообщений: инвайты, ссылки, запрещённые слова, спам, массовые упоминания, капс, эмодзи.",
    fields=[
        {"key": "log_channel", "label": "Канал логов", "type": "channel", "kind": "text", "default": None},
        {"key": "notify", "label": "Предупреждать в чате", "type": "bool", "default": True,
         "help": "Короткое сообщение, которое само исчезнет через несколько секунд"},
        {"key": "exempt_roles", "label": "Исключённые роли", "type": "roles", "default": []},
        {"key": "exempt_channels", "label": "Исключённые каналы", "type": "channels", "default": []},
        {"key": "strikes_for_timeout", "label": "Нарушений до таймаута", "type": "number", "default": 3, "min": 0, "max": 20,
         "help": "За 10 минут. 0 — не выдавать таймаут"},
        {"key": "timeout_minutes", "label": "Длительность таймаута, мин", "type": "number", "default": 10, "min": 1, "max": 40320},

        {"key": "invites_on", "label": "Фильтр: инвайты Discord", "type": "bool", "default": True},
        {"key": "links_on", "label": "Фильтр: ссылки", "type": "bool", "default": False},
        {"key": "allowed_domains", "label": "Разрешённые домены", "type": "longtext", "default": "",
         "help": "По одному в строке, например youtube.com — для фильтра ссылок"},
        {"key": "words_on", "label": "Фильтр: запрещённые слова", "type": "bool", "default": False},
        {"key": "banned_words", "label": "Запрещённые слова", "type": "longtext", "default": "",
         "help": "По одному в строке. * в начале или конце — любая приставка/окончание"},
        {"key": "spam_on", "label": "Фильтр: спам", "type": "bool", "default": True},
        {"key": "spam_messages", "label": "Спам: сообщений", "type": "number", "default": 6, "min": 2, "max": 50},
        {"key": "spam_seconds", "label": "Спам: за секунд", "type": "number", "default": 5, "min": 1, "max": 60},
        {"key": "mentions_on", "label": "Фильтр: массовые упоминания", "type": "bool", "default": True},
        {"key": "mentions_max", "label": "Упоминаний в сообщении, максимум", "type": "number", "default": 5, "min": 2, "max": 50},
        {"key": "caps_on", "label": "Фильтр: капс", "type": "bool", "default": False},
        {"key": "caps_percent", "label": "Капс: процент заглавных", "type": "number", "default": 70, "min": 30, "max": 100},
        {"key": "caps_min_length", "label": "Капс: минимум букв в сообщении", "type": "number", "default": 12, "min": 5, "max": 200},
        {"key": "emoji_on", "label": "Фильтр: эмодзи", "type": "bool", "default": False},
        {"key": "emoji_max", "label": "Эмодзи в сообщении, максимум", "type": "number", "default": 10, "min": 2, "max": 100},
    ],
))

# ── detectors (pure) ────────────────────────────────────────────────────────

INVITE_RE = re.compile(r"(?:discord\.gg|discord(?:app)?\.com/invite|dsc\.gg)/[\w-]+", re.I)
URL_RE = re.compile(r"(?:https?://|www\.)([^\s/<>\]\)]+)", re.I)
CUSTOM_EMOJI_RE = re.compile(r"<a?:\w+:\d+>")
UNICODE_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U0001F900-\U0001F9FF]")


def has_invite(content: str) -> bool:
    return bool(INVITE_RE.search(content))


def _domain_allowed(host: str, allowed: set[str]) -> bool:
    host = host.lower().split(":")[0]
    return any(host == d or host.endswith("." + d) for d in allowed)


def disallowed_link(content: str, allowed_text: str) -> str | None:
    """First link whose domain isn't allow-listed, or None. Invites are the invite filter's job."""
    allowed = {l.strip().lower().removeprefix("www.") for l in allowed_text.splitlines() if l.strip()}
    for m in URL_RE.finditer(content):
        host = m.group(1).lower().removeprefix("www.")
        if INVITE_RE.search(m.group(0)) or host.startswith("discord.gg"):
            continue
        if not _domain_allowed(host, allowed):
            return host
    return None


def banned_word(content: str, words_text: str) -> str | None:
    text = content.lower()
    for raw in words_text.splitlines():
        w = raw.strip().lower()
        if not w:
            continue
        starts, ends = w.startswith("*"), w.endswith("*")
        core = re.escape(w.strip("*"))
        if not core:
            continue
        pattern = ("" if starts else r"(?<!\w)") + core + ("" if ends else r"(?!\w)")
        if re.search(pattern, text):
            return w.strip("*")
    return None


def caps_hit(content: str, percent: int, min_letters: int) -> bool:
    letters = [c for c in content if c.isalpha()]
    if len(letters) < min_letters:
        return False
    return sum(c.isupper() for c in letters) * 100 / len(letters) >= percent


def emoji_count(content: str) -> int:
    return len(CUSTOM_EMOJI_RE.findall(content)) + len(UNICODE_EMOJI_RE.findall(content))


class SpamTracker:
    """Sliding window of message times per (guild, user)."""

    def __init__(self):
        self._hits: dict[tuple[int, int], collections.deque] = {}

    def hit(self, guild_id: int, user_id: int, limit: int, seconds: int, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        q = self._hits.setdefault((guild_id, user_id), collections.deque())
        q.append(now)
        while q and now - q[0] > seconds:
            q.popleft()
        if len(q) >= limit:
            q.clear()           # one violation per burst, not one per extra message
            return True
        return False


def check_message(content: str, mention_count: int, cfg: dict, spam: SpamTracker,
                  guild_id: int, user_id: int, now: float | None = None) -> str | None:
    """Return a human reason for the first filter the message trips, else None."""
    if cfg["invites_on"] and has_invite(content):
        return "Приглашение на другой сервер"
    if cfg["links_on"]:
        host = disallowed_link(content, cfg["allowed_domains"])
        if host:
            return f"Ссылка на {host}"
    if cfg["words_on"]:
        w = banned_word(content, cfg["banned_words"])
        if w:
            return f"Запрещённое слово «{w}»"
    if cfg["mentions_on"] and mention_count > cfg["mentions_max"]:
        return f"Массовые упоминания ({mention_count})"
    if cfg["caps_on"] and caps_hit(content, cfg["caps_percent"], cfg["caps_min_length"]):
        return "Слишком много заглавных букв"
    if cfg["emoji_on"] and emoji_count(content) > cfg["emoji_max"]:
        return "Слишком много эмодзи"
    if cfg["spam_on"] and spam.hit(guild_id, user_id, cfg["spam_messages"], cfg["spam_seconds"], now):
        return "Спам"
    return None


# ── cog ─────────────────────────────────────────────────────────────────────

class AutoMod(commands.Cog):
    STRIKE_WINDOW = 600

    def __init__(self, bot):
        self.bot = bot
        self.spam = SpamTracker()
        self._strikes: dict[tuple[int, int], collections.deque] = {}

    def _exempt(self, message: discord.Message, cfg: dict) -> bool:
        author = message.author
        if author.bot or message.guild is None or not isinstance(author, discord.Member):
            return True
        perms = author.guild_permissions
        if perms.administrator or perms.manage_messages:
            return True
        if message.channel.id in (cfg["exempt_channels"] or []):
            return True
        return any(r.id in (cfg["exempt_roles"] or []) for r in author.roles)

    async def _scan(self, message: discord.Message):
        if message.guild is None:
            return
        cfg = modules.get_config(message.guild.id, "automod")
        if not cfg["enabled"] or self._exempt(message, cfg):
            return
        mentions = len(message.raw_mentions) + len(message.raw_role_mentions) + (1 if message.mention_everyone else 0)
        reason = check_message(message.content or "", mentions, cfg, self.spam, message.guild.id, message.author.id)
        if reason:
            await self._punish(message, cfg, reason)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        await self._scan(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.content != after.content:
            await self._scan(after)

    async def _punish(self, message: discord.Message, cfg: dict, reason: str):
        guild, author = message.guild, message.author
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass
        if cfg["notify"]:
            try:
                await message.channel.send(f"{author.mention}, сообщение удалено: {reason}.", delete_after=6,
                                           allowed_mentions=discord.AllowedMentions(users=[author]))
            except (discord.Forbidden, discord.HTTPException):
                pass
        await db.run(moderation.add_case, guild.id, "automod", author, guild.me, reason)

        key = (guild.id, author.id)
        now = time.monotonic()
        q = self._strikes.setdefault(key, collections.deque())
        q.append(now)
        while q and now - q[0] > self.STRIKE_WINDOW:
            q.popleft()
        timed_out = False
        limit = cfg["strikes_for_timeout"]
        if limit and len(q) >= limit and moderation.hierarchy_problem(guild, guild.me, author) is None:
            try:
                secs = cfg["timeout_minutes"] * 60
                await author.timeout(datetime.timedelta(seconds=secs), reason=f"AutoMod: {reason}")
                await db.run(moderation.add_case, guild.id, "mute", author, guild.me, f"AutoMod: {reason}", secs)
                timed_out = True
                q.clear()
            except (discord.Forbidden, discord.HTTPException):
                pass

        ch = guild.get_channel(cfg["log_channel"]) if cfg["log_channel"] else None
        if ch:
            e = embeds.make("🤖 AutoMod", color=0xE67E22)
            e.add_field(name="Автор", value=f"{author.mention} (`{author.id}`)")
            e.add_field(name="Канал", value=message.channel.mention)
            e.add_field(name="Причина", value=reason, inline=False)
            if message.content:
                e.add_field(name="Сообщение", value=embeds.clip(message.content, 900), inline=False)
            if timed_out:
                e.add_field(name="Действие", value=f"Таймаут на {cfg['timeout_minutes']} мин", inline=False)
            try:
                await ch.send(embed=e, allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException):
                pass


async def setup(bot):
    await bot.add_cog(AutoMod(bot))
