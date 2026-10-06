"""
SQLite storage for the dashboard-era modules (guild configs, audit log, and
whatever tables later modules add). Legacy data still lives in the JSON files
managed by main.py -- this is only for new code.

stdlib sqlite3 behind one lock, WAL mode. Calls are short; async callers use
`await db.run(fn, ...)` so the event loop never blocks on disk.
"""

import asyncio
import json
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "moriarty.sqlite"

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id INTEGER NOT NULL,
    module   TEXT    NOT NULL,
    data     TEXT    NOT NULL,
    PRIMARY KEY (guild_id, module)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        INTEGER NOT NULL,
    guild_id  INTEGER NOT NULL,
    user_id   INTEGER,
    user_name TEXT,
    module    TEXT,
    action    TEXT NOT NULL,
    details   TEXT
);
CREATE INDEX IF NOT EXISTS audit_guild_ts ON audit_log (guild_id, ts DESC);
CREATE TABLE IF NOT EXISTS panel_sessions (
    token_hash TEXT PRIMARY KEY,
    created    INTEGER NOT NULL,
    data       TEXT    NOT NULL
);
"""

# Modules register extra CREATE TABLE statements here before init() runs.
_extra_schema: list[str] = []


def register_schema(sql: str) -> None:
    _extra_schema.append(sql)
    if _conn is not None:
        with _lock:
            _conn.executescript(sql)
            _conn.commit()


def init(path: Path | None = None) -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is not None:
            return _conn
        _conn = sqlite3.connect(str(path or DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.executescript(SCHEMA)
        for sql in _extra_schema:
            _conn.executescript(sql)
        _conn.commit()
        return _conn


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        conn = init()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with _lock:
        return init().execute(sql, params).fetchall()


async def run(fn, *args, **kwargs):
    """Run a blocking db function off the event loop."""
    return await asyncio.to_thread(fn, *args, **kwargs)


# ── guild config ────────────────────────────────────────────────────────────

def load_config(guild_id: int, module: str) -> dict:
    rows = query("SELECT data FROM guild_config WHERE guild_id=? AND module=?", (guild_id, module))
    if not rows:
        return {}
    try:
        return json.loads(rows[0]["data"])
    except json.JSONDecodeError:
        return {}


def save_config(guild_id: int, module: str, data: dict) -> None:
    execute(
        "INSERT INTO guild_config (guild_id, module, data) VALUES (?,?,?) "
        "ON CONFLICT(guild_id, module) DO UPDATE SET data=excluded.data",
        (guild_id, module, json.dumps(data, ensure_ascii=False)),
    )


# ── audit log ───────────────────────────────────────────────────────────────

def audit(guild_id: int, user_id: int | None, user_name: str | None,
          module: str | None, action: str, details: dict | None = None) -> None:
    execute(
        "INSERT INTO audit_log (ts, guild_id, user_id, user_name, module, action, details) "
        "VALUES (?,?,?,?,?,?,?)",
        (int(time.time()), guild_id, user_id, user_name, module, action,
         json.dumps(details, ensure_ascii=False) if details else None),
    )


def audit_page(guild_id: int, limit: int = 50, before_id: int | None = None) -> list[dict]:
    if before_id:
        rows = query(
            "SELECT * FROM audit_log WHERE guild_id=? AND id<? ORDER BY id DESC LIMIT ?",
            (guild_id, before_id, limit),
        )
    else:
        rows = query("SELECT * FROM audit_log WHERE guild_id=? ORDER BY id DESC LIMIT ?", (guild_id, limit))
    out = []
    for r in rows:
        d = dict(r)
        d["details"] = json.loads(d["details"]) if d["details"] else None
        out.append(d)
    return out


# ── dashboard sessions (survive bot restarts; only a hash of the cookie token is stored) ──

def session_save(token_hash: str, created: float, data: dict) -> None:
    execute("INSERT OR REPLACE INTO panel_sessions (token_hash, created, data) VALUES (?,?,?)",
            (token_hash, int(created), json.dumps(data, ensure_ascii=False)))


def session_load(token_hash: str) -> tuple[float, dict] | None:
    rows = query("SELECT created, data FROM panel_sessions WHERE token_hash=?", (token_hash,))
    if not rows:
        return None
    try:
        return float(rows[0]["created"]), json.loads(rows[0]["data"])
    except json.JSONDecodeError:
        return None


def session_delete(token_hash: str) -> None:
    execute("DELETE FROM panel_sessions WHERE token_hash=?", (token_hash,))


def session_purge(older_than: float) -> None:
    execute("DELETE FROM panel_sessions WHERE created<?", (int(older_than),))
