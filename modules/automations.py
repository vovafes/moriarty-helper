"""
Automations -- "if this happens, do that" rules without code.

Triggers: member joins / leaves, a message containing a keyword, a role
being added / removed, a daily time (MSK) or a fixed interval.
Actions:  send a message, DM the member, add / remove a role, delete the
triggering message, react to it. Message texts may use {user} {name}
{server} {count} {channel}.

One rule = one trigger + one action (several rules can share a trigger).
Rules are created and managed from the panel; this module also runs them.
"""

import datetime
import time

import discord
from discord.ext import commands, tasks

from core import db, modules

db.register_schema("""
CREATE TABLE IF NOT EXISTS automations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    name            TEXT    NOT NULL,
    trigger         TEXT    NOT NULL,
    trigger_value   TEXT,
    trigger_channel INTEGER,
    trigger_role    INTEGER,
    action          TEXT    NOT NULL,
    target_channel  INTEGER,
    role_id         INTEGER,
    text            TEXT,
    cooldown_s      INTEGER NOT NULL DEFAULT 10,
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_run_ts     INTEGER NOT NULL DEFAULT 0,
    runs            INTEGER NOT NULL DEFAULT 0,
    created_ts      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS automations_guild ON automations (guild_id, trigger);
""")

MSK = datetime.timezone(datetime.timedelta(hours=3))
MAX_RULES = 50

TRIGGERS = {
    "member_join": "Участник зашёл на сервер",
    "member_leave": "Участник вышел",
    "message_keyword": "Сообщение содержит слово",
    "role_added": "Участнику выдана роль",
    "role_removed": "У участника снята роль",
    "schedule_daily": "Каждый день в HH:MM (МСК)",
    "schedule_interval": "Каждые N минут",
}
ACTIONS = {
    "send_message": "Отправить сообщение в канал",
    "send_dm": "Написать участнику в ЛС",
    "add_role": "Выдать роль",
    "remove_role": "Снять роль",
    "delete_message": "Удалить сообщение",
    "react": "Поставить реакцию",
}
USER_TRIGGERS = {"member_join", "member_leave", "message_keyword", "role_added", "role_removed"}
NEEDS_USER = {"send_dm", "add_role", "remove_role"}
NEEDS_MESSAGE = {"delete_message", "react"}

MODULE = modules.register(modules.Module(
    key="automations", title="Автоматизации", icon="⚙️", category="tools",
    description="Правила «если → то» без кода: приветствия, авто-роли по событиям, ответы на слова, рассылки по расписанию.",
    fields=[{"key": "log_channel", "label": "Канал логов", "type": "channel", "kind": "text", "default": None,
             "help": "Сюда пишутся ошибки выполнения правил (нет прав, удалённая роль и т.п.)"}],
    tables=[{"id": "rules", "title": "Правила", "columns": [
        {"key": "id", "label": "ID"}, {"key": "name", "label": "Название"}, {"key": "when", "label": "Когда"},
        {"key": "then", "label": "Что делать"}, {"key": "on", "label": "Включено", "format": "bool"},
        {"key": "runs", "label": "Срабатываний"}, {"key": "last_run_ts", "label": "Последний раз", "format": "time"}]}],
))


# ── storage ─────────────────────────────────────────────────────────────────

def rules_for(guild_id: int, trigger: str | tuple | None = None) -> list[dict]:
    rows = [dict(r) for r in db.query("SELECT * FROM automations WHERE guild_id=? AND enabled=1 ORDER BY id", (guild_id,))]
    if trigger is None:
        return rows
    wanted = {trigger} if isinstance(trigger, str) else set(trigger)
    return [r for r in rows if r["trigger"] in wanted]


def all_rules(guild_id: int) -> list[dict]:
    return [dict(r) for r in db.query("SELECT * FROM automations WHERE guild_id=? ORDER BY id", (guild_id,))]


def add_rule(guild_id: int, **f) -> int:
    if len(all_rules(guild_id)) >= MAX_RULES:
        raise modules.ValidationError(f"Максимум {MAX_RULES} правил на сервер")
    return db.execute(
        "INSERT INTO automations (guild_id, name, trigger, trigger_value, trigger_channel, trigger_role, action,"
        " target_channel, role_id, text, cooldown_s, created_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (guild_id, f["name"], f["trigger"], f.get("trigger_value"), f.get("trigger_channel"), f.get("trigger_role"),
         f["action"], f.get("target_channel"), f.get("role_id"), f.get("text"), f.get("cooldown_s", 10), int(time.time()))).lastrowid


