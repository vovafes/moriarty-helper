"""
laws_module.py — Discord-интеграция ассистента семьи: законы Murrieta + игровая вики.

Подключается к main.py через setup_laws(bot).

Реагирует на упоминание бота: «@Moriarty какая статья за нелегальное оружие?»,
«@Moriarty как работает война семей?»
→ FTS5-поиск по laws.sqlite (таблицы laws — законы, wiki — вики gta5rp)
→ топ-N чанков отдаются в LLM → ответ в тот же канал.

LLM: Ollama (gpt-oss:120b) — основной, Groq (GROQ_API_KEY) — запасной. Ключей Ollama может быть
много (llm_keys.py, настраиваются в веб-панели): кончился один — берётся следующий; когда
отказали все, запрос уходит на Groq.

Команды:
    /токены           — расход токенов и статус лимитов (для роли «Семья Moriarty»)
    /закон_роль @роль — настроить роль с доступом к ассистенту (только админ)
    /закон_модель X   — переключить модель (только админ)
    /закон_тест ...   — служебная команда: задать вопрос напрямую (только админ)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import discord
from discord import app_commands

import events_feed
import llm_keys

DB_FILE = "laws.sqlite"
CONFIG_FILE = "laws_config.json"
USAGE_FILE = "laws_usage.json"

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "https://ollama.com").rstrip("/")
OLLAMA_MODELS = {"gpt-oss:120b", "gpt-oss:20b"}
GROQ_MODELS = {
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "qwen/qwen3-32b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
}
ALLOWED_MODELS = OLLAMA_MODELS | GROQ_MODELS

DEFAULT_MODEL = "gpt-oss:120b"
GROQ_FALLBACK_MODEL = "openai/gpt-oss-120b"
OLLAMA_FALLBACK_MODEL = "gpt-oss:120b"

TOP_K = 6
MAX_PER_KIND = 4  # не даём одному источнику (законы/вики) занять весь топ
MAX_QUESTION_LEN = 500
MAX_ANSWER_TOKENS = 700
OLLAMA_NUM_PREDICT = 1500  # gpt-oss тратит часть лимита на рассуждение
LLM_TIMEOUT = 90

SYSTEM_PROMPT = (
    "Ты — помощник Discord-сервера семьи Moriarty (RAGE Multiplayer, проект GTA5RP, "
    "сервер №20 Murrieta). Знаешь законы сервера и игровую вики.\n\n"
    "ПРАВИЛА:\n"
    "1. Отвечай ТОЛЬКО на основе приведённых ниже выдержек. Каждая помечена: "
    "[Закон] — законодательная база Murrieta, [Вики] — игровая вики проекта.\n"
    "2. Если в выдержках нет ответа — напиши «В моей базе этого нет» и НЕ выдумывай. "
    "Цифры, КД, суммы и сроки приводи только такие, как в выдержках.\n"
    "3. На юридические вопросы называй номер статьи и суть, цитата 1-3 строки. "
    "На игровые — объясняй по шагам, если это инструкция.\n"
    "4. Вики описывает механики общие для проекта; если выдержка про другой сервер "
    "или может отличаться на Murrieta — скажи об этом.\n"
    "5. Не путай Murrieta с другими серверами GTA5RP — кодексы у них разные.\n"
    "6. Отвечай на русском, кратко (3-7 предложений), без воды.\n"
    "7. Если вопрос не про законы и не про игру — кратко перенаправь: «Это вне моей базы».\n"
    "8. Про мероприятия на сервере (МП, ГМП) отвечай по блоку «АКТУАЛЬНЫЕ МЕРОПРИЯТИЯ»: "
    "прямо скажи, есть ли мероприятия (сегодня / в ближайшее время) или нет, и перечисли "
    "предстоящие с датой и временем по МСК. Прошедшие не предлагай. Если у темы статус "
    "«На рассмотрении» — предупреди, что мероприятие ещё может быть не одобрено."
)


# ──────────────────────────────────────────────────────────────
# Конфиг и использование (JSON)
# ──────────────────────────────────────────────────────────────

def _load_json(path: str, default: dict) -> dict:
    p = Path(path)
    if not p.exists():
        return dict(default)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return dict(default)


def _save_json(path: str, data: dict) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config() -> dict:
    """{ guild_id_str: { 'role_id': int, 'model': str } }"""
    return _load_json(CONFIG_FILE, {})


def save_config(cfg: dict) -> None:
    _save_json(CONFIG_FILE, cfg)


def load_usage() -> dict:
    """
    {
      'totals': { 'requests': int, 'input_tokens': int, 'output_tokens': int },
      'by_day': { 'YYYY-MM-DD': { 'requests': int, 'input': int, 'output': int } },
      'last': { 'model': str, 'at': str },  # какая модель ответила в последний раз
      'rate_limit': {  # последние заголовки от Groq (у Ollama их нет)
         'limit_requests': int, 'remaining_requests': int, 'reset_requests': str,
         'limit_tokens': int,   'remaining_tokens': int,   'reset_tokens': str,
         'updated_at': str,
      }
    }
    """
    return _load_json(USAGE_FILE, {
        "totals": {"requests": 0, "input_tokens": 0, "output_tokens": 0},
        "by_day": {},
        "rate_limit": {},
    })


def save_usage(u: dict) -> None:
    _save_json(USAGE_FILE, u)


def track_usage(input_tokens: int, output_tokens: int, headers: dict | None, model: str | None = None) -> dict:
    usage = load_usage()
    usage["totals"]["requests"] += 1
    usage["totals"]["input_tokens"] += input_tokens
    usage["totals"]["output_tokens"] += output_tokens

    day = datetime.now().strftime("%Y-%m-%d")
    d = usage["by_day"].setdefault(day, {"requests": 0, "input": 0, "output": 0})
    d["requests"] += 1
    d["input"] += input_tokens
    d["output"] += output_tokens

    if model:
        usage["last"] = {"model": model, "at": datetime.now().isoformat(timespec="seconds")}

    if headers:
        def _h(k):
            return headers.get(k) or headers.get(k.lower())
        usage["rate_limit"] = {
            "limit_requests": _h("x-ratelimit-limit-requests"),
            "remaining_requests": _h("x-ratelimit-remaining-requests"),
            "reset_requests": _h("x-ratelimit-reset-requests"),
            "limit_tokens": _h("x-ratelimit-limit-tokens"),
            "remaining_tokens": _h("x-ratelimit-remaining-tokens"),
            "reset_tokens": _h("x-ratelimit-reset-tokens"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    save_usage(usage)
    return usage


# ──────────────────────────────────────────────────────────────
# Поиск в SQLite FTS5
# ──────────────────────────────────────────────────────────────

_FTS_SAFE = re.compile(r"[^\w\sа-яА-ЯёЁ.-]+", re.UNICODE)

# Общие/служебные слова — если их OR'ить наравне со смысловыми словами вопроса,
# они забивают топ-K нерелевантными кусками (совпадают почти везде в базе).
_STOPWORDS = {
    "что", "как", "какой", "какая", "какие", "какое", "каком", "какому", "какого",
    "если", "будет", "это", "эти", "этот", "эта", "для", "кто", "где",
    "когда", "почему", "зачем", "или", "либо", "при", "про", "мне", "нам",
    "можно", "нужно", "надо", "есть", "быть", "был", "была", "были", "будут",
    "меня", "тебя", "его", "её", "их", "вас", "нас", "то", "все", "всё",
    "такое", "такой", "такая", "такие", "они", "она", "оно", "тот", "эту",
    # предлоги / союзы / частицы
    "за", "по", "на", "в", "во", "и", "с", "со", "у", "от", "до", "об", "обо",
    "не", "ли", "же", "бы", "б", "а", "но", "да", "из", "из-за", "к", "ко",
    "о", "то", "ну", "ведь", "чтобы", "также", "тоже", "уже", "ещё", "еще",
    "лишь", "только", "именно", "даже", "вот", "тут", "там", "здесь", "туда",
}


def _words(question: str) -> list[str]:
    q = _FTS_SAFE.sub(" ", question)
    return [w for w in q.split() if len(w) >= 2]


def _stem(word: str) -> str:
    """Грубо отрезаем окончание, чтобы «дальнобойщиком» находило «дальнобойщик», «войне» — «война»."""
    n = len(word)
    if not word.isalpha() or n < 5:
        return word
    return word[: n - (1 if n <= 5 else 2 if n <= 7 else 3)]


def _fts_query(words: list[str], op: str) -> str:
    """Строит FTS5 MATCH-запрос: каждое слово — префиксный матч, склеенные через op (AND/OR)."""
    parts = [f'"{_stem(w)}"*' for w in words]
    return f" {op} ".join(parts) if parts else '""'


_SOURCES = {
    # kind: (SQL с bm25-рангом; колонки: название, раздел, текст, url, rank)
    "law": """
        SELECT laws.law_name, laws.article_code, laws.body, laws.url, rank
        FROM laws_fts JOIN laws ON laws.id = laws_fts.rowid
        WHERE laws_fts MATCH ? ORDER BY rank LIMIT ?
    """,
    "wiki": """
        SELECT wiki.page_title, wiki.section, wiki.body, wiki.url, rank
        FROM wiki_fts JOIN wiki ON wiki.id = wiki_fts.rowid
        WHERE wiki_fts MATCH ? ORDER BY rank LIMIT ?
    """,
}


def _run_query(conn: sqlite3.Connection, query: str, top_k: int) -> list[dict]:
    """Ищет в обоих источниках и сливает по bm25, не давая одному занять весь топ."""
    found: list[tuple[float, dict]] = []
    for kind, sql in _SOURCES.items():
        try:
            rows = conn.execute(sql, (query, top_k)).fetchall()
        except sqlite3.OperationalError:
            continue  # таблицы нет (напр. вики ещё не скачана) или кривой MATCH
        for name, section, body, url, rank in rows:
            found.append((rank, {
                "kind": kind, "law_name": name, "article_code": section,
                "body": body, "url": url,
            }))
    found.sort(key=lambda x: x[0])  # bm25: меньше = лучше

    result: list[dict] = []
    per_kind: dict[str, int] = {}
    for _, item in found:
        if per_kind.get(item["kind"], 0) >= MAX_PER_KIND:
            continue
        per_kind[item["kind"]] = per_kind.get(item["kind"], 0) + 1
        result.append(item)
        if len(result) >= top_k:
            break
    return result


def _search_sync(question: str, top_k: int) -> list[dict]:
    if not Path(DB_FILE).exists():
        return []
    all_words = _words(question)
    if not all_words:
        return []
    significant = [w for w in all_words if w.lower() not in _STOPWORDS]

    conn = sqlite3.connect(DB_FILE)
    try:
        # 1) Сначала строгий AND по значимым словам — прицельно бьёт в нужный документ,
        #    не давая общим словам вопроса ("что", "если", "будет") размывать топ-K.
        if significant:
            results = _run_query(conn, _fts_query(significant, "AND"), top_k)
            if results:
                return results

        # 2) Не нашли пересечение всех слов сразу — пробуем OR по значимым словам.
        if significant:
            results = _run_query(conn, _fts_query(significant, "OR"), top_k)
            if results:
                return results

        # 3) Совсем короткий/служебный вопрос — последний шанс с исходными словами.
        return _run_query(conn, _fts_query(all_words, "OR"), top_k)
    finally:
        conn.close()


async def search_laws(question: str, top_k: int = TOP_K) -> list[dict]:
    return await asyncio.to_thread(_search_sync, question, top_k)


def _db_stats_sync() -> dict:
    stats = {"documents": 0, "chunks": 0, "scraped_at": None,
             "wiki_pages": 0, "wiki_chunks": 0, "wiki_scraped_at": None}
    if not Path(DB_FILE).exists():
        return stats
    conn = sqlite3.connect(DB_FILE)
    try:
        stats["chunks"] = conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
        stats["documents"] = conn.execute("SELECT COUNT(DISTINCT law_name) FROM laws").fetchone()[0]
        stats["scraped_at"] = conn.execute("SELECT MAX(scraped_at) FROM laws").fetchone()[0]
        try:
            stats["wiki_chunks"] = conn.execute("SELECT COUNT(*) FROM wiki").fetchone()[0]
            stats["wiki_pages"] = conn.execute("SELECT COUNT(DISTINCT page) FROM wiki").fetchone()[0]
            stats["wiki_scraped_at"] = conn.execute("SELECT MAX(scraped_at) FROM wiki").fetchone()[0]
        except sqlite3.OperationalError:
            pass  # вики ещё не скачана
        return stats
    finally:
        conn.close()


async def db_stats() -> dict:
    return await asyncio.to_thread(_db_stats_sync)


# ──────────────────────────────────────────────────────────────
# LLM: Ollama (основной) → Groq (запасной)
# ──────────────────────────────────────────────────────────────

_KIND_LABEL = {"law": "Закон", "wiki": "Вики"}


def _build_context(chunks: list[dict]) -> str:
    parts = []
    for i, c in enumerate(chunks, 1):
        label = _KIND_LABEL.get(c.get("kind", "law"), "Закон")
        parts.append(
            f"[{i}] [{label}] {c['law_name']} — {c['article_code']}\n"
            f"{c['body']}\n"
            f"Источник: {c['url']}"
        )
    return "\n\n".join(parts)


def _build_messages(question: str, chunks: list[dict], events_block: str | None = None) -> list[dict]:
    parts = []
    if events_block:
        parts.append(events_block)
    if chunks:
        parts.append(f"ВЫДЕРЖКИ ИЗ БАЗЫ (ЗАКОНЫ MURRIETA И ИГРОВАЯ ВИКИ):\n\n{_build_context(chunks)}")
    parts.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nВОПРОС: {question}")
    user_msg = "\n\n".join(parts)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]


class OllamaError(RuntimeError):
    """HTTP-ошибка Ollama; status и retry_after нужны, чтобы решить, на сколько отправить ключ отдыхать."""

    def __init__(self, status: int, message: str, retry_after: float | None = None):
        super().__init__(f"Ollama HTTP {status}: {message}")
        self.status = status
        self.body = message
        self.retry_after = retry_after


async def ollama_chat(key: str, payload: dict, timeout: int = LLM_TIMEOUT) -> dict:
    import aiohttp  # уже есть в зависимостях бота

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async with session.post(
            f"{OLLAMA_HOST}/api/chat",
            json=payload,
            headers={"Authorization": f"Bearer {key}"},
        ) as resp:
            if resp.status != 200:
                try:
                    retry_after = float(resp.headers.get("Retry-After", ""))
                except ValueError:
                    retry_after = None
                raise OllamaError(resp.status, (await resp.text())[:200], retry_after)
            return await resp.json()


async def _ask_ollama(messages: list[dict], model: str) -> tuple[str, dict | None, dict]:
    """Пробует ключи по порядку: ключ с лимитом/ошибкой уходит на отдых, берётся следующий."""
    if not llm_keys.has_keys():
        raise RuntimeError("Ключи Ollama не заданы (панель → Ассистент или OLLAMA_API_KEY в .env)")
    keys = llm_keys.candidates()
    if not keys:
        raise RuntimeError("Все ключи Ollama сейчас на отдыхе (лимит исчерпан)")

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": "low",  # RAG по готовым выдержкам не требует длинных рассуждений
        "options": {"temperature": 0.2, "num_predict": OLLAMA_NUM_PREDICT},
    }
    last_error: Exception | None = None
    for key in keys:
        try:
            data = await ollama_chat(key, payload)
        except OllamaError as e:
            secs = llm_keys.mark_failed(key, e.status, e.body, e.retry_after)
            print(f"[laws] ключ Ollama …{key[-4:]} → HTTP {e.status}, отдых {secs // 60} мин, пробую следующий")
            last_error = e
            continue
        except Exception as e:  # сеть, таймаут
            llm_keys.mark_failed(key, None, f"{type(e).__name__}: {e}")
            print(f"[laws] ключ Ollama …{key[-4:]} → {type(e).__name__}: {e}, пробую следующий")
            last_error = e
            continue

        answer = ((data.get("message") or {}).get("content") or "").strip()
        if not answer:
            # всё ушло на рассуждение и ответ обрезался — это не вина ключа, отдаём запасному провайдеру
            raise RuntimeError("Ollama вернул пустой ответ")
        llm_keys.mark_ok(key)
        usage = {
            "input_tokens": data.get("prompt_eval_count", 0) or 0,
            "output_tokens": data.get("eval_count", 0) or 0,
        }
        return answer, None, usage
    raise RuntimeError(f"Все ключи Ollama отказали, последняя ошибка: {last_error}")


async def _ask_groq(messages: list[dict], model: str) -> tuple[str, dict | None, dict]:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY не задан в .env")

    import aiohttp

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": MAX_ANSWER_TOKENS,
    }
    timeout = aiohttp.ClientTimeout(total=LLM_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Groq HTTP {resp.status}: {(await resp.text())[:200]}")
            headers = dict(resp.headers)
            data = await resp.json()

    answer = (data["choices"][0]["message"].get("content") or "").strip()
    u = data.get("usage") or {}
    usage = {
        "input_tokens": u.get("prompt_tokens", 0),
        "output_tokens": u.get("completion_tokens", 0),
    }
    return answer, headers, usage


async def ask_llm(question: str, chunks: list[dict], model: str,
                  events_block: str | None = None) -> tuple[str, dict | None, dict, str]:
    """
    Возвращает (answer_text, response_headers, usage_dict, использованная_модель).
    Сначала провайдер выбранной модели, при ошибке — второй провайдер (если у него есть ключ).
    """
    messages = _build_messages(question, chunks, events_block)
    if model in OLLAMA_MODELS:
        plan = [("ollama", model, _ask_ollama), ("groq", GROQ_FALLBACK_MODEL, _ask_groq)]
    else:
        plan = [("groq", model, _ask_groq), ("ollama", OLLAMA_FALLBACK_MODEL, _ask_ollama)]

    errors = []
    for provider, used_model, fn in plan:
        try:
            answer, headers, usage = await fn(messages, used_model)
            return answer, headers, usage, used_model
        except Exception as e:
            errors.append(f"{provider}: {e}")
            print(f"[laws] {provider} ({used_model}) не ответил: {e}")
    raise RuntimeError("Все провайдеры недоступны — " + " | ".join(errors))


# ──────────────────────────────────────────────────────────────
# Discord-обработчики
# ──────────────────────────────────────────────────────────────

def _has_access(member: discord.Member, role_id: int | None) -> bool:
    """Доступ есть у админа сервера + у заданной роли."""
    if member.guild_permissions.administrator:
        return True
    if role_id is None:
        return False
    return any(r.id == role_id for r in member.roles)


def _strip_mention(content: str, bot_user_id: int) -> str:
    """Убираем упоминание бота из текста сообщения."""
    patterns = [f"<@{bot_user_id}>", f"<@!{bot_user_id}>"]
    for p in patterns:
        content = content.replace(p, " ")
    return re.sub(r"\s+", " ", content).strip()


async def prepare_context(question: str) -> tuple[list[dict], str | None]:
    """(выдержки из базы, блок мероприятий или None). Мероприятия читаются с форума вживую."""
    if not events_feed.is_events_question(question):
        return await search_laws(question, top_k=TOP_K), None
    try:
        block = events_feed.build_events_block(await events_feed.fetch_events())
    except Exception as e:
        print(f"[laws] не удалось прочитать раздел мероприятий: {e}")
        block = (
            "АКТУАЛЬНЫЕ МЕРОПРИЯТИЯ: раздел форума сейчас недоступен, данных о мероприятиях нет. "
            "Честно скажи, что не смог проверить, и пришли ссылку на раздел: " + events_feed.EVENTS_URL
        )
    # на вопрос о мероприятиях законы/вики почти не нужны — оставляем немного для контекста
    return await search_laws(question, top_k=3), block


def _format_answer_embed(question: str, answer: str, chunks: list[dict], model: str,
                         events: bool = False) -> discord.Embed:
    embed = discord.Embed(
        title="📖 Помощник Moriarty",
        description=answer[:4000],
        color=0xE67E22,
        timestamp=datetime.now(),
    )
    if chunks:
        lines = []
        seen_urls = set()
        for c in chunks[:3]:
            if c["url"] in seen_urls:
                continue
            seen_urls.add(c["url"])
            icon = "🌐" if c.get("kind") == "wiki" else "⚖️"
            lines.append(f"{icon} [{c['law_name']} — {c['article_code']}]({c['url']})")
        if lines:
            embed.add_field(name="Источники", value="\n".join(lines), inline=False)
    if events:
        embed.add_field(
            name="Мероприятия",
            value=f"📅 [Раздел форума «Мероприятия на сервере»]({events_feed.EVENTS_URL})",
            inline=False,
        )
    embed.set_footer(text=f"Модель: {model} · Q: {question[:80]}")
    return embed


def setup_laws(bot) -> None:
    """Регистрирует обработчик упоминаний и slash-команды."""
    tree = bot.tree

    @bot.listen("on_message")
    async def on_message_laws(message: discord.Message):
        if message.author.bot:
            return
        if not message.guild:
            return
        if bot.user is None or not message.mentions:
            return
        if bot.user not in message.mentions:
            return

        cfg = load_config().get(str(message.guild.id), {})
        role_id = cfg.get("role_id")
        model = cfg.get("model", DEFAULT_MODEL)

        if not _has_access(message.author, role_id):
            return  # тихо игнорируем — упоминания бота не должны спамить

        question = _strip_mention(message.content, bot.user.id)
        if len(question) < 3:
            return
        if len(question) > MAX_QUESTION_LEN:
            await message.reply(f"Вопрос длиннее {MAX_QUESTION_LEN} символов — урежь, пожалуйста.")
            return

        async with message.channel.typing():
            try:
                chunks, events_block = await prepare_context(question)
            except Exception as e:
                await message.reply(f"❌ Ошибка поиска в базе: `{e}`")
                return

            if not chunks and not events_block:
                await message.reply(
                    "📖 В базе законов и вики я ничего не нашёл по этому вопросу. "
                    "Попробуй переформулировать или проверь, обновлена ли база "
                    "(`python update_laws.py`, `python update_wiki.py`)."
                )
                return

            try:
                answer, headers, usage, model = await ask_llm(question, chunks, model, events_block)
            except Exception as e:
                await message.reply(f"❌ ИИ недоступен: `{e}`")
                return

            track_usage(usage["input_tokens"], usage["output_tokens"], headers, model)

        embed = _format_answer_embed(question, answer, chunks, model, events=bool(events_block))
        await message.reply(embed=embed, mention_author=False)

    @tree.command(name="токены", description="Расход токенов и статус лимитов ассистента")
    async def tokens_cmd(interaction: discord.Interaction):
        cfg = load_config().get(str(interaction.guild_id), {})
        role_id = cfg.get("role_id")
        if not _has_access(interaction.user, role_id):
            await interaction.response.send_message("❌ Нет доступа.", ephemeral=True)
            return

        usage = load_usage()
        stats = await db_stats()
        rl = usage.get("rate_limit") or {}
        totals = usage["totals"]
        today = datetime.now().strftime("%Y-%m-%d")
        d = usage["by_day"].get(today, {"requests": 0, "input": 0, "output": 0})

        embed = discord.Embed(title="📊 Помощник: статус", color=0x3498DB)
        embed.add_field(
            name="База законов",
            value=f"Документов: **{stats['documents']}**\n"
                  f"Чанков: **{stats['chunks']}**\n"
                  f"Обновлена: {stats['scraped_at'] or '—'}",
            inline=True,
        )
        embed.add_field(
            name="Игровая вики",
            value=f"Страниц: **{stats['wiki_pages']}**\n"
                  f"Чанков: **{stats['wiki_chunks']}**\n"
                  f"Обновлена: {stats['wiki_scraped_at'] or '—'}",
            inline=True,
        )
        embed.add_field(
            name="Сегодня",
            value=f"Запросов: **{d['requests']}**\n"
                  f"Вход: **{d['input']}** ток.\n"
                  f"Выход: **{d['output']}** ток.",
            inline=True,
        )
        embed.add_field(
            name="Всего",
            value=f"Запросов: **{totals['requests']}**\n"
                  f"Вход: **{totals['input_tokens']}** ток.\n"
                  f"Выход: **{totals['output_tokens']}** ток.",
            inline=True,
        )
        last = usage.get("last")
        if last:
            embed.add_field(
                name="Последний ответ",
                value=f"Модель: `{last['model']}`\nКогда: {last['at']}",
                inline=False,
            )
        if rl:
            embed.add_field(
                name="Лимиты Groq (после последнего запроса через Groq)",
                value=(
                    f"Запросы: **{rl.get('remaining_requests', '?')}** / {rl.get('limit_requests', '?')}\n"
                    f"Токены: **{rl.get('remaining_tokens', '?')}** / {rl.get('limit_tokens', '?')}\n"
                    f"Сброс запросов через: {rl.get('reset_requests', '?')}\n"
                    f"Сброс токенов через: {rl.get('reset_tokens', '?')}\n"
                    f"Обновлено: {rl.get('updated_at', '?')}"
                ),
                inline=False,
            )
        else:
            embed.add_field(
                name="Лимиты Groq",
                value="Groq ещё не использовался (основной провайдер — Ollama, у него лимитов в ответах нет).",
                inline=False,
            )
        embed.set_footer(text=f"Модель: {cfg.get('model', DEFAULT_MODEL)}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tree.command(name="закон_роль", description="Задать роль с доступом к юр-ассистенту")
    @app_commands.describe(роль="Роль «Семья Moriarty» — её участники смогут тегать бота")
    async def laws_role_cmd(interaction: discord.Interaction, роль: discord.Role):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Только для админа.", ephemeral=True)
            return
        cfg = load_config()
        g = cfg.setdefault(str(interaction.guild_id), {})
        g["role_id"] = роль.id
        save_config(cfg)
        await interaction.response.send_message(
            f"✅ Доступ к юр-ассистенту: роль **{роль.name}**", ephemeral=True
        )

    @tree.command(name="закон_модель", description="Сменить модель для ответов (Ollama или Groq)")
    @app_commands.describe(модель="Например: gpt-oss:120b (Ollama) или openai/gpt-oss-120b (Groq)")
    async def laws_model_cmd(interaction: discord.Interaction, модель: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Только для админа.", ephemeral=True)
            return
        if модель not in ALLOWED_MODELS:
            allowed = "\n".join(f"• `{m}`" for m in sorted(ALLOWED_MODELS))
            await interaction.response.send_message(
                f"❌ Неизвестная модель. Допустимые:\n{allowed}",
                ephemeral=True,
            )
            return
        cfg = load_config()
        g = cfg.setdefault(str(interaction.guild_id), {})
        g["model"] = модель
        save_config(cfg)
        await interaction.response.send_message(f"✅ Модель: `{модель}`", ephemeral=True)

    @tree.command(name="закон_тест", description="Отладка: задать вопрос ассистенту напрямую")
    @app_commands.describe(вопрос="Юридический вопрос для теста")
    async def laws_test_cmd(interaction: discord.Interaction, вопрос: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Только для админа.", ephemeral=True)
            return
        cfg = load_config().get(str(interaction.guild_id), {})
        model = cfg.get("model", DEFAULT_MODEL)
        await interaction.response.defer(ephemeral=True)
        chunks, events_block = await prepare_context(вопрос)
        if not chunks and not events_block:
            await interaction.followup.send("📖 В базе ничего не нашёл.", ephemeral=True)
            return
        try:
            answer, headers, usage, model = await ask_llm(вопрос, chunks, model, events_block)
        except Exception as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return
        track_usage(usage["input_tokens"], usage["output_tokens"], headers, model)
        embed = _format_answer_embed(вопрос, answer, chunks, model, events=bool(events_block))
        await interaction.followup.send(embed=embed, ephemeral=True)
