"""Panel module: AI assistant (laws + wiki + events) -- model, access role and the pool of Ollama keys."""

import time

import laws_module
import llm_keys
from core import modules

reg = modules.register


def _load(gid):
    g = laws_module.load_config().get(str(gid), {})
    return {
        "role": g.get("role_id"),
        "model": g.get("model", laws_module.DEFAULT_MODEL),
        # настоящие ключи в браузер не отдаём — только маски (••••abcd)
        "keys": llm_keys.panel_items(),
    }


def _save(gid, cfg):
    try:
        llm_keys.panel_merge(cfg["keys"])
    except ValueError as exc:
        raise modules.ValidationError(str(exc))
    full = laws_module.load_config()
    g = full.setdefault(str(gid), {})
    if cfg["role"]:
        g["role_id"] = cfg["role"]
    else:
        g.pop("role_id", None)
    g["model"] = cfg["model"]
    laws_module.save_config(full)


def _keys_table(guild):
    return llm_keys.status_rows()


modules.register_table("assistant", "keys", _keys_table)


async def _test_keys(ctx, p):
    """Минимальный запрос каждым ключом: проверяет, что ключ жив и не упёрся в лимит."""
    keys = [k["key"] for k in llm_keys.all_keys()]
    if not keys:
        raise modules.ValidationError("Ключей нет — добавьте хотя бы один и сохраните")
    payload = {
        "model": laws_module.OLLAMA_FALLBACK_MODEL,
        "messages": [{"role": "user", "content": "ответь одним словом: ок"}],
        "stream": False, "think": "low", "options": {"num_predict": 20},
    }
    ok = bad = 0
    for key in keys:
        try:
            await laws_module.ollama_chat(key, payload, timeout=40)
            llm_keys.mark_ok(key)
            ok += 1
        except laws_module.OllamaError as e:
            llm_keys.mark_failed(key, e.status, e.body, e.retry_after)
            bad += 1
        except Exception as e:
            llm_keys.mark_failed(key, None, f"{type(e).__name__}: {e}")
            bad += 1
    return f"Проверено ключей: {len(keys)}. Рабочих: {ok}, с ошибкой: {bad} (подробности в таблице статуса)"


async def _reset(ctx, p):
    n = llm_keys.reset_cooldowns()
    return f"Отдых снят у ключей: {n}" if n else "Ни один ключ не был на отдыхе"


reg(modules.Module(
    key="assistant", title="ИИ-ассистент", icon="🤖", category="system", toggleable=False,
    description="Бот отвечает на упоминание по законам Murrieta, игровой вики и мероприятиям. "
                "Основной ИИ — Ollama; ключей может быть много: когда один упирается в лимит, берётся следующий. "
                "Если отказали все — запрос уходит на запасной Groq.",
    fields=[
        {"key": "role", "label": "Роль с доступом", "type": "role", "default": None,
         "help": "Кто может тегать бота с вопросом (админы сервера — всегда)"},
        {"key": "model", "label": "Модель", "type": "select", "default": laws_module.DEFAULT_MODEL,
         "options": [{"value": m, "label": f"{m} (Ollama)"} for m in sorted(laws_module.OLLAMA_MODELS)]
                    + [{"value": m, "label": f"{m} (Groq)"} for m in sorted(laws_module.GROQ_MODELS)]},
        {"key": "keys", "label": "Ключи Ollama", "type": "rules", "max": llm_keys.MAX_KEYS, "default": [],
         "secret": True, "group": "Ключи Ollama",
         "group_help": "Порядок = приоритет: сначала используется верхний ключ. Ключи общие для всех серверов бота и "
                       "после сохранения показываются только в виде маски. Чтобы заменить ключ — впишите новый "
                       "значением; ключи из .env (OLLAMA_API_KEY) тоже работают, но здесь не редактируются.",
         "item_fields": [
             {"key": "name", "label": "Название", "type": "text", "default": ""},
             {"key": "key", "label": "Ключ", "type": "text", "default": ""},
         ]},
    ],
    tables=[{"id": "keys", "title": "Статус ключей Ollama", "columns": [
        {"key": "n", "label": "#"}, {"key": "name", "label": "Название"}, {"key": "key", "label": "Ключ"},
        {"key": "status", "label": "Статус"}, {"key": "requests", "label": "Запросов"},
        {"key": "last_used", "label": "Последнее использование"}, {"key": "error", "label": "Последняя ошибка"}]}],
    actions=[
        modules.Action("test", "Проверить ключи", _test_keys,
                       description="Делает крошечный запрос каждым ключом (тратит чуть-чуть лимита)."),
        modules.Action("reset", "Снять отдых со всех ключей", _reset,
                       description="Если ключи ушли на отдых по ошибке — вернуть их в работу сразу."),
    ],
    loader=_load, saver=_save,
))