def touch(rule_id: int, now: int) -> None:
    db.execute("UPDATE automations SET last_run_ts=?, runs=runs+1 WHERE id=?", (now, rule_id))


# ── pure logic ──────────────────────────────────────────────────────────────

def validate_rule(f: dict) -> None:
    """Raise ValidationError if the trigger/action combination makes no sense."""
    t, a = f["trigger"], f["action"]
    if t not in TRIGGERS or a not in ACTIONS:
        raise modules.ValidationError("Неизвестный триггер или действие")
    if a in NEEDS_USER and t not in USER_TRIGGERS:
        raise modules.ValidationError("Это действие нужно участнику, а расписание его не даёт — выберите другое действие")
    if a in NEEDS_MESSAGE and t != "message_keyword":
        raise modules.ValidationError("Удалять сообщение и ставить реакцию можно только в ответ на сообщение со словом")
    if t == "message_keyword" and not (f.get("trigger_value") or "").strip():
        raise modules.ValidationError("Укажите слово или фразу для триггера")
    if t in ("role_added", "role_removed") and not f.get("trigger_role"):
        raise modules.ValidationError("Выберите роль для триггера")
    if t == "schedule_daily":
        try:
            datetime.datetime.strptime((f.get("trigger_value") or "").strip(), "%H:%M")
        except ValueError:
            raise modules.ValidationError("Время вида ЧЧ:ММ, например 20:00")
    if t == "schedule_interval":
        v = (f.get("trigger_value") or "").strip()
        if not v.isdigit() or not 1 <= int(v) <= 10080:
            raise modules.ValidationError("Интервал — число минут от 1 до 10080")
    if a in ("send_message", "send_dm") and not (f.get("text") or "").strip():
        raise modules.ValidationError("Укажите текст сообщения")
    if a == "send_message" and not f.get("target_channel"):
        raise modules.ValidationError("Выберите канал для сообщения")
    if a in ("add_role", "remove_role") and not f.get("role_id"):
        raise modules.ValidationError("Выберите роль для действия")
    if a == "react" and not (f.get("text") or "").strip():
        raise modules.ValidationError("Укажите эмодзи в поле «Текст»")
    if len(f.get("text") or "") > 1900:
        raise modules.ValidationError("Текст слишком длинный (до 1900 символов)")


def render(text: str, *, member=None, guild=None, channel=None) -> str:
    out = text or ""
    repl = {
        "{user}": member.mention if member else "",
        "{name}": getattr(member, "display_name", "") if member else "",
        "{server}": guild.name if guild else "",
        "{count}": str(guild.member_count) if guild else "",
        "{channel}": channel.mention if channel else "",
    }
    for k, v in repl.items():
        out = out.replace(k, v)
    return out


def keyword_hit(rule: dict, content: str, channel_id: int) -> bool:
    if rule["trigger_channel"] and rule["trigger_channel"] != channel_id:
        return False
    return (rule["trigger_value"] or "").strip().lower() in (content or "").lower()


def cooled_down(rule: dict, now: int) -> bool:
    return now - rule["last_run_ts"] >= rule["cooldown_s"]


def schedule_due(rule: dict, now_msk: datetime.datetime, now_ts: int) -> bool:
    if rule["trigger"] == "schedule_interval":
        return now_ts - rule["last_run_ts"] >= int(rule["trigger_value"]) * 60
    if rule["trigger"] == "schedule_daily":
        hh, mm = map(int, rule["trigger_value"].split(":"))
        target = now_msk.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now_msk < target:
            return False
        last = datetime.datetime.fromtimestamp(rule["last_run_ts"], MSK) if rule["last_run_ts"] else None
        return last is None or last < target          # not yet run since today's target time
    return False


