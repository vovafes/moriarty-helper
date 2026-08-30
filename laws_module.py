"""
laws_module.py — Discord-интеграция юридического ассистента Murrieta.

Подключается к main.py через setup_laws(bot).

Реагирует на упоминание бота: «@Moriarty какая статья за нелегальное оружие?»
→ FTS5-поиск по laws.sqlite → топ-N чанков отдаются в Groq → ответ в тот же канал.

Команды:
    /токены           — статус rate-limit Groq (для роли «Семья Moriarty»)
    /закон_роль @роль — настроить роль с доступом к ассистенту (только админ)
    /закон_модель X   — переключить модель Groq (только админ)
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

DB_FILE = "laws.sqlite"
CONFIG_FILE = "laws_config.json"
USAGE_FILE = "laws_usage.json"

DEFAULT_MODEL = "llama-3.3-70b-versatile"
ALLOWED_MODELS = {
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "qwen/qwen3-32b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
}

TOP_K = 5
MAX_QUESTION_LEN = 500
MAX_ANSWER_TOKENS = 700

SYSTEM_PROMPT = (
    "Ты — юридический ассистент Discord-сервера семьи Moriarty (RAGE Multiplayer, "
    "проект GTA5RP, сервер №20 Murrieta).\n\n"
    "ПРАВИЛА:\n"
    "1. Отвечай ТОЛЬКО на основе приведённых ниже выдержек из законодательной базы Murrieta.\n"
    "2. Если в выдержках нет ответа — напиши «В базе Murrieta я этого не нашёл» и НЕ выдумывай.\n"
    "3. Если статья найдена — назови её номер и кратко суть. Цитату оставь короткой (1-3 строки).\n"
    "4. Не путай Murrieta с другими серверами GTA5RP — кодексы у них разные.\n"
    "5. Отвечай на русском, кратко (3-6 предложений), без воды.\n"
    "6. Если вопрос не юридический или вне темы законов — кратко перенаправь: «Это вне моей юр-базы»."
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
      'rate_limit': {  # последние заголовки от Groq
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


def track_usage(input_tokens: int, output_tokens: int, headers: dict | None) -> dict:
    usage = load_usage()
    usage["totals"]["requests"] += 1
    usage["totals"]["input_tokens"] += input_tokens
    usage["totals"]["output_tokens"] += output_tokens

    day = datetime.now().strftime("%Y-%m-%d")
    d = usage["by_day"].setdefault(day, {"requests": 0, "input": 0, "output": 0})
    d["requests"] += 1
    d["input"] += input_tokens
    d["output"] += output_tokens

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


def _fts_query(words: list[str], op: str) -> str:
    """Строит FTS5 MATCH-запрос: каждое слово — префиксный матч, склеенные через op (AND/OR)."""
    parts = [f'"{w}"*' for w in words]
    return f" {op} ".join(parts) if parts else '""'


def _run_query(conn: sqlite3.Connection, query: str, top_k: int) -> list[dict]:
    try:
        rows = conn.execute(
            """
            SELECT laws.law_name, laws.article_code, laws.body, laws.url
            FROM laws_fts
            JOIN laws ON laws.id = laws_fts.rowid
            WHERE laws_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, top_k),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        {"law_name": n, "article_code": c, "body": b, "url": u}
        for n, c, b, u in rows
    ]


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
    if not Path(DB_FILE).exists():
        return {"documents": 0, "chunks": 0, "scraped_at": None}
    conn = sqlite3.connect(DB_FILE)
    try:
        cnt = conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
        docs = conn.execute("SELECT COUNT(DISTINCT law_name) FROM laws").fetchone()[0]
        ts = conn.execute("SELECT MAX(scraped_at) FROM laws").fetchone()[0]
        return {"documents": docs, "chunks": cnt, "scraped_at": ts}
    finally:
        conn.close()


async def db_stats() -> dict:
    return await asyncio.to_thread(_db_stats_sync)


# ──────────────────────────────────────────────────────────────
# Groq клиент
# ──────────────────────────────────────────────────────────────

_groq_client = None


def _get_groq():
    global _groq_client
    if _groq_client is not None:
        return _groq_client
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    try:
        from groq import AsyncGroq  # ленивая загрузка, чтобы бот не падал без groq
    except ImportError:
        return None
    _groq_client = AsyncGroq(api_key=api_key)
    return _groq_client


