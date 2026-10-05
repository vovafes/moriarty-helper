"""
Module registry. Every feature module declares itself once -- key, title,
description, settings schema -- and the dashboard builds its forms from that,
so adding a setting means adding a schema field, not touching HTML.

Field types:  bool | text | longtext | number | select | multiselect | user | channel | role |
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
    # Read-only tables shown under the form:
    #   {"id": "cases", "title": "Дела", "columns": [{"key": "case_no", "label": "#"}, {"key": "ts", "label": "Когда", "format": "time"}]}
    # Rows come from a provider registered with register_table(module_key, table_id, fn).
    tables: list = field(default_factory=list)
    # Buttons with small parameter forms (give points, remove an item, ...):
    #   Action("give", "Выдать баллы", params=[{field...}], handler=async fn(ctx, params) -> str)
    actions: list = field(default_factory=list)
    # Legacy storage adapter: when set, settings are read/written through these
    # instead of SQLite (they wrap the old JSON-backed dicts in legacy/state.py).
    loader: Any = None       # fn(guild_id) -> dict of field values
    saver: Any = None        # fn(guild_id, cfg: dict) -> None   (must persist, e.g. save_data())
    toggleable: bool = True  # False -> no on/off switch (legacy features are always on)


@dataclass
class Action:
    key: str
    label: str
    handler: Any                      # async fn(ctx: ActionContext, params: dict) -> str
    params: list = field(default_factory=list)
    confirm: str | None = None        # ask "are you sure" with this text first
    danger: bool = False
    description: str = ""


@dataclass
class ActionContext:
    guild: Any
    bot: Any
    user_id: int | None
    user_name: str | None


REGISTRY: dict[str, Module] = {}
TABLE_PROVIDERS: dict[tuple[str, str], Any] = {}   # (module key, table id) -> fn(guild) -> list[dict] (may be async)


def register(module: Module) -> Module:
    REGISTRY[module.key] = module
    return module


def register_table(key: str, table_id: str, fn) -> None:
    TABLE_PROVIDERS[(key, table_id)] = fn


def _field_defaults(m: Module) -> dict:
    return {f["key"]: f.get("default") for f in m.fields}


_cache: dict[tuple[int, str], dict] = {}


def get_config(guild_id: int, key: str) -> dict:
    """Stored config merged over schema defaults, plus the `enabled` flag.
    Cached in memory (event handlers call this on every event); set_config
    invalidates, and the bot and the panel share one process."""
    hit = _cache.get((guild_id, key))
    if hit is not None and REGISTRY[key].loader is None:
        return dict(hit)
    m = REGISTRY[key]
    if m.loader is not None:                # legacy adapter: always live, never cached
        stored = m.loader(guild_id) or {}
        cfg = {**_field_defaults(m), **{k: v for k, v in stored.items() if k in _field_defaults(m)}}
        cfg["enabled"] = True if not m.toggleable else bool(stored.get("enabled", m.default_enabled))
        return cfg
    stored = db.load_config(guild_id, key)
    cfg = {**_field_defaults(m), **{k: v for k, v in stored.items() if k in _field_defaults(m)}}
    cfg["enabled"] = bool(stored.get("enabled", m.default_enabled))
    _cache[(guild_id, key)] = cfg
    return dict(cfg)


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
    if t == "multiselect":
        allowed = {o["value"] if isinstance(o, dict) else o for o in f.get("options", [])}
        if not isinstance(value, list) or any(v not in allowed for v in value):
            raise ValidationError(f"«{f['label']}»: недопустимое значение")
        return [v for v in value]
    if t == "user":
        if value in ("", None):
            return None
        s = str(value).strip().strip("<@!>")
        if not s.isdigit():
            raise ValidationError(f"«{f['label']}»: нужен ID пользователя (число)")
        return int(s)
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
    if m.saver is not None:
        m.saver(guild_id, after)
    else:
        db.save_config(guild_id, key, after)
    _cache.pop((guild_id, key), None)
    db.audit(guild_id, user_id, user_name, key, "config_update", changed)
    return after


async def run_action(key: str, action_key: str, params: dict, ctx: ActionContext) -> str:
    """Validate `params` against the action's field list, run it, audit it."""
    m = REGISTRY[key]
    action = next((a for a in m.actions if a.key == action_key), None)
    if action is None:
        raise KeyError(action_key)
    clean = {}
    for f in action.params:
        raw = params.get(f["key"])
        if raw in (None, "") and f.get("required", True) and f["type"] not in ("bool",):
            raise ValidationError(f"«{f['label']}»: обязательное поле")
        clean[f["key"]] = _coerce(f, raw) if raw not in (None, "") or f["type"] == "bool" else f.get("default")
    result = await action.handler(ctx, clean)
    db.audit(ctx.guild.id, ctx.user_id, ctx.user_name, key, f"action:{action_key}", clean or None)
    return result or "Готово"


def describe(key: str) -> dict:
    m = REGISTRY[key]
    return {"key": m.key, "title": m.title, "description": m.description,
            "category": m.category, "icon": m.icon, "fields": m.fields,
            "default_enabled": m.default_enabled, "toggleable": m.toggleable,
            "tables": [{k: v for k, v in t.items()} for t in m.tables],
            "actions": [{"key": a.key, "label": a.label, "params": a.params, "confirm": a.confirm,
                         "danger": a.danger, "description": a.description} for a in m.actions]}
