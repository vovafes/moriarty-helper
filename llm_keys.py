"""
llm_keys.py — пул ключей Ollama с автопереключением.

Ключи берутся по порядку; если ключ упёрся в лимит (429) или недействителен
(401/403/402), он уходит на "отдых" (cooldown) и ассистент берёт следующий.
Когда отдых закончился, ключ снова идёт в работу — первым по порядку.

Хранение: ollama_keys.json (в .gitignore, это секреты)
    {"keys": [{"name": "Аккаунт 1", "key": "..."}],
     "state": {"<fingerprint>": {"cooldown_until": epoch, "error": str,
                                  "requests": int, "last_used": iso}}}
Дополнительно подхватываются ключи из .env: OLLAMA_API_KEY и OLLAMA_API_KEYS
(через запятую) — их в панели нельзя изменить, они помечены «из .env».

В веб-панель ключи уходят только в виде маски (••••abcd); настоящие значения
наружу не отдаются и в журнал аудита не попадают.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

KEYS_FILE = "ollama_keys.json"
MAX_KEYS = 20

COOLDOWN_RATE_LIMIT = 3600      # 429 без Retry-After
COOLDOWN_INVALID = 24 * 3600    # 401/402/403 — ключ мёртв или кончились средства
COOLDOWN_TRANSIENT = 60         # 5xx / таймаут
COOLDOWN_MAX = 24 * 3600

MASK_PREFIX = "••••"


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def mask(key: str) -> str:
    return MASK_PREFIX + key[-4:] if len(key) >= 8 else MASK_PREFIX


def _load() -> dict:
    p = Path(KEYS_FILE)
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        data = {}
    data.setdefault("keys", [])
    data.setdefault("state", {})
    return data


def _save(data: dict) -> None:
    live = {fingerprint(k["key"]) for k in all_keys(data)}
    data["state"] = {fp: st for fp, st in data["state"].items() if fp in live}
    tmp = Path(KEYS_FILE + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(KEYS_FILE)


def _env_keys() -> list[str]:
    raw = [os.getenv("OLLAMA_API_KEY", "")] + os.getenv("OLLAMA_API_KEYS", "").split(",")
    return [k.strip() for k in raw if k.strip()]


def all_keys(data: dict | None = None) -> list[dict]:
    """Ключи из файла, затем из .env (без дублей): [{name, key, env}]."""
    data = data or _load()
    out, seen = [], set()
    for item in data["keys"]:
        k = (item.get("key") or "").strip()
        if k and k not in seen:
            seen.add(k)
            out.append({"name": item.get("name") or "", "key": k, "env": False})
    for i, k in enumerate(_env_keys(), 1):
        if k not in seen:
            seen.add(k)
            out.append({"name": f"из .env #{i}", "key": k, "env": True})
    return out


def has_keys() -> bool:
    return bool(all_keys())


def candidates() -> list[str]:
    """Ключи, которые сейчас можно пробовать, в порядке приоритета."""
    data = _load()
    now = time.time()
    return [
        k["key"] for k in all_keys(data)
        if data["state"].get(fingerprint(k["key"]), {}).get("cooldown_until", 0) <= now
    ]


def mark_ok(key: str) -> None:
    data = _load()
    st = data["state"].setdefault(fingerprint(key), {})
    st["requests"] = st.get("requests", 0) + 1
    st["last_used"] = datetime.now().isoformat(timespec="seconds")
    st.pop("cooldown_until", None)
    st.pop("error", None)
    _save(data)


def mark_failed(key: str, status: int | None, error: str, retry_after: float | None = None) -> int:
    """Отправляет ключ на отдых; возвращает длительность в секундах."""
    if status == 429:
        seconds = retry_after if retry_after and retry_after > 0 else COOLDOWN_RATE_LIMIT
    elif status in (401, 402, 403):
        seconds = COOLDOWN_INVALID
    else:
        seconds = COOLDOWN_TRANSIENT
    seconds = int(min(seconds, COOLDOWN_MAX))
    data = _load()
    st = data["state"].setdefault(fingerprint(key), {})
    st["cooldown_until"] = time.time() + seconds
    st["error"] = (f"HTTP {status}: " if status else "") + error[:150]
    st["last_used"] = datetime.now().isoformat(timespec="seconds")
    _save(data)
    return seconds


def reset_cooldowns() -> int:
    data = _load()
    n = 0
    for st in data["state"].values():
        if st.pop("cooldown_until", None):
            n += 1
        st.pop("error", None)
    _save(data)
    return n


# ── для веб-панели ──────────────────────────────────────────────────────────

def panel_items() -> list[dict]:
    """Редактируемые ключи (без .env) в виде [{name, key: маска}]."""
    return [{"name": k["name"], "key": mask(k["key"])} for k in all_keys() if not k["env"]]


def panel_merge(items: list[dict]) -> None:
    """
    Сохраняет список из панели. Элемент с маской вместо ключа означает «этот ключ не менялся»:
    настоящее значение берём из файла по совпадению маски. Новое значение — новый ключ.
    """
    data = _load()
    stored = [dict(k) for k in data["keys"] if (k.get("key") or "").strip()]
    used: set[int] = set()
    result = []
    seen = set()
    for n, item in enumerate(items, 1):
        name = (item.get("name") or "").strip() or f"Ключ {n}"
        raw = (item.get("key") or "").strip()
        if not raw:
            continue
        if raw.startswith(MASK_PREFIX):
            idx = next((i for i, k in enumerate(stored) if i not in used and mask(k["key"]) == raw), None)
            if idx is None:
                raise ValueError(f"«{name}»: ключ пропал из хранилища — введите его заново")
            used.add(idx)
            raw = stored[idx]["key"]
        if any(c.isspace() for c in raw):
            raise ValueError(f"«{name}»: в ключе не должно быть пробелов")
        if len(raw) < 16:
            raise ValueError(f"«{name}»: слишком короткий ключ")
        if raw in seen:
            continue
        seen.add(raw)
        result.append({"name": name, "key": raw})
    if len(result) > MAX_KEYS:
        raise ValueError(f"Не больше {MAX_KEYS} ключей")
    data["keys"] = result
    _save(data)


def status_rows() -> list[dict]:
    data = _load()
    now = time.time()
    rows = []
    for i, k in enumerate(all_keys(data), 1):
        st = data["state"].get(fingerprint(k["key"]), {})
        until = st.get("cooldown_until", 0)
        if until > now:
            left = int(until - now)
            status = f"⏸ отдыхает ещё {left // 3600} ч {left % 3600 // 60} мин" if left >= 3600 else f"⏸ отдыхает ещё {max(left // 60, 1)} мин"
        else:
            status = "✅ в работе"
        rows.append({
            "n": i, "name": k["name"], "key": mask(k["key"]), "status": status,
            "requests": st.get("requests", 0), "last_used": st.get("last_used") or "—",
            "error": st.get("error") or "",
        })
    return rows
