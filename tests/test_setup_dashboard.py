"""Run: venv/bin/python -m tests.test_setup_dashboard   (never touches the real .env)"""
import io, contextlib, tempfile
from pathlib import Path

import setup_dashboard as sd

CID, SECRET, OWNER = "123456789012345678", "s" * 32, "987654321098765432"
ok = sd.validate(CID, SECRET, "http://localhost:10000/", OWNER)
assert ok == {"DISCORD_CLIENT_ID": CID, "DISCORD_CLIENT_SECRET": SECRET, "DASHBOARD_URL": "http://localhost:10000",
              "DASHBOARD_OWNER_IDS": OWNER}
assert sd.validate(CID, SECRET, "https://bot.example.com", f"{OWNER}, 111111111111111111")["DASHBOARD_OWNER_IDS"] == f"{OWNER},111111111111111111"
for bad in (("abc", SECRET, "http://localhost:10000", OWNER), (CID, "short", "http://localhost:10000", OWNER),
            (CID, "has space " * 4, "http://localhost:10000", OWNER), (CID, SECRET, "localhost:10000", OWNER),
            (CID, SECRET, "http://localhost:10000/path", OWNER), (CID, SECRET, "http://bot.example.com", OWNER),
            (CID, SECRET, "http://localhost:10000", ""), (CID, SECRET, "http://localhost:10000", "not-an-id")):
    try: sd.validate(*bad); assert False, bad
    except sd.SetupError: pass

d = Path(tempfile.mkdtemp()); env = d / ".env"
env.write_text("DISCORD_TOKEN=tok\nMONGO_URI=m\nDASHBOARD_URL=old\n# comment\nGROQ_API_KEY=g")
assert sd.update_env(env, ok) is True
text = env.read_text().splitlines()
assert text[:2] == ["DISCORD_TOKEN=tok", "MONGO_URI=m"] and "# comment" in text and "GROQ_API_KEY=g" in text   # other lines intact
assert text.count("DASHBOARD_URL=http://localhost:10000") == 1 and not any(l == "DASHBOARD_URL=old" for l in text)  # replaced, not duplicated
assert f"DISCORD_CLIENT_SECRET={SECRET}" in text and (d / ".env.bak").read_text().startswith("DISCORD_TOKEN=tok")
assert oct(env.stat().st_mode & 0o777) == "0o600"
assert sd.update_env(env, ok) is True and env.read_text().count("DISCORD_CLIENT_ID=") == 1                       # idempotent
fresh = d / "new.env"; assert sd.update_env(fresh, ok) is False and "DISCORD_CLIENT_ID=" + CID in fresh.read_text()

# end to end through main() without prompts; the secret must never be echoed
env2 = d / "e2e.env"; env2.write_text("DISCORD_TOKEN=tok\n"); buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = sd.main(["--env", str(env2), "--client-id", CID, "--client-secret", SECRET, "--url", "http://localhost:10000", "--owner-id", OWNER])
out = buf.getvalue()
assert rc == 0 and SECRET not in out and "http://localhost:10000/auth/callback" in out and "DISCORD_CLIENT_SECRET=" in env2.read_text()
env3 = d / "bad.env"; buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = sd.main(["--env", str(env3), "--client-id", "x", "--client-secret", SECRET, "--url", "http://localhost:10000", "--owner-id", OWNER])
assert rc == 1 and not env3.exists() and "Ничего не записано" in buf.getvalue()                                  # invalid input writes nothing
print("setup_dashboard tests OK")
