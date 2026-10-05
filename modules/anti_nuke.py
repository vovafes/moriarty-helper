"""
Anti-Nuke -- threat scoring over the audit log. Every destructive action
(deleting channels/roles, bans, kicks, prunes, new bots, handing out admin)
adds points to whoever did it inside a sliding window; crossing the threshold
trips the response (strip dangerous roles / kick / ban), raises an alert and,
optionally, rebuilds the channels and roles that were deleted meanwhile.

Never acts on: the server owner, the bot itself, whitelisted users/roles.
Needs the bot's role above the attacker's and the View Audit Log permission.
"""

import collections
import time

import discord
from discord.ext import commands

from core import embeds, modules

modules.register(modules.Module(
    key="anti_nuke", title="Anti-Nuke", icon="☢️", category="moderation",
    description="Защита от рейдов и взлома админ-аккаунта: считает опасные действия, останавливает нарушителя и восстанавливает удалённое.",
    fields=[
        {"key": "alert_channel", "label": "Канал тревог", "type": "channel", "kind": "text", "default": None},
        {"key": "alert_role", "label": "Кого пинговать при тревоге", "type": "role", "default": None},
        {"key": "action", "label": "Реакция на нарушителя", "type": "select", "default": "strip",
         "options": [{"value": "alert", "label": "Только тревога"},
                     {"value": "strip", "label": "Снять опасные роли"},
                     {"value": "kick", "label": "Кик"},
                     {"value": "ban", "label": "Бан"}]},
        {"key": "threshold", "label": "Порог угрозы (очки)", "type": "number", "default": 8, "min": 2, "max": 100,
         "help": "Удаление канала или роли = 3, бан или кик = 2, новый бот = 4, выдача админки = 6"},
        {"key": "window_seconds", "label": "Окно подсчёта, сек", "type": "number", "default": 60, "min": 10, "max": 3600},
        {"key": "restore", "label": "Восстанавливать удалённые каналы и роли", "type": "bool", "default": True,
         "help": "Каналы возвращаются с правами и категорией; у ролей сохраняются название, цвет и права (но не участники)"},
        {"key": "whitelist_roles", "label": "Доверенные роли", "type": "roles", "default": []},
        {"key": "whitelist_users", "label": "Доверенные пользователи (ID)", "type": "longtext", "default": "",
         "help": "По одному ID в строке. Владелец сервера доверен всегда"},
    ],
))

WEIGHTS = {
    discord.AuditLogAction.channel_delete: 3,
    discord.AuditLogAction.role_delete: 3,
    discord.AuditLogAction.ban: 2,
    discord.AuditLogAction.kick: 2,
    discord.AuditLogAction.member_prune: 5,
    discord.AuditLogAction.bot_add: 4,
    discord.AuditLogAction.webhook_create: 1,
    discord.AuditLogAction.channel_create: 1,
    discord.AuditLogAction.role_create: 1,
}
WEIGHT_GRANT_ADMIN = 6
LABELS = {
    discord.AuditLogAction.channel_delete: "удалён канал", discord.AuditLogAction.role_delete: "удалена роль",
    discord.AuditLogAction.ban: "бан", discord.AuditLogAction.kick: "кик",
    discord.AuditLogAction.member_prune: "массовая чистка участников", discord.AuditLogAction.bot_add: "добавлен бот",
    discord.AuditLogAction.webhook_create: "создан вебхук", discord.AuditLogAction.channel_create: "создан канал",
    discord.AuditLogAction.role_create: "создана роль", "grant_admin": "выдача прав администратора",
}
DANGEROUS = ("administrator", "manage_guild", "manage_roles", "manage_channels", "ban_members",
             "kick_members", "manage_webhooks", "mention_everyone")


class ThreatTracker:
    """Sliding-window score per (guild, actor)."""

    def __init__(self):
        self._events: dict[tuple[int, int], collections.deque] = {}

    def add(self, guild_id: int, actor_id: int, weight: int, now: float | None = None,
            window: int = 60, what=None) -> int:
        now = time.monotonic() if now is None else now
        q = self._events.setdefault((guild_id, actor_id), collections.deque())
        q.append((now, weight, what))
        while q and now - q[0][0] > window:
            q.popleft()
        return sum(w for _, w, _ in q)

    def recent(self, guild_id: int, actor_id: int) -> list:
        return [what for _, _, what in self._events.get((guild_id, actor_id), ()) if what is not None]

    def reset(self, guild_id: int, actor_id: int) -> None:
        self._events.pop((guild_id, actor_id), None)