def _build_context(chunks: list[dict]) -> str:
    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(
            f"[{i}] {c['law_name']} — {c['article_code']}\n"
            f"{c['body']}\n"
            f"Источник: {c['url']}"
        )
    return "\n\n".join(parts)


async def ask_groq(question: str, chunks: list[dict], model: str) -> tuple[str, dict | None, dict]:
    """
    Возвращает (answer_text, response_headers, usage_dict).
    usage_dict = {'input_tokens': int, 'output_tokens': int}
    """
    client = _get_groq()
    if client is None:
        raise RuntimeError("GROQ_API_KEY не задан в .env")

    context = _build_context(chunks)
    user_msg = (
        f"ВЫДЕРЖКИ ИЗ ЗАКОНОДАТЕЛЬНОЙ БАЗЫ MURRIETA:\n\n{context}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"ВОПРОС: {question}"
    )

    raw = await client.chat.completions.with_raw_response.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.2,
        max_tokens=MAX_ANSWER_TOKENS,
    )
    headers = dict(raw.headers)
    completion = await raw.parse()
    answer = completion.choices[0].message.content or ""
    u = getattr(completion, "usage", None)
    usage = {
        "input_tokens": getattr(u, "prompt_tokens", 0) if u else 0,
        "output_tokens": getattr(u, "completion_tokens", 0) if u else 0,
    }
    return answer.strip(), headers, usage


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


def _format_answer_embed(question: str, answer: str, chunks: list[dict], model: str) -> discord.Embed:
    embed = discord.Embed(
        title="📖 Юридический ассистент Murrieta",
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
            lines.append(f"• [{c['law_name']} — {c['article_code']}]({c['url']})")
        if lines:
            embed.add_field(name="Источники", value="\n".join(lines), inline=False)
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
                chunks = await search_laws(question, top_k=TOP_K)
            except Exception as e:
                await message.reply(f"❌ Ошибка поиска в базе: `{e}`")
                return

            if not chunks:
                await message.reply(
                    "📖 В базе Murrieta я ничего не нашёл по этому вопросу. "
                    "Проверь, обновлена ли база (`python update_laws.py`)."
                )
                return

            try:
                answer, headers, usage = await ask_groq(question, chunks, model)
            except RuntimeError as e:
                await message.reply(f"⚠️ {e}")
                return
            except Exception as e:
                await message.reply(f"❌ Groq упал: `{e}`")
                return

            track_usage(usage["input_tokens"], usage["output_tokens"], headers)

        embed = _format_answer_embed(question, answer, chunks, model)
        await message.reply(embed=embed, mention_author=False)

    @tree.command(name="токены", description="Статус лимитов Groq и расход токенов")
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

        embed = discord.Embed(title="📊 Юр-ассистент: статус", color=0x3498DB)
        embed.add_field(
            name="База законов",
            value=f"Документов: **{stats['documents']}**\n"
                  f"Чанков: **{stats['chunks']}**\n"
                  f"Обновлена: {stats['scraped_at'] or '—'}",
            inline=False,
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
        if rl:
            embed.add_field(
                name="Лимиты Groq (после последнего запроса)",
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
                value="Запросов ещё не было — лимиты появятся после первого ответа.",
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

    @tree.command(name="закон_модель", description="Сменить модель Groq для ответов")
    @app_commands.describe(модель="Например: llama-3.3-70b-versatile, qwen/qwen3-32b, openai/gpt-oss-120b")
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
        await interaction.response.send_message(f"✅ Модель Groq: `{модель}`", ephemeral=True)

    @tree.command(name="закон_тест", description="Отладка: задать вопрос ассистенту напрямую")
    @app_commands.describe(вопрос="Юридический вопрос для теста")
    async def laws_test_cmd(interaction: discord.Interaction, вопрос: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Только для админа.", ephemeral=True)
            return
        cfg = load_config().get(str(interaction.guild_id), {})
        model = cfg.get("model", DEFAULT_MODEL)
        await interaction.response.defer(ephemeral=True)
        chunks = await search_laws(вопрос, top_k=TOP_K)
        if not chunks:
            await interaction.followup.send("📖 В базе ничего не нашёл.", ephemeral=True)
            return
        try:
            answer, headers, usage = await ask_groq(вопрос, chunks, model)
        except Exception as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return
        track_usage(usage["input_tokens"], usage["output_tokens"], headers)
        embed = _format_answer_embed(вопрос, answer, chunks, model)
        await interaction.followup.send(embed=embed, ephemeral=True)
