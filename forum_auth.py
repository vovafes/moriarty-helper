"""
forum_auth.py — доступ к forum.gta5rp.com, который теперь требует авторизацию.

Вход на форум идёт через SSO на gta5rp.com (редирект + капча/2FA), поэтому логин/пароль
из бота не отправить. Вместо этого берём куки уже залогиненной сессии браузера:

  1. Зайти на форум в браузере под своим аккаунтом.
  2. DevTools → Application → Cookies → forum.gta5rp.com, скопировать xf_user и xf_session
     (xf_tfa_trust тоже, если есть).
  3. В .env добавить одну строку:
         FORUM_COOKIE=xf_user=...; xf_session=...; xf_tfa_trust=...
     (или положить ту же строку в файл forum_cookie.txt — он в .gitignore).

Если куки нет или она протухла, форум отдаёт страницу входа (HTTP 403, data-template="login").
Тогда поднимается ForumAuthError — вызывающий код сообщает об этом явно, а не делает вид, что
на форуме ничего нет.
"""

from __future__ import annotations

import os
from pathlib import Path

COOKIE_FILE = Path(__file__).with_name("forum_cookie.txt")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class ForumAuthError(RuntimeError):
    """Форум требует вход: куки нет, она неверна или истекла."""


def get_cookie() -> str:
    c = (os.getenv("FORUM_COOKIE") or "").strip()
    if not c:  # скрипты вроде update_laws.py запускаются без load_dotenv
        env = Path(__file__).with_name(".env")
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("FORUM_COOKIE="):
                    c = line.split("=", 1)[1].strip().strip("\"'")
    if not c and COOKIE_FILE.exists():
        c = COOKIE_FILE.read_text(encoding="utf-8").strip()
    return c.removeprefix("Cookie:").strip()


def headers(extra: dict | None = None) -> dict:
    h = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ru,en;q=0.5",
    }
    cookie = get_cookie()
    if cookie:
        h["Cookie"] = cookie
    if extra:
        h.update(extra)
    return h


def is_login_wall(status: int | None, html: str) -> bool:
    """Страница входа вместо запрошенной (XenForo ставит data-template="login")."""
    if html and ('data-template="login"' in html[:2000] or "gta5rp.com/login-forum" in html):
        return True
    return status in (401, 403) and 'data-logged-in="false"' in (html or "")


def check(status: int | None, html: str, url: str = "") -> None:
    if is_login_wall(status, html):
        why = "FORUM_COOKIE не задан" if not get_cookie() else "FORUM_COOKIE устарел или неверен"
        raise ForumAuthError(
            f"форум требует вход ({why}); обнови куки xf_user/xf_session — см. forum_auth.py"
            + (f" [{url}]" if url else "")
        )
