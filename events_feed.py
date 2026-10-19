"""
events_feed.py — актуальные мероприятия сервера из раздела форума
https://forum.gta5rp.com/forums/meropriyatiya-na-servere.1693/

В отличие от законов и вики это живые данные: темы появляются каждый день, поэтому
раздел читается в момент вопроса (с кэшем на несколько минут), а не кладётся в базу.

Дату и время мероприятия берём из названия темы («МП от FIB "..." | 18.10.2026 18:30»),
статус — из префикса темы («На рассмотрении» и т.п.).
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

FORUM_BASE = "https://forum.gta5rp.com"
EVENTS_URL = FORUM_BASE + "/forums/meropriyatiya-na-servere.1693/"
MSK = ZoneInfo("Europe/Moscow")
CACHE_TTL = 300  # секунд
FETCH_TIMEOUT = 20

# Вопросы про мероприятия: «какие мероприятия сегодня», «есть МП?», «будет ли гмп»
_EVENTS_RE = re.compile(
    r"мероприят|(?<![а-яё])г?мп(?![а-яё])|ивент|эвент|event|афиш", re.IGNORECASE
)
_DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2}))?")

_cache: dict = {"at": 0.0, "events": []}


def is_events_question(question: str) -> bool:
    return bool(_EVENTS_RE.search(question))


def _parse_threads(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    events = []
    for item in soup.select("div.structItem--thread"):
        title_div = item.select_one(".structItem-title")
        if not title_div:
            continue
        link = next(
            (a for a in title_div.select("a[href^='/threads/']")
             if "labelLink" not in (a.get("class") or [])),
            None,
        )
        if not link:
            continue
        label = title_div.select_one(".label")
        status = label.get_text(strip=True) if label else ""
        title = link.get_text(strip=True)
        if status == "Важно":
            continue  # закреплённые правила оформления мероприятий — не мероприятие

        starts_at = None
        m = _DATE_RE.search(title)
        if m:
            d, mo, y, hh, mm = m.groups()
            try:
                starts_at = datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), tzinfo=MSK)
            except ValueError:
                pass
        events.append({
            "title": title,
            "status": status,
            "starts_at": starts_at,
            "has_time": bool(m and m.group(4)),
            "url": FORUM_BASE + link["href"],
        })
    return events


async def fetch_events() -> list[dict]:
    """Список тем раздела (кэш CACHE_TTL секунд; при сбое сети отдаём прошлый кэш)."""
    if time.time() - _cache["at"] < CACHE_TTL:
        return _cache["events"]

    import aiohttp

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(EVENTS_URL) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"форум ответил HTTP {resp.status}")
                html = await resp.text()
        events = _parse_threads(html)
    except Exception:
        if _cache["events"]:
            return _cache["events"]
        raise
    _cache.update(at=time.time(), events=events)
    return events


def build_events_block(events: list[dict], now: datetime | None = None) -> str:
    """Текстовый блок для LLM: текущее время + мероприятия с пометкой прошло/сегодня/предстоит."""
    now = now or datetime.now(MSK)
    head = (
        f"АКТУАЛЬНЫЕ МЕРОПРИЯТИЯ НА СЕРВЕРЕ (раздел форума «Мероприятия на сервере»). "
        f"Сейчас {now.strftime('%d.%m.%Y %H:%M')} МСК."
    )
    if not events:
        return head + "\nВ разделе сейчас нет ни одной темы с мероприятием."

    def sort_key(e):
        return (e["starts_at"] is None, e["starts_at"] or now)

    lines = []
    for e in sorted(events, key=sort_key):
        st = e["starts_at"]
        if st is None:
            when = "дата в названии не указана"
        else:
            end_of_event = st if e["has_time"] else st.replace(hour=23, minute=59)
            if end_of_event < now:
                rel = "уже прошло"
            elif st.date() == now.date():
                rel = "СЕГОДНЯ, ещё не началось"
            else:
                rel = "предстоит"
            when = f"{st.strftime('%d.%m.%Y' + (' %H:%M' if e['has_time'] else ''))} ({rel})"
        status = f"; статус темы: «{e['status']}»" if e["status"] else ""
        lines.append(f"• {e['title']} — {when}{status} — {e['url']}")
    return head + "\n" + "\n".join(lines)
