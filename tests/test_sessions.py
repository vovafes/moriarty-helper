"""Panel logins must survive a bot restart: python -m tests.test_sessions"""
import pathlib
import tempfile
from types import SimpleNamespace as NS

from core import db

db.init(pathlib.Path(tempfile.mkdtemp()) / "t.sqlite")
from dashboard import server as s  # noqa: E402

tok = s._new_session({"user": {"id": 1234567890123456789, "name": "x"}, "guilds": {987654321098765432: {"name": "g"}}})
s.sessions.clear()                       # what a restart does to the in-memory cache
req = NS(cookies={s.COOKIE: tok})
got = s._session(req)
assert got and got["user"]["id"] == 1234567890123456789 and 987654321098765432 in got["guilds"], got
assert tok not in str([tuple(r) for r in db.query("SELECT * FROM panel_sessions")]), "raw token must not be stored"
s._drop_session(tok)
s.sessions.clear()
assert s._session(req) is None
print("sessions tests OK")
