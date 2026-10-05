"""
Module registry. Every feature module declares itself once -- key, title,
description, settings schema -- and the dashboard builds its forms from that,
so adding a setting means adding a schema field, not touching HTML.

Field types:  bool | text | longtext | number | select | channel | role |
              channels | roles
Each field: {"key", "label", "type", "default", ["help"], ["options"], ["min"], ["max"]}
"""

from dataclasses import dataclass, field
from typing import Any

from core import db


@dataclass
class Module:
    key: str
    title: str
    description: str
    category: str                     # moderation | community | support | team | tools
    fields: list[dict] = field(default_factory=list)
    default_enabled: bool = False
    icon: str = "🧩"


REGISTRY: dict[str, Module] = {}


def register(module: Module) -> Module:
    REGISTRY[module.key] = module
    return module


def _field_defaults(m: Module) -> dict:
    return {f["key"]: f.get("default") for f in m.fields}


def get_config(guild_id: int, key: str) -> dict:
    """Stored config merged over schema defaults, plus the `enabled` flag."""
    m = REGISTRY[key]
    stored = db.load_config(guild_id, key)
    cfg = {**_field_defaults(m), **{k: v for k, v in stored.items() if k in _field_defaults(m)}}
    cfg["enabled"] = bool(stored.get("enabled", m.default_enabled))
    return cfg


def is_enabled(guild_id: int, key: str) -> bool:
    return get_config(guild_id, key)["enabled"]


class ValidationError(ValueError):
    pass


def _coerce(f: dict, value: Any) -> Any:
    t = f["type"]
    if value is None:
        return f.get("default")
    if t == "bool":
        return bool(value)
    if t in ("text", "longtext"):
        s = str(value)
        limit = 4000 if t == "longtext" else 500
        if len(s) > limit:
            raise ValidationError(f"«{f['label']}»: максимум {limit} символов")
        return s
    if t == "number":
        try:
            n = float(value)
        except (TypeError, ValueError):
            raise ValidationError(f"«{f['label']}»: нужно число")
        lo, hi = f.get("min"), f.get("max")
        if lo is not None and n < lo:
            raise ValidationError(f"«{f['label']}»: минимум {lo}")
        if hi is not None and n > hi:
            raise ValidationError(f"«{f['label']}»: максимум {hi}")
        return int(n) if n.is_integer() else n
    if t == "select":
        allowed = {o["value"] if isinstance(o, dict) else o for o in f.get("options", [])}
        if value not in allowed:
            raise ValidationError(f"«{f['label']}»: недопустимое значение")
        return value
    if t in ("channel", "role"):
        if value in ("", 0, "0"):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValidationError(f"«{f['label']}»: некорректный ID")
    if t in ("channels", "roles"):
        if not isinstance(value, list):
            raise ValidationError(f"«{f['label']}»: ожидается список")
        try:
            return sorted({int(v) for v in value})
        except (TypeError, ValueError):
            raise ValidationError(f"«{f['label']}»: некорректный ID в списке")
    raise ValidationError(f"неизвестный тип поля {t}")


def set_config(guild_id: int, key: str, patch: dict, *, user_id: int | None = None,
               user_name: str | None = None) -> dict:
    """Validate `patch` against the schema, merge, store, audit what changed."""
    m = REGISTRY[key]
    by_key = {f["key"]: f for f in m.fields}
    before = get_config(guild_id, key)
    after = dict(before)
    for k, v in patch.items():
        if k == "enabled":
            after["enabled"] = bool(v)
        elif k in by_key:
            after[k] = _coerce(by_key[k], v)
        else:
            raise ValidationError(f"неизвестная настройка «{k}»")
    changed = {k: {"from": before.get(k), "to": after[k]} for k in after if after[k] != before.get(k)}
    if not changed:
        return after
    db.save_config(guild_id, key, after)
    db.audit(guild_id, user_id, user_name, key, "config_update", changed)
    return after


def describe(key: str) -> dict:
    m = REGISTRY[key]
    return {"key": m.key, "title": m.title, "description": m.description,
            "category": m.category, "icon": m.icon, "fields": m.fields,
            "default_enabled": m.default_enabled}
