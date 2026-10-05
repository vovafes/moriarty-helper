"""
Local demo of the panel -- NO Discord connection, NO real token, throwaway DB.
A fake server ("Demo Server") with a few channels/roles lets you click through
the UI. Login is skipped: open /dev-login.

    venv/bin/python -m dashboard.devserver            # http://localhost:8080/dev-login
"""
import asyncio
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

from aiohttp import web

from core import db
from dashboard import server

# importing the modules registers their schemas (same as loading the extensions)
from modules import server_logging, moderation, automod, anti_nuke  # noqa: F401

GID = 424242


def _fake_bot():
    ch = lambda i, n, t, cat: NS(id=i, name=n, type=NS(name=t), position=i, category_id=cat)
    guild = NS(
        id=GID, name="Demo Server", member_count=128, icon=None,
        categories=[NS(id=900, name="Основное"), NS(id=901, name="Администрация")],
        channels=[ch(1, "общий", "text", 900), ch(2, "новости", "text", 900), ch(3, "логи", "text", 901),
                  ch(4, "модерация", "text", 901), ch(5, "Голосовой 1", "voice", 900), ch(6, "Голосовой 2", "voice", 900)],
        roles=[NS(id=GID, name="@everyone", color=NS(value=0), managed=False, position=0, is_default=lambda: True),
               NS(id=7, name="Модератор", color=NS(value=0x3BA55D), managed=False, position=5, is_default=lambda: False),
               NS(id=8, name="Администратор", color=NS(value=0xED4245), managed=False, position=9, is_default=lambda: False),
               NS(id=9, name="Участник", color=NS(value=0x5865F2), managed=False, position=1, is_default=lambda: False)],
    )
    return NS(get_guild=lambda i: guild if i == GID else None, guilds=[guild])


async def main():
    db.close()
    db.init(Path(tempfile.mkdtemp()) / "demo.sqlite")
    # a little history so the audit log and case table aren't empty
    db.audit(GID, 1, "Demo Admin", "logging", "config_update", {"messages_channel": {"from": None, "to": 3}})
    me = NS(id=1, __str__=lambda s: "demo-mod#0001")
    class U(NS):
        def __str__(self): return self.name
    moderation.add_case(GID, "mute", U(id=11, name="spammer#1234"), U(id=1, name="Demo Admin"), "Спам в общем чате", 3600)
    moderation.add_case(GID, "automod", U(id=12, name="linker#4321"), U(id=99, name="Moriarty"), "Приглашение на другой сервер")
    moderation.add_case(GID, "ban", U(id=13, name="raider#0001"), U(id=1, name="Demo Admin"), "Рейд")

    token = server._new_session({"user": {"id": 1, "name": "Demo Admin", "avatar": None},
                                 "guilds": {GID: {"id": str(GID), "name": "Demo Server", "permissions": "8", "owner": True}}})

    async def dev_login(request):
        resp = web.HTTPFound("/")
        resp.set_cookie(server.COOKIE, token, httponly=True, samesite="Lax")
        raise resp
    server.register_routes(lambda app: app.router.add_get("/dev-login", dev_login))

    runner = web.AppRunner(server.create_app(_fake_bot()))
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    await web.TCPSite(runner, "127.0.0.1", port).start()
    print(f"Demo panel: http://localhost:{port}/dev-login  (Ctrl+C to stop)", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