def describe_rule(rule: dict, guild=None) -> tuple[str, str]:
    """Human-readable (when, then) for the panel table."""
    role = lambda rid: (f"«{guild.get_role(rid).name}»" if guild and rid and guild.get_role(rid) else "")
    t, v = rule["trigger"], rule["trigger_value"]
    when = {
        "message_keyword": f"Сообщение содержит «{v}»",
        "role_added": f"Участнику выдана роль {role(rule['trigger_role'])}".strip(),
        "role_removed": f"У участника снята роль {role(rule['trigger_role'])}".strip(),
        "schedule_daily": f"Каждый день в {v} (МСК)",
        "schedule_interval": f"Каждые {v} мин",
    }.get(t, TRIGGERS.get(t, t))
    then = ACTIONS.get(rule["action"], rule["action"])
    if rule["action"] in ("add_role", "remove_role"):
        then = f"{then} {role(rule['role_id'])}".strip()
    return when, then


# ── engine ──────────────────────────────────────────────────────────────────

class Automations(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.schedule_loop.start()

    async def cog_unload(self):
        self.schedule_loop.cancel()

    async def _log(self, guild, rule, text):
        cfg = modules.get_config(guild.id, "automations")
        ch = guild.get_channel(cfg["log_channel"]) if cfg["log_channel"] else None
        if ch:
            try:
                await ch.send(f"⚠️ Автоматизация **{rule['name']}** (#{rule['id']}): {text}")
            except (discord.Forbidden, discord.HTTPException):
                pass

    async def execute(self, guild: discord.Guild, rule: dict, *, member=None, message=None, channel=None):
        now = int(time.time())
        await db.run(touch, rule["id"], now)
        a = rule["action"]
        try:
            if a == "send_message":
                ch = guild.get_channel(rule["target_channel"])
                if ch is None:
                    return await self._log(guild, rule, "канал для сообщения не найден")
                await ch.send(render(rule["text"], member=member, guild=guild, channel=channel or ch),
                              allowed_mentions=discord.AllowedMentions(users=[member] if member else False, roles=False, everyone=False))
            elif a == "send_dm" and member is not None:
                await member.send(render(rule["text"], member=member, guild=guild, channel=channel))
            elif a in ("add_role", "remove_role") and member is not None:
                role = guild.get_role(rule["role_id"])
                if role is None:
                    return await self._log(guild, rule, "роль не найдена (удалена?)")
                real = guild.get_member(member.id)
                if real is None:
                    return
                if a == "add_role":
                    await real.add_roles(role, reason=f"Автоматизация «{rule['name']}»")
                else:
                    await real.remove_roles(role, reason=f"Автоматизация «{rule['name']}»")
            elif a == "delete_message" and message is not None:
                await message.delete()
            elif a == "react" and message is not None:
                await message.add_reaction(rule["text"].strip())
        except discord.Forbidden:
            await self._log(guild, rule, "у бота не хватает прав для этого действия")
        except discord.HTTPException as exc:
            await self._log(guild, rule, f"Discord отклонил действие: {exc.text or exc}")

    async def _run_all(self, guild, trigger, **ctx):
        if not modules.is_enabled(guild.id, "automations"):
            return
        for rule in await db.run(rules_for, guild.id, trigger):
            await self.execute(guild, rule, **ctx)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        await self._run_all(member.guild, "member_join", member=member)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        await self._run_all(member.guild, "member_leave", member=member)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        added = {r.id for r in after.roles} - {r.id for r in before.roles}
        removed = {r.id for r in before.roles} - {r.id for r in after.roles}
        if not (added or removed) or not modules.is_enabled(after.guild.id, "automations"):
            return
        for rule in await db.run(rules_for, after.guild.id, ("role_added", "role_removed")):
            hit = rule["trigger_role"] in (added if rule["trigger"] == "role_added" else removed)
            if hit:
                await self.execute(after.guild, rule, member=after)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or not message.content:
            return
        if not modules.is_enabled(message.guild.id, "automations"):
            return
        now = int(time.time())
        for rule in await db.run(rules_for, message.guild.id, "message_keyword"):
            if keyword_hit(rule, message.content, message.channel.id) and cooled_down(rule, now):
                await self.execute(message.guild, rule, member=message.author, message=message, channel=message.channel)
                rule["last_run_ts"] = now

    @tasks.loop(seconds=20)
    async def schedule_loop(self):
        now_ts = int(time.time())
        now_msk = datetime.datetime.now(MSK)
        for guild in self.bot.guilds:
            if not modules.is_enabled(guild.id, "automations"):
                continue
            for rule in await db.run(rules_for, guild.id, ("schedule_daily", "schedule_interval")):
                if schedule_due(rule, now_msk, now_ts):
                    await self.execute(guild, rule)

    @schedule_loop.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @schedule_loop.error
    async def _loop_error(self, error):
        print(f"WARNING: automations schedule_loop crashed, restarting: {error}")
        if not self.schedule_loop.is_running():
            self.schedule_loop.start()


# ── panel ───────────────────────────────────────────────────────────────────

def _table(guild):
    out = []
    for r in all_rules(guild.id):
        when, then = describe_rule(r, guild)
        out.append({"id": r["id"], "name": r["name"], "when": when, "then": then, "on": bool(r["enabled"]),
                    "runs": r["runs"], "last_run_ts": r["last_run_ts"] or None})
    return out


modules.register_table("automations", "rules", _table)


async def _act_add(ctx, p):
    fields = {"name": p["name"].strip(), "trigger": p["trigger"], "trigger_value": (p.get("value") or "").strip(),
              "trigger_channel": p.get("trigger_channel"), "trigger_role": p.get("trigger_role"), "action": p["action"],
              "target_channel": p.get("channel"), "role_id": p.get("role"), "text": (p.get("text") or "").strip(),
              "cooldown_s": int(p.get("cooldown") or 10)}
    if not fields["name"]:
        raise modules.ValidationError("Укажите название правила")
    validate_rule(fields)
    rid = await db.run(add_rule, ctx.guild.id, **fields)
    return f"Правило #{rid} создано"


def _by_id(run):
    async def handler(ctx, p):
        rows = await db.run(db.query, "SELECT * FROM automations WHERE id=? AND guild_id=?", (int(p["id"]), ctx.guild.id))
        if not rows:
            raise modules.ValidationError("Правило с таким ID не найдено (смотрите таблицу)")
        return await run(ctx, dict(rows[0]))
    return handler


async def _act_toggle(ctx, rule):
    await db.run(db.execute, "UPDATE automations SET enabled=? WHERE id=?", (0 if rule["enabled"] else 1, rule["id"]))
    return "Правило выключено" if rule["enabled"] else "Правило включено"


async def _act_remove(ctx, rule):
    await db.run(db.execute, "DELETE FROM automations WHERE id=?", (rule["id"],))
    return f"Правило «{rule['name']}» удалено"


_ID = [{"key": "id", "label": "ID правила", "type": "number", "min": 1}]
MODULE.actions.extend([
    modules.Action("add", "Создать правило", _act_add, description="Одно условие и одно действие; текст может содержать {user} {name} {server} {count} {channel}",
                   params=[
        {"key": "name", "label": "Название", "type": "text"},
        {"key": "trigger", "label": "Когда", "type": "select", "default": "member_join",
         "options": [{"value": k, "label": v} for k, v in TRIGGERS.items()]},
        {"key": "value", "label": "Слово / время ЧЧ:ММ / минуты", "type": "text", "required": False,
         "help": "Для «содержит слово», «каждый день» и «каждые N минут»"},
        {"key": "trigger_role", "label": "Роль-триггер", "type": "role", "required": False},
        {"key": "trigger_channel", "label": "Только в этом канале (для слова)", "type": "channel", "kind": "text", "required": False},
        {"key": "action", "label": "Что сделать", "type": "select", "default": "send_message",
         "options": [{"value": k, "label": v} for k, v in ACTIONS.items()]},
        {"key": "channel", "label": "Канал для сообщения", "type": "channel", "kind": "text", "required": False},
        {"key": "role", "label": "Роль для выдачи/снятия", "type": "role", "required": False},
        {"key": "text", "label": "Текст сообщения / эмодзи", "type": "longtext", "required": False},
        {"key": "cooldown", "label": "Пауза между срабатываниями, сек", "type": "number", "default": 10, "min": 0, "max": 86400,
         "required": False}]),
    modules.Action("toggle", "Вкл/выкл правило", _by_id(_act_toggle), params=_ID),
    modules.Action("remove", "Удалить правило", _by_id(_act_remove), params=_ID, danger=True),
])


async def setup(bot):
    await bot.add_cog(Automations(bot))
