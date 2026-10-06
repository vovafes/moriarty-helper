"""Member profile for the panel's «Участники» page.

Collects what the bot knows about one member into generic sections
({title, icon, items: [{label, value}]}) so the page needs no per-module code.
Sections of the dashboard-era modules (levels, moderation, ...) appear only while
that module is enabled; legacy features are always on.
"""

import time
from datetime import datetime

from core import db, modules


def _hm(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h} ч {m} мин" if h else f"{m} мин"


def _on(guild_id: int, key: str) -> bool:
    try:
        return modules.is_enabled(guild_id, key)
    except KeyError:
        return False


def _num(n) -> str:
    return f"{int(n):,}".replace(",", " ")


def _day(ts) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y") if ts else "—"


def search(guild, q: str, limit: int = 25) -> list[dict]:
    q = (q or "").strip().lower().lstrip("@")
    out = []
    for m in guild.members:
        if q and not (q in m.display_name.lower() or q in m.name.lower() or q == str(m.id)):
            continue
        out.append(m)
        if len(out) >= (limit if q else limit):
            break
    out.sort(key=lambda m: m.display_name.lower())
    return [{"id": str(m.id), "name": m.display_name, "username": m.name, "bot": m.bot,
             "avatar": m.display_avatar.replace(size=64).url} for m in out]


def build(guild, member) -> dict:
    from legacy.helpers import get_chips, get_points, get_warns, warn_payment_label
    from legacy.state import afk_list, inactive_list, message_counts, recruit_stats, voice_join_times, voice_minutes
    gid, uid = guild.id, member.id
    sections = []

    def add(title, icon, items):
        items = [i for i in items if i is not None]
        if items:
            sections.append({"title": title, "icon": icon, "items": items})

    def item(label, value, hint=None):
        return {"label": label, "value": str(value), **({"hint": hint} if hint else {})}

    # ── economy ──
    add("Баланс", "💎", [item("Алмазы", _num(get_points(gid, uid))), item("Фишки", _num(get_chips(gid, uid)))])

    # ── activity (legacy counters) ──
    mins = voice_minutes.get(gid, {}).get(uid, 0)
    joined = voice_join_times.get(gid, {}).get(uid)
    live = int((datetime.now() - joined).total_seconds() // 60) if joined else 0
    vs = member.voice
    add("Активность", "🎙", [
        item("Минут в войсе", _hm(mins + live), f"{mins + live} мин" + (" · сейчас в войсе" if joined else "")),
        item("Сообщений", _num(message_counts.get(gid, {}).get(uid, 0))),
        item("Сейчас в голосовом", f"#{vs.channel.name}" if vs and vs.channel else "нет"),
    ])

    # ── warns / afk / recruits ──
    w = get_warns(gid, uid)
    if w:
        add("Варны", "⚠️", [item("Варнов", f"{w['warns']}/3"), item("Причина", w.get("reason") or "—"),
                            item("Оплата", warn_payment_label(w).replace("💵 ", "").replace("💎 ", ""))])
    st = []
    for store, label in ((afk_list, "AFK"), (inactive_list, "Неактив")):
        d = store.get(gid, {}).get(uid)
        if d:
            st.append(item(label, d.get("reason") or "—", f"вернётся: {d.get('return_time') or d.get('return_date') or '—'}"))
    add("Статус", "💤", st)
    r = recruit_stats.get(gid, {}).get(uid)
    if r and (r.get("approved") or r.get("rejected")):
        add("Рекрутинг", "🎖", [item("Принято", r.get("approved", 0)), item("Отклонено", r.get("rejected", 0))])

    # ── dashboard-era modules ──
    if _on(gid, "levels"):
        from modules import levels
        xp, msgs, _ = levels.get_xp(gid, uid)
        lvl, into, need = levels.progress(xp)
        add("Уровень", "⭐", [item("Уровень", lvl), item("Опыт", _num(xp), f"{into}/{need} до следующего"),
                              item("Место в топе", f"#{levels.rank_of(gid, uid)}"), item("Сообщений (XP)", _num(msgs))])
    if _on(gid, "moderation"):
        rows = db.query("SELECT type, COUNT(*) n, SUM(active) a FROM mod_cases WHERE guild_id=? AND user_id=? GROUP BY type", (gid, uid))
        if rows:
            add("Модерация", "🛡", [item(r["type"], r["n"], f"активных: {r['a'] or 0}") for r in rows])
    if _on(gid, "team"):
        since = datetime.fromtimestamp(time.time() - 30 * 86400).strftime("%Y-%m-%d")
        a = db.query("SELECT COALESCE(SUM(msgs),0) m, COALESCE(SUM(voice_min),0) v FROM team_activity "
                     "WHERE guild_id=? AND user_id=? AND ymd>=?", (gid, uid, since))[0]
        rt = db.query("SELECT AVG(stars) s, COUNT(*) n FROM team_ratings WHERE guild_id=? AND target_id=?", (gid, uid))[0]
        its = []
        if a["m"] or a["v"]:
            its += [item("Сообщений за 30 дней", _num(a["m"])), item("Войс за 30 дней", _hm(a["v"]))]
        if rt["n"]:
            its.append(item("Оценка команды", f"{rt['s']:.1f} ★", f"оценок: {rt['n']}"))
        add("Команда", "👥", its)
    if _on(gid, "birthdays"):
        b = db.query("SELECT day, month, year FROM birthdays WHERE guild_id=? AND user_id=?", (gid, uid))
        if b:
            add("День рождения", "🎂", [item("Дата", f"{b[0]['day']:02d}.{b[0]['month']:02d}" + (f".{b[0]['year']}" if b[0]["year"] else ""))])

    roles = [{"id": str(r.id), "name": r.name, "color": r.color.value} for r in reversed(member.roles) if not r.is_default()]
    return {
        "id": str(uid), "name": member.display_name, "username": member.name, "bot": member.bot,
        "avatar": member.display_avatar.replace(size=128).url,
        "joined": int(member.joined_at.timestamp()) if member.joined_at else None,
        "created": int(member.created_at.timestamp()),
        "roles": roles, "sections": sections,
    }
