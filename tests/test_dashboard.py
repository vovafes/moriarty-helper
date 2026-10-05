"""Run: venv/bin/python -m tests.test_dashboard   (no Discord connection needed)"""
import asyncio, tempfile
from pathlib import Path
from types import SimpleNamespace as NS

from aiohttp.test_utils import TestClient, TestServer

from core import db, modules
from dashboard import server


def fake_guild(gid=111):
    ch = lambda i, n, t: NS(id=i, name=n, type=NS(name=t), position=i, category_id=900)
    return NS(id=gid, name="Test", member_count=5, icon=None,
              categories=[NS(id=900, name="Main")],
              channels=[ch(1, "general", "text"), ch(2, "Voice", "voice")],
              roles=[NS(id=gid, name="@everyone", color=NS(value=0), managed=False, position=0, is_default=lambda: True),
                     NS(id=7, name="Mod", color=NS(value=255), managed=False, position=5, is_default=lambda: False)])


async def main():
    db.close(); db.init(Path(tempfile.mkdtemp()) / "t.sqlite")
    modules.register(modules.Module("demo", "Demo", "demo module", "tools", [
        {"key": "chan", "label": "Channel", "type": "channel", "default": None},
        {"key": "n", "label": "N", "type": "number", "default": 3, "min": 1, "max": 10},
        {"key": "word", "label": "W", "type": "text", "default": "hi"},
    ]))
    g = fake_guild()
    bot = NS(get_guild=lambda i: g if i == 111 else None, guilds=[g])
    async with TestClient(TestServer(server.create_app(bot))) as c:
        assert (await c.get("/health")).status == 200
        assert (await c.get("/api/me")).status == 401
        assert (await c.get("/api/guild/111/modules")).status == 401

        tok = server._new_session({"user": {"id": 5, "name": "tester", "avatar": None},
                                   "guilds": {111: {"id": "111", "name": "Test", "permissions": "32", "owner": False},
                                              222: {"id": "222", "name": "Other", "permissions": "0", "owner": False}}})
        c.session.cookie_jar.update_cookies({server.COOKIE: tok})
        me = await (await c.get("/api/me")).json()
        assert [x["id"] for x in me["guilds"]] == ["111"], me      # 222: no manage perms -> hidden
        assert (await c.get("/api/guild/222/modules")).status == 403
        assert (await c.get("/api/guild/333/modules")).status == 403   # not a guild of the user

        mods = await (await c.get("/api/guild/111/modules")).json()
        assert mods[0]["config"] == {"chan": None, "n": 3, "word": "hi", "enabled": False}, mods

        r = await c.put("/api/guild/111/modules/demo", json={"enabled": True, "chan": "1", "n": 4})
        assert r.status == 200, await r.text()
        assert (await r.json())["config"] == {"chan": 1, "n": 4, "word": "hi", "enabled": True}
        assert (await c.put("/api/guild/111/modules/demo", json={"n": 99})).status == 400
        assert (await c.put("/api/guild/111/modules/demo", json={"bogus": 1})).status == 400
        assert (await c.put("/api/guild/111/modules/nope", json={})).status == 404
        assert (await c.put("/api/guild/111/modules/demo", json={"n": 5}, headers={"Origin": "https://evil.example"})).status == 403

        audit = await (await c.get("/api/guild/111/audit")).json()
        assert len(audit) == 1 and audit[0]["user_name"] == "tester" and audit[0]["details"]["n"] == {"from": 3, "to": 4}, audit
        chans = await (await c.get("/api/guild/111/channels")).json()
        assert [x["name"] for x in chans] == ["general", "Voice"] and chans[1]["kind"] == "voice"
        roles = await (await c.get("/api/guild/111/roles")).json()
        assert [x["name"] for x in roles] == ["Mod"]
        # ── legacy-style adapter module: loader/saver, no on/off switch, table, action, multiselect/user fields
        store = {"chan": 2, "files": ["a"], "owner": None}
        saved = []
        async def give(ctx, params):
            store["owner"] = params["who"]; return f"gave {params['amount']} to {params['who']}"
        modules.register(modules.Module(
            "legacydemo", "Legacy", "adapter demo", "family", [
                {"key": "chan", "label": "C", "type": "channel", "default": None},
                {"key": "files", "label": "F", "type": "multiselect", "default": [], "options": ["a", "b"]},
                {"key": "owner", "label": "O", "type": "user", "default": None}],
            loader=lambda gid: dict(store), saver=lambda gid, cfg: (store.update({k: v for k, v in cfg.items() if k != "enabled"}), saved.append(gid)),
            toggleable=False,
            tables=[{"id": "t", "title": "T", "columns": [{"key": "x", "label": "X"}]}],
            actions=[modules.Action("give", "Give", give, params=[
                {"key": "who", "label": "Who", "type": "user"}, {"key": "amount", "label": "Amount", "type": "number", "min": 1}])]))
        modules.register_table("legacydemo", "t", lambda guild: [{"x": guild.name}])
        mods = {m["key"]: m for m in await (await c.get("/api/guild/111/modules")).json()}
        ld = mods["legacydemo"]
        assert ld["toggleable"] is False and ld["config"]["enabled"] is True and ld["config"]["chan"] == 2
        assert ld["actions"][0]["key"] == "give" and ld["tables"][0]["id"] == "t"
        r = await c.put("/api/guild/111/modules/legacydemo", json={"files": ["a", "b"], "owner": "<@42>"})
        assert r.status == 200 and store["files"] == ["a", "b"] and store["owner"] == 42 and saved == [111]
        assert (await c.put("/api/guild/111/modules/legacydemo", json={"files": ["zzz"]})).status == 400
        assert (await c.put("/api/guild/111/modules/legacydemo", json={"owner": "abc"})).status == 400
        assert await (await c.get("/api/guild/111/modules/legacydemo/tables/t")).json() == [{"x": "Test"}]
        assert (await c.get("/api/guild/111/modules/legacydemo/tables/nope")).status == 404
        r = await c.post("/api/guild/111/modules/legacydemo/actions/give", json={"params": {"who": "7", "amount": 5}})
        assert r.status == 200 and (await r.json())["message"] == "gave 5 to 7", await r.text()
        assert (await c.post("/api/guild/111/modules/legacydemo/actions/give", json={"params": {"who": "7", "amount": 0}})).status == 400
        assert (await c.post("/api/guild/111/modules/legacydemo/actions/give", json={"params": {"amount": 1}})).status == 400
        assert (await c.post("/api/guild/111/modules/legacydemo/actions/nope", json={})).status == 404
        assert (await c.post("/api/guild/111/modules/legacydemo/actions/give", json={"params": {}}, headers={"Origin": "https://evil.example"})).status == 403
        audit = await (await c.get("/api/guild/111/audit")).json()
        assert any(a["action"] == "action:give" for a in audit)
        assert (await c.get("/")).status == 200
        assert (await c.post("/auth/logout")).status == 200
        assert (await c.get("/api/me")).status == 401
    print("dashboard tests OK")

asyncio.run(main())
