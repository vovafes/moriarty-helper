"""
Web dashboard -- aiohttp app that runs inside the bot's own event loop (so it
reads channels/roles straight from the bot's cache) and replaces the old
health-check thread: GET /health still answers "OK" on $PORT.

Auth: Discord OAuth2 (identify + guilds). A user may manage a guild when the
bot is in it AND they own it / have Administrator there (Manage Server only with DASHBOARD_ALLOW_MANAGE_GUILD=1), or
their id is in DASHBOARD_OWNER_IDS. Sessions live in memory (a restart just
logs people out); the cookie carries only a random token.

Env:
  DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET   OAuth application
  DASHBOARD_URL            public base URL, e.g. https://bot.example.com  (redirect = <url>/auth/callback)
  DASHBOARD_OWNER_IDS      comma-separated user ids that can manage every guild the bot is in
  DASHBOARD_ALLOW_MANAGE_GUILD   1 = also let members with "Manage Server" in (default: Administrators only)
  PORT                     listen port (default 10000, what the old health server used)
"""

import asyncio
import inspect
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

from core import db, modules

STATIC_DIR = Path(__file__).resolve().parent / "static"
DISCORD_API = "https://discord.com/api/v10"
COOKIE = "moriarty_session"
STATE_COOKIE = "moriarty_oauth"
SESSION_TTL = 7 * 24 * 3600
PERM_ADMIN = 0x8
PERM_MANAGE_GUILD = 0x20

sessions: dict[str, dict] = {}
_oauth_states: dict[str, float] = {}

# Modules add their own API routes: register_routes(lambda app: app.router.add_get(...))
_route_hooks: list = []


def register_routes(fn) -> None:
    _route_hooks.append(fn)


def _allow_manage_guild() -> bool:
    """By default only server Administrators/owner may use the panel: with "Manage Server" alone a person could
    publish a role button that hands out an admin role. Opt in with DASHBOARD_ALLOW_MANAGE_GUILD=1."""
    return os.getenv("DASHBOARD_ALLOW_MANAGE_GUILD", "").lower() in ("1", "true", "yes")


def _owner_ids() -> set[int]:
    out = set()
    for part in os.getenv("DASHBOARD_OWNER_IDS", "").split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


def _base_url(request: web.Request) -> str:
    return os.getenv("DASHBOARD_URL", "").rstrip("/") or f"{request.scheme}://{request.host}"


def _json_error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


# ── sessions ────────────────────────────────────────────────────────────────

def _new_session(data: dict) -> str:
    token = secrets.token_urlsafe(32)
    sessions[token] = {**data, "created": time.time()}
    return token