def whitelisted_ids(text: str) -> set[int]:
    return {int(p) for p in (text or "").split() if p.isdigit()}


def is_trusted(guild: discord.Guild, actor, cfg: dict, bot_id: int) -> bool:
    if actor is None or actor.id in (guild.owner_id, bot_id):
        return True
    if actor.id in whitelisted_ids(cfg["whitelist_users"]):
        return True
    roles = {r.id for r in getattr(actor, "roles", [])}
    return bool(roles & set(cfg["whitelist_roles"] or []))


def snapshot_channel(ch) -> dict:
    return {"name": ch.name, "type": ch.type, "position": ch.position,
            "category_id": getattr(ch, "category_id", None),
            "topic": getattr(ch, "topic", None), "nsfw": getattr(ch, "nsfw", False),
            "slowmode": getattr(ch, "slowmode_delay", 0), "bitrate": getattr(ch, "bitrate", None),
            "user_limit": getattr(ch, "user_limit", None), "overwrites": dict(ch.overwrites)}


def snapshot_role(r: discord.Role) -> dict:
    return {"name": r.name, "color": r.color, "permissions": r.permissions, "hoist": r.hoist,
            "mentionable": r.mentionable}


class AntiNuke(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tracker = ThreatTracker()
        self.channels: dict[int, dict[int, dict]] = {}   # guild -> channel id -> snapshot
        self.roles: dict[int, dict[int, dict]] = {}
        self._tripped: set[tuple[int, int]] = set()

    # ── snapshots (so deleted things can be rebuilt) ────────────────────────
    def _remember_guild(self, guild: discord.Guild):
        self.channels[guild.id] = {c.id: snapshot_channel(c) for c in guild.channels}
        self.roles[guild.id] = {r.id: snapshot_role(r) for r in guild.roles if not r.is_default() and not r.managed}

    @commands.Cog.listener()
    async def on_ready(self):
        for g in self.bot.guilds:
            self._remember_guild(g)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, ch):
        self.channels.setdefault(ch.guild.id, {})[ch.id] = snapshot_channel(ch)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        self.channels.setdefault(after.guild.id, {})[after.id] = snapshot_channel(after)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role):
        if not role.managed:
            self.roles.setdefault(role.guild.id, {})[role.id] = snapshot_role(role)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after):
        if not after.managed:
            self.roles.setdefault(after.guild.id, {})[after.id] = snapshot_role(after)

    # ── scoring ─────────────────────────────────────────────────────────────
    @staticmethod
    def entry_weight(entry: discord.AuditLogEntry) -> tuple[int, object] | None:
        """(weight, label key) for an audit entry, or None if harmless."""
        if entry.action in WEIGHTS:
            return WEIGHTS[entry.action], entry.action
        if entry.action == discord.AuditLogAction.role_update:
            before, after = getattr(entry.before, "permissions", None), getattr(entry.after, "permissions", None)
            if after is not None and after.administrator and not (before is not None and before.administrator):
                return WEIGHT_GRANT_ADMIN, "grant_admin"
        if entry.action == discord.AuditLogAction.member_role_update:
            added = getattr(entry.after, "roles", None) or []
            if any(r.permissions.administrator for r in added):
                return WEIGHT_GRANT_ADMIN, "grant_admin"
        return None

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        guild = entry.guild
        cfg = modules.get_config(guild.id, "anti_nuke")
        if not cfg["enabled"]:
            return
        actor = entry.user
        member = guild.get_member(actor.id) if actor else None
        if is_trusted(guild, member or actor, cfg, self.bot.user.id):
            return
        scored = self.entry_weight(entry)
        if not scored:
            return
        weight, what = scored
        info = (what, entry.target.id if getattr(entry.target, "id", None) else None,
                getattr(entry.target, "name", None))
        total = self.tracker.add(guild.id, actor.id, weight, window=cfg["window_seconds"], what=info)
        if total >= cfg["threshold"] and (guild.id, actor.id) not in self._tripped:
            self._tripped.add((guild.id, actor.id))
            try:
                await self._respond(guild, member, actor, cfg, total)
            finally:
                self.tracker.reset(guild.id, actor.id)
                self._tripped.discard((guild.id, actor.id))

    # ── response ────────────────────────────────────────────────────────────
    async def _respond(self, guild: discord.Guild, member, actor, cfg: dict, score: int):
        events = self.tracker.recent(guild.id, actor.id)
        done = []
        action = cfg["action"]
        reason = f"Anti-Nuke: угроза {score} очков"
        if action == "strip" and member:
            stripped = await self._strip(guild, member, reason)
            done.append(f"снято опасных ролей: {stripped}" if stripped is not None else "не удалось снять роли (роль бота ниже?)")
        elif action in ("kick", "ban"):
            try:
                if action == "ban":
                    await guild.ban(actor, reason=reason)
                elif member:
                    await guild.kick(member, reason=reason)
                done.append("забанен" if action == "ban" else "кикнут")
            except (discord.Forbidden, discord.HTTPException):
                done.append("не удалось применить наказание (роль бота ниже?)")
        if cfg["restore"]:
            restored = await self._restore(guild, events)
            if restored:
                done.append("восстановлено: " + ", ".join(restored))
        await self._alert(guild, actor, cfg, score, events, done)

    async def _strip(self, guild, member, reason) -> int | None:
        """Remove every role with a dangerous permission that the bot is allowed to touch."""
        risky = [r for r in member.roles if not r.is_default() and not r.managed
                 and any(getattr(r.permissions, p) for p in DANGEROUS) and r < guild.me.top_role]
        if not risky:
            return 0
        try:
            await member.remove_roles(*risky, reason=reason)
            return len(risky)
        except (discord.Forbidden, discord.HTTPException):
            return None

    async def _restore(self, guild, events) -> list[str]:
        out = []
        for what, target_id, name in events:
            try:
                if what == discord.AuditLogAction.channel_delete and target_id in self.channels.get(guild.id, {}) \
                        and guild.get_channel(target_id) is None:
                    snap = self.channels[guild.id][target_id]
                    cat = guild.get_channel(snap["category_id"]) if snap["category_id"] else None
                    kw = {"reason": "Anti-Nuke: восстановление", "overwrites": snap["overwrites"]}
                    if snap["type"] == discord.ChannelType.voice:
                        await guild.create_voice_channel(snap["name"], category=cat, bitrate=snap["bitrate"] or 64000,
                                                         user_limit=snap["user_limit"] or 0, **kw)
                    elif snap["type"] == discord.ChannelType.category:
                        await guild.create_category(snap["name"], **kw)
                    else:
                        await guild.create_text_channel(snap["name"], category=cat, topic=snap["topic"],
                                                        nsfw=snap["nsfw"], slowmode_delay=snap["slowmode"], **kw)
                    out.append(f"#{snap['name']}")
                elif what == discord.AuditLogAction.role_delete and target_id in self.roles.get(guild.id, {}) \
                        and guild.get_role(target_id) is None:
                    snap = self.roles[guild.id][target_id]
                    await guild.create_role(name=snap["name"], colour=snap["color"], permissions=snap["permissions"],
                                            hoist=snap["hoist"], mentionable=snap["mentionable"],
                                            reason="Anti-Nuke: восстановление")
                    out.append(f"@{snap['name']}")
            except (discord.Forbidden, discord.HTTPException):
                continue
        return out

    async def _alert(self, guild, actor, cfg, score, events, done):
        ch = guild.get_channel(cfg["alert_channel"]) if cfg["alert_channel"] else None
        if ch is None:
            return
        lines = collections.Counter(LABELS.get(w, str(w)) for w, _, _ in events)
        e = embeds.make("☢️ Anti-Nuke: нарушитель остановлен", color=0xED4245)
        e.add_field(name="Кто", value=f"{actor.mention} (`{actor}`, `{actor.id}`)", inline=False)
        e.add_field(name="Очки угрозы", value=str(score))
        e.add_field(name="Действия", value="\n".join(f"{n}× {l}" for l, n in lines.items()) or "—", inline=False)
        e.add_field(name="Что сделал бот", value="\n".join(f"• {d}" for d in done) or "• только тревога", inline=False)
        role = guild.get_role(cfg["alert_role"]) if cfg["alert_role"] else None
        try:
            await ch.send(content=role.mention if role else None, embed=e,
                          allowed_mentions=discord.AllowedMentions(roles=[role] if role else []))
        except (discord.Forbidden, discord.HTTPException):
            pass


async def setup(bot):
    await bot.add_cog(AntiNuke(bot))
