"""Member profile for the panel: python -m tests.test_profile"""
import pathlib
import tempfile
from types import SimpleNamespace as NS

from core import db

db.init(pathlib.Path(tempfile.mkdtemp()) / "t.sqlite")
import panel_modules  # noqa: E402,F401  (registers legacy modules, imports legacy state)
from dashboard import profile  # noqa: E402
from legacy import state  # noqa: E402

G, U = 1234567890123456789, 987654321098765432
state.points_db[G] = {U: 1500}
state.chips_db[G] = {U: 20}
state.voice_minutes[G] = {U: 125}
state.message_counts[G] = {U: 42}
state.warns_db[G] = {U: {"warns": 2, "reason": "спам", "moderator": 1}}

avatar = NS(url="http://a/x.png")
avatar.replace = lambda **k: avatar
member = NS(id=U, display_name="Вова", name="vova", bot=False, voice=None, display_avatar=avatar,
            joined_at=None, created_at=__import__("datetime").datetime(2020, 1, 1),
            roles=[NS(id=5, name="@everyone", color=NS(value=0), is_default=lambda: True),
                   NS(id=6, name="Админ", color=NS(value=0xFF0000), is_default=lambda: False)])
guild = NS(id=G, members=[member])

p = profile.build(guild, member)
secs = {s["title"]: {i["label"]: i["value"] for i in s["items"]} for s in p["sections"]}
assert secs["Баланс"]["Алмазы"] == "1 500" and secs["Баланс"]["Фишки"] == "20", secs
assert secs["Активность"]["Минут в войсе"] == "2 ч 5 мин" and secs["Активность"]["Сообщений"] == "42", secs
assert secs["Варны"]["Варнов"] == "2/3", secs
assert p["id"] == str(U) and [r["name"] for r in p["roles"]] == ["Админ"], p
assert profile.search(guild, "vov")[0]["id"] == str(U) and profile.search(guild, "zzz") == []
print("profile tests OK")