def _session(request: web.Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    s = sessions.get(token or "")
    if not s:
        return None
    if time.time() - s["created"] > SESSION_TTL:
        sessions.pop(token, None)
        return None
    return s


def _require_session(request: web.Request) -> dict:
    s = _session(request)
    if not s:
        raise web.HTTPUnauthorized(text='{"error":"not logged in"}', content_type="application/json")
    return s


def _can_manage(session: dict, guild_id: int) -> bool:
    if session["user"]["id"] in _owner_ids():
        return True
    g = session["guilds"].get(guild_id)
    if not g:
        return False
    perms = int(g.get("permissions") or 0)
    return bool(g.get("owner")) or bool(perms & PERM_ADMIN) or (_allow_manage_guild() and bool(perms & PERM_MANAGE_GUILD))


def _live_access(guild, user_id: int) -> bool:
    """Re-check against the bot's own view of the server: the login-time permission
    snapshot can be days old, so a demoted or departed admin must not keep access."""
    if user_id in _owner_ids() or guild.owner_id == user_id:
        return True
    member = guild.get_member(user_id)
    if member is None:
        return False
    perms = member.guild_permissions
    return bool(perms.administrator or (_allow_manage_guild() and perms.manage_guild))


def _guild_for(request: web.Request):
    """Session check + permission check + bot presence. Returns (session, guild)."""
    session = _require_session(request)
    try:
        gid = int(request.match_info["gid"])
    except ValueError:
        raise web.HTTPBadRequest(text='{"error":"bad guild id"}', content_type="application/json")
    if not _can_manage(session, gid):
        raise web.HTTPForbidden(text='{"error":"no access to this server"}', content_type="application/json")
    guild = request.app["bot"].get_guild(gid)
    if guild is None:
        raise web.HTTPNotFound(text='{"error":"bot is not on this server"}', content_type="application/json")
    if not _live_access(guild, session["user"]["id"]):
        raise web.HTTPForbidden(text='{"error":"no access to this server"}', content_type="application/json")
    return session, guild


def _check_origin(request: web.Request) -> None:
    """State-changing calls must come from our own pages (SameSite=Lax already
    blocks cross-site POSTs; this also rejects odd Origins)."""
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        raise web.HTTPForbidden(text='{"error":"cross-site request"}', content_type="application/json")
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") not in (_base_url(request), f"{request.scheme}://{request.host}"):
        raise web.HTTPForbidden(text='{"error":"bad origin"}', content_type="application/json")


# ── auth routes ─────────────────────────────────────────────────────────────

async def health(request):
    return web.Response(text="OK")


async def auth_login(request):
    client_id = os.getenv("DISCORD_CLIENT_ID")
    if not client_id:
        return web.Response(status=503, text="DISCORD_CLIENT_ID не настроен")
    state = secrets.token_urlsafe(16)
    _oauth_states[state] = time.time()
    for k in [k for k, t in _oauth_states.items() if time.time() - t > 600]:
        _oauth_states.pop(k, None)
    q = urlencode({
        "client_id": client_id, "response_type": "code", "scope": "identify guilds",
        "redirect_uri": f"{_base_url(request)}/auth/callback", "state": state, "prompt": "none",
    })
    resp = web.HTTPFound(f"https://discord.com/oauth2/authorize?{q}")
    resp.set_cookie(STATE_COOKIE, state, max_age=600, httponly=True, samesite="Lax",
                    secure=_base_url(request).startswith("https"))
    raise resp


async def auth_callback(request):
    state, code = request.query.get("state"), request.query.get("code")
    # the state must be one we issued AND belong to this very browser (stops login-CSRF)
    if (not code or _oauth_states.pop(state or "", None) is None
            or not secrets.compare_digest(request.cookies.get(STATE_COOKIE, ""), state or "-")):
        return web.Response(status=400, text="Неверный state или code, попробуй войти заново.")
    for tok in [t for t, s in sessions.items() if time.time() - s["created"] > SESSION_TTL]:
        sessions.pop(tok, None)
    async with aiohttp.ClientSession() as http:
        async with http.post(f"{DISCORD_API}/oauth2/token", data={
            "client_id": os.getenv("DISCORD_CLIENT_ID"), "client_secret": os.getenv("DISCORD_CLIENT_SECRET"),
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": f"{_base_url(request)}/auth/callback",
        }) as r:
            if r.status != 200:
                return web.Response(status=502, text="Discord не принял код авторизации.")
            tok = (await r.json())["access_token"]
        headers = {"Authorization": f"Bearer {tok}"}
        async with http.get(f"{DISCORD_API}/users/@me", headers=headers) as r:
            user = await r.json()
        async with http.get(f"{DISCORD_API}/users/@me/guilds", headers=headers) as r:
            guilds = await r.json() if r.status == 200 else []
    session = {
        "user": {"id": int(user["id"]), "name": user.get("global_name") or user["username"],
                 "avatar": user.get("avatar")},
        "guilds": {int(g["id"]): g for g in guilds},
    }
    resp = web.HTTPFound("/")
    resp.del_cookie(STATE_COOKIE)
    resp.set_cookie(COOKIE, _new_session(session), max_age=SESSION_TTL, httponly=True,
                    samesite="Lax", secure=_base_url(request).startswith("https"))
    raise resp


async def auth_logout(request):
    _check_origin(request)
    sessions.pop(request.cookies.get(COOKIE) or "", None)
    resp = web.json_response({"ok": True})
    resp.del_cookie(COOKIE)
    return resp


# ── api ─────────────────────────────────────────────────────────────────────

async def api_me(request):
    s = _require_session(request)
    bot = request.app["bot"]
    guilds = []
    for gid, g in s["guilds"].items():
        if not _can_manage(s, gid):
            continue
        live = bot.get_guild(gid)
        if live is not None and not _live_access(live, s["user"]["id"]):
            continue
        guilds.append({"id": str(gid), "name": g["name"], "icon": g.get("icon"),
                       "bot_present": bot.get_guild(gid) is not None})
    # owners see every guild the bot is in, even ones they aren't a member of
    if s["user"]["id"] in _owner_ids():
        have = {int(g["id"]) for g in guilds}
        for g in bot.guilds:
            if g.id not in have:
                guilds.append({"id": str(g.id), "name": g.name,
                               "icon": g.icon.key if g.icon else None, "bot_present": True})
    guilds.sort(key=lambda g: (not g["bot_present"], g["name"].lower()))
    return web.json_response({"user": {**s["user"], "id": str(s["user"]["id"])}, "guilds": guilds})


async def api_overview(request):
    _, guild = _guild_for(request)
    mods = []
    for key in modules.REGISTRY:
        cfg = modules.get_config(guild.id, key)
        mods.append({"key": key, "enabled": cfg["enabled"]})
    return web.json_response({
        "id": str(guild.id), "name": guild.name, "members": guild.member_count,
        "icon": guild.icon.key if guild.icon else None,
        "modules_total": len(mods), "modules_enabled": sum(m["enabled"] for m in mods),
    })


def _module_payload(guild_id: int, key: str) -> dict:
    return {**modules.describe(key), "config": modules.get_config(guild_id, key)}


async def api_modules(request):
    _, guild = _guild_for(request)
    return web.json_response([_module_payload(guild.id, k) for k in modules.REGISTRY])


async def api_module_get(request):
    _, guild = _guild_for(request)
    key = request.match_info["key"]
    if key not in modules.REGISTRY:
        return _json_error(404, "unknown module")
    return web.json_response(_module_payload(guild.id, key))


async def api_module_put(request):
    _check_origin(request)
    session, guild = _guild_for(request)
    key = request.match_info["key"]
    if key not in modules.REGISTRY:
        return _json_error(404, "unknown module")
    try:
        patch = await request.json()
        if not isinstance(patch, dict):
            raise ValueError
    except ValueError:
        return _json_error(400, "expected a JSON object")
    try:
        # on the loop (not a worker thread): legacy adapters mutate the bot's own dicts,
        # and sqlite writes here are a millisecond
        modules.set_config(guild.id, key, patch,
                           user_id=session["user"]["id"], user_name=session["user"]["name"])
    except modules.ValidationError as exc:
        return _json_error(400, str(exc))
    return web.json_response(_module_payload(guild.id, key))


async def api_module_table(request):
    _, guild = _guild_for(request)
    fn = modules.TABLE_PROVIDERS.get((request.match_info["key"], request.match_info["tid"]))
    if fn is None:
        return _json_error(404, "no such table")
    try:
        rows = fn(guild)
        if inspect.isawaitable(rows):
            rows = await rows
    except Exception as exc:   # a broken provider must not look like the whole panel crashed
        return _json_error(500, f"не удалось собрать таблицу: {type(exc).__name__}: {exc}")
    return web.json_response(rows, dumps=lambda o: json.dumps(o, default=str))


async def api_module_action(request):
    _check_origin(request)
    session, guild = _guild_for(request)
    key, akey = request.match_info["key"], request.match_info["akey"]
    if key not in modules.REGISTRY:
        return _json_error(404, "unknown module")
    try:
        body = await request.json()
        params = body.get("params", {}) if isinstance(body, dict) else {}
    except ValueError:
        return _json_error(400, "expected a JSON object")
    ctx = modules.ActionContext(guild=guild, bot=request.app["bot"],
                                user_id=session["user"]["id"], user_name=session["user"]["name"])
    try:
        message = await modules.run_action(key, akey, params, ctx)
    except KeyError:
        return _json_error(404, "unknown action")
    except modules.ValidationError as exc:
        return _json_error(400, str(exc))
    except Exception as exc:   # an action failing must not look like a server crash
        return _json_error(500, f"{type(exc).__name__}: {exc}")
    return web.json_response({"ok": True, "message": message})


async def api_channels(request):
    _, guild = _guild_for(request)
    cats = {c.id: c.name for c in guild.categories}
    kinds = {"text": "text", "voice": "voice", "stage_voice": "voice", "news": "text", "forum": "forum"}
    out = []
    for ch in sorted(guild.channels, key=lambda c: (c.position, c.id)):
        if ch.type.name == "category":
            out.append({"id": str(ch.id), "name": ch.name, "kind": "category", "category": None})
            continue
        out.append({"id": str(ch.id), "name": ch.name, "kind": kinds.get(ch.type.name, ch.type.name),
                    "category": cats.get(getattr(ch, "category_id", None))})
    return web.json_response(out)


async def api_roles(request):
    _, guild = _guild_for(request)
    out = [{"id": str(r.id), "name": r.name, "color": r.color.value, "managed": r.managed}
           for r in sorted(guild.roles, key=lambda r: -r.position) if not r.is_default()]
    return web.json_response(out)


async def api_audit(request):
    _, guild = _guild_for(request)
    before = request.query.get("before")
    rows = await db.run(db.audit_page, guild.id, 50, int(before) if before and before.isdigit() else None)
    for r in rows:
        r["user_id"] = str(r["user_id"]) if r["user_id"] else None
        r["guild_id"] = str(r["guild_id"])
    return web.json_response(rows)


# ── app ─────────────────────────────────────────────────────────────────────

@web.middleware
async def _errors(request, handler):
    try:
        return await handler(request)
    except web.HTTPException as exc:
        if request.path.startswith("/api/") and exc.content_type != "application/json":
            return _json_error(exc.status, exc.reason)
        raise


_APP_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


@web.middleware
async def _security_headers(request, handler):
    resp = await handler(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Content-Security-Policy", _APP_CSP)       # transcripts set their own, stricter one
    if request.path.startswith(("/api/", "/auth/")):
        resp.headers["Cache-Control"] = "no-store"
    return resp


async def _index(request):
    return web.FileResponse(STATIC_DIR / "index.html")


def create_app(bot) -> web.Application:
    app = web.Application(middlewares=[_security_headers, _errors])
    app["bot"] = bot
    r = app.router
    r.add_get("/health", health)
    r.add_get("/auth/login", auth_login)
    r.add_get("/auth/callback", auth_callback)
    r.add_post("/auth/logout", auth_logout)
    r.add_get("/api/me", api_me)
    r.add_get("/api/guild/{gid}/overview", api_overview)
    r.add_get("/api/guild/{gid}/modules", api_modules)
    r.add_get("/api/guild/{gid}/modules/{key}", api_module_get)
    r.add_put("/api/guild/{gid}/modules/{key}", api_module_put)
    r.add_get("/api/guild/{gid}/modules/{key}/tables/{tid}", api_module_table)
    r.add_post("/api/guild/{gid}/modules/{key}/actions/{akey}", api_module_action)
    r.add_get("/api/guild/{gid}/channels", api_channels)
    r.add_get("/api/guild/{gid}/roles", api_roles)
    r.add_get("/api/guild/{gid}/audit", api_audit)
    for hook in _route_hooks:
        hook(app)
    r.add_get("/", _index)
    r.add_static("/static/", STATIC_DIR, show_index=False)
    return app


_runner: web.AppRunner | None = None


async def start(bot) -> None:
    """Start serving inside the running event loop (called from the bot's setup_hook)."""
    global _runner
    db.init()
    _runner = web.AppRunner(create_app(bot))
    await _runner.setup()
    port = int(os.getenv("PORT", 10000))
    await web.TCPSite(_runner, "0.0.0.0", port).start()
    print(f"Dashboard: listening on :{port}")


async def stop() -> None:
    if _runner:
        await _runner.cleanup()
