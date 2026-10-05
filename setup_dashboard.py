#!/usr/bin/env python3
"""
One-time setup for logging into the web panel with Discord.

    venv/bin/python setup_dashboard.py

Asks for the OAuth values from https://discord.com/developers/applications
(your bot's application -> OAuth2), writes them into .env (other lines are
kept, a .env.bak copy is made first) and prints the exact Redirect URL to add.
The client secret is typed hidden and is never printed back.
"""

import argparse
import getpass
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse

ENV_PATH = Path(__file__).resolve().parent / ".env"
KEYS = ("DISCORD_CLIENT_ID", "DISCORD_CLIENT_SECRET", "DASHBOARD_URL", "DASHBOARD_OWNER_IDS")


class SetupError(ValueError):
    pass


def validate(client_id: str, secret: str, url: str, owners: str) -> dict[str, str]:
    client_id, secret, url = client_id.strip(), secret.strip(), url.strip().rstrip("/")
    if not re.fullmatch(r"\d{15,22}", client_id):
        raise SetupError("Client ID — это число из 17–20 цифр (OAuth2 → Client ID).")
    if len(secret) < 20 or re.search(r"\s", secret):
        raise SetupError("Client Secret выглядит неверно: он длинный и без пробелов (OAuth2 → Reset Secret).")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path not in ("", "/") or parsed.query:
        raise SetupError("Адрес панели вида http://localhost:10000 или https://bot.example.com (без пути).")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1"):
        raise SetupError("Для не локального адреса нужен https:// — иначе вход через Discord небезопасен.")
    ids = [p for p in re.split(r"[,\s]+", owners.strip()) if p]
    if not ids or not all(re.fullmatch(r"\d{15,22}", i) for i in ids):
        raise SetupError("Ваш Discord ID — число (Режим разработчика → ПКМ по себе → «Копировать ID пользователя»). "
                         "Несколько ID — через запятую.")
    return {"DISCORD_CLIENT_ID": client_id, "DISCORD_CLIENT_SECRET": secret, "DASHBOARD_URL": url,
            "DASHBOARD_OWNER_IDS": ",".join(ids)}


def update_env(path: Path, values: dict[str, str]) -> bool:
    """Set/replace the given keys, keep every other line untouched. Returns True if a backup was made."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    backed_up = False
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
        backed_up = True
    remaining = dict(values)
    out = []
    for line in lines:
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m and m.group(1) in remaining:
            out.append(f"{m.group(1)}={remaining.pop(m.group(1))}")
        else:
            out.append(line)
    if remaining:
        if out and out[-1].strip():
            out.append("")
        out += [f"{k}={v}" for k, v in remaining.items()]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)                     # the file holds tokens: owner-only
    except OSError:
        pass
    return backed_up


def ask(prompt: str, default: str = "", secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    val = (getpass.getpass if secret else input)(f"{prompt}{suffix}: ").strip()
    return val or default


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Настройка входа в панель через Discord")
    ap.add_argument("--env", type=Path, default=ENV_PATH, help="путь к .env (по умолчанию рядом со скриптом)")
    ap.add_argument("--client-id"); ap.add_argument("--client-secret"); ap.add_argument("--url"); ap.add_argument("--owner-id")
    args = ap.parse_args(argv)

    print("Настройка входа в панель через Discord\n"
          "Откройте https://discord.com/developers/applications → приложение вашего бота → OAuth2.\n")
    client_id = args.client_id or ask("Client ID")
    secret = args.client_secret or ask("Client Secret (ввод скрыт)", secret=True)
    url = args.url or ask("Адрес панели", "http://localhost:10000")
    owner = args.owner_id or ask("Ваш Discord ID (можно несколько через запятую)")
    try:
        values = validate(client_id, secret, url, owner)
    except SetupError as exc:
        print(f"\n❌ {exc}\nНичего не записано, запустите скрипт ещё раз.")
        return 1
    backed_up = update_env(args.env, values)
    print(f"\n✅ Записано в {args.env}" + (f" (копия прежнего файла: {args.env.name}.bak)" if backed_up else ""))
    print(f"\nОсталось:\n"
          f"  1. В Developer Portal → OAuth2 → Redirects добавьте и сохраните:\n"
          f"       {values['DASHBOARD_URL']}/auth/callback\n"
          f"  2. Перезапустите бота: venv/bin/python main.py\n"
          f"  3. Откройте {values['DASHBOARD_URL']} и нажмите «Войти через Discord».")
    return 0


if __name__ == "__main__":
    sys.exit(main())
