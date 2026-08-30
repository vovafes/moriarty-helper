"""
update_laws.py — скрейпер правил и законодательной базы Murrieta (Сервер №20).

Источники:
  1. THREADS / MULTI_THREADS — захардкоженный список тем «Законодательная база»
     (Конституция, кодексы, судебные прецеденты) — форум не входит в ROOT_FORUM_URLS,
     поэтому список тем поддерживается вручную.
  2. ROOT_FORUM_URLS — динамический обход: «Правила проекта» и «Правила сервера
     Murrieta» со ВСЕМИ подразделами и ВСЕМИ темами внутри. Новые/удалённые темы
     подхватываются сами при следующем запуске, руками список поддерживать не нужно.

Парсит первый пост каждой темы, разбивает на чанки и кладёт в laws.sqlite
с FTS5-индексом для быстрого поиска BM25.

Запуск:
    python update_laws.py                    # обновить всю базу (обход + статика)
    python update_laws.py --no-discover       # только статические THREADS/MULTI_THREADS
    python update_laws.py --thread 3237255    # только одну тему (без обхода разделов)
    python update_laws.py --stats             # показать что в базе сейчас
"""

import argparse
import gzip
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

DB_FILE = "laws.sqlite"
RAW_DIR = Path("laws_raw")  # человекочитаемые .md копии, попадают в git
FORUM_BASE = "https://forum.gta5rp.com"
THREAD_URL = FORUM_BASE + "/threads/{slug}.{tid}/"

# Список тем раздела «Законодательная база» (forum 1732).
# Формат: (thread_id, slug, отображаемое имя закона)
THREADS = [
    (3237255, "konstitucija-shtata-san-andreas", "Конституция Штата Сан-Андреас"),
    (3237254, "ugolovno-administrativnyi-kodeks-shtata-san-andreas", "Уголовно-административный кодекс"),
    (3237253, "processualnyi-kodeks-shtata-san-andreas", "Процессуальный кодекс"),
    (3237252, "zakon-o-pravitelstve-shtata-san-andreas", "Закон «О правительстве»"),
    (3237251, "zakon-o-prokurature-shtata-san-andreas", "Закон «О прокуратуре»"),
    (3237242, "sudebnyi-kodeks-shtata-san-andreas", "Судебный кодекс"),
    (3237241, "kodeks-ehtiki-i-sluzhebnogo-povedenija-gosudarstvennyx-sluzhaschix", "Кодекс этики гос. служащих"),
    (3237240, "trudovoi-kodeks-shtata-san-andreas", "Трудовой кодекс"),
    (3237239, "dorozhnyi-kodeks-shtata-san-andreas", "Дорожный кодекс (ПДД)"),
    (3237238, "zakon-ob-advokatskoi-dejatelnosti-i-advokature-v-shtate-san-andreas", "Закон «Об адвокатской деятельности»"),
    (3237237, "zakon-o-statuse-neprikosnovennosti-dolzhnostnyx-lic-v-shtate-san-andreas", "Закон «О неприкосновенности должностных лиц»"),
    (3237236, "zakon-o-kongresse-shtata-san-andreas", "Закон «О Конгрессе»"),
    (3237222, "zakon-o-regulirovanii-oborota-oruzhija-boepripasov-i-specsredstv-v-shtate-san-andreas", "Закон «Об обороте оружия»"),
    (3237221, "zakon-o-zakrytyx-i-oxranjaemyx-territorijax", "Закон «О закрытых территориях»"),
    (3237220, "zakon-ob-osobyx-rezhimax-i-protokolax-zaschity-v-shtate-san-andreas", "Закон «Об особых режимах»"),
    (3237219, "zakon-o-federalnom-bjuro-rassledovanii-fib", "Закон «О ФБР» [FIB]"),
    (3237218, "zakon-o-policeiskom-departamente-goroda-los-santos-lspd", "Закон «О LSPD»"),
    (3237217, "zakon-o-departamente-sherifa-okruga-blein-lssd", "Закон «О LSSD»"),
    (3237216, "zakon-o-san-andreas-national-guard-army", "Закон «О National Guard» [ARMY]"),
    (3237189, "zakon-o-federalnoi-tjurme-saspa", "Закон «О Федеральной тюрьме» [SASPA]"),
    (3237188, "zakon-o-united-states-secret-service-usss", "Закон «О USSS»"),
    (3237187, "zakon-ob-ehkstrennoi-medicinskoi-sluzhbe-shtata-san-andreas-ems", "Закон «О EMS»"),
    (3237186, "zakon-o-sredstvax-massovoi-informacii-v-shtate-san-andreas", "Закон «О СМИ»"),
    (3237185, "zakon-o-gosudarstvennoi-taine", "Закон «О гос. тайне»"),
    (3237184, "zakon-o-predprinimatelskoi-dejatelnosti", "Закон «О предпринимательской деятельности»"),
    (3237183, "zakon-o-rozyske-grazhdan-v-shtate-san-andreas", "Закон «О розыске граждан»"),
    (3237182, "zakon-o-registracii-transportnyx-sredstv", "Закон «О регистрации ТС»"),
    (3237180, "zakon-o-ljubitelskom-i-professionalnom-rybolovstve-i-oxote", "Закон «О рыболовстве и охоте»"),
    (3237179, "zakon-o-politicheskix-partijax-v-shtate-san-andreas", "Закон «О политических партиях»"),
    (3237178, "zakon-o-jurisdikcii", "Закон «О юрисдикции»"),
    (3237176, "zakon-o-sluzhbe-sudebnyx-marshalov-ssha-usms", "Закон «О USMS»"),
    (3237170, "zakon-ob-upravlenii-gosudarstvennoi-sobstvennostju", "Закон «Об управлении гос. собственностью»"),

]

# ─── Разделы, которые обходим ДИНАМИЧЕСКИ (все подразделы + все темы) ───
# Правила проекта в целом + правила сервера Murrieta. Больше не нужно вручную
# добавлять thread_id при появлении новых тем — скрипт сам находит все подразделы
# (node--forum) и все темы (structItem--thread) внутри них рекурсивно.
ROOT_FORUM_URLS = [
    "https://forum.gta5rp.com/forums/pravila-proyekta.5/",
    "https://forum.gta5rp.com/forums/pravila-servera-murrieta.1691/",
]

# Темы со множеством постов и мусором — обходим все страницы и фильтруем по автору.
# include_first_post=True означает что первый пост (обычно оглавление) берём всегда,
# независимо от author_filter.
MULTI_THREADS = [
    {
        "tid": 3347921,
        "slug": "sudebnye-precedenty-i-tolkovanija",
        "name": "Судебные прецеденты и толкования",
        "author_filter": "Luis_Kalimator",
        "include_first_post": True,
        "max_pages": 50,
    },
]

# Максимальный размер чанка (в символах) при дроблении длинных документов.
CHUNK_SIZE = 1800
CHUNK_OVERLAP = 200


# ──────────────────────────────────────────────────────────────
# Сеть
# ──────────────────────────────────────────────────────────────

def fetch(url: str, retries: int = 3) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Encoding": "gzip",
            "Accept-Language": "ru,en;q=0.5",
        },
    )
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data.decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Не удалось скачать {url}: {last_err}")


# ──────────────────────────────────────────────────────────────
# Парсинг XenForo
# ──────────────────────────────────────────────────────────────

def _post_bbwrapper_to_text(bb) -> str:
    """Конвертирует .bbWrapper элемент в чистый текст с переводами строк."""
    # Убираем скрипты, стили, картинки
    for tag in bb.select("script, style, img, .bbCodeBlock-title"):
        tag.decompose()

    # <br> → перевод строки
    for br in bb.find_all("br"):
        br.replace_with("\n")

    # Параграфы / div / table cells / li → отдельные строки
    for tag in bb.find_all(["p", "div", "li", "tr"]):
        tag.append("\n")
    for tag in bb.find_all(["td", "th"]):
        tag.append(" | ")

    return clean_text(bb.get_text())


def extract_first_post_text(html: str) -> tuple[str, str | None]:
    """
    Достаёт текст первого поста темы XenForo.
    Возвращает (text, post_anchor) где post_anchor вида 'post-8856297' для ссылок.
    """
    soup = BeautifulSoup(html, "html.parser")
    article = soup.select_one("article.message.message--post")
    if not article:
        raise RuntimeError("Не найден первый пост: article.message--post")

    anchor = article.get("data-content")  # 'post-XXXXXXX'
    bb = article.select_one(".bbWrapper")
    if not bb:
        raise RuntimeError("Не найден .bbWrapper в первом посте")
    return _post_bbwrapper_to_text(bb), anchor


def extract_all_posts(html: str) -> list[dict]:
    """
    Извлекает все посты со страницы XenForo.
    Возвращает [{'author': str, 'anchor': str, 'text': str, 'is_first_on_page': bool}].
    """
    soup = BeautifulSoup(html, "html.parser")
    posts = []
    for idx, article in enumerate(soup.select("article.message.message--post")):
        bb = article.select_one(".bbWrapper")
        if not bb:
            continue
        author = article.get("data-author") or ""
        anchor = article.get("data-content") or ""
        text = _post_bbwrapper_to_text(bb)
        posts.append({
            "author": author,
            "anchor": anchor,
            "text": text,
            "is_first_on_page": idx == 0,
        })
    return posts


def extract_max_page(html: str) -> int:
    """Сколько страниц в теме (по pageNav). Возвращает 1 если пагинации нет."""
    # XenForo: <li class="pageNav-page"><a href=".../page-N">N</a></li>
    nums = re.findall(r'href="[^"]*?/page-(\d+)', html)
    if not nums:
        return 1
    return max(int(n) for n in nums)


def _extract_subforums(html: str) -> list[str]:
    """Ссылки на подразделы (node--forum) на странице раздела форума."""
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for node in soup.select("div.node--forum"):
        a = node.select_one(".node-title a")
        if a and a.get("href"):
            urls.append(FORUM_BASE + a["href"])
    return urls


THREAD_LINK_RE = re.compile(r"^/threads/([a-z0-9-]+)\.(\d+)/$")


def _extract_threads(html: str) -> list[tuple[int, str, str]]:
    """Темы (structItem--thread) на странице раздела: [(tid, slug, title)]."""
    soup = BeautifulSoup(html, "html.parser")
    threads = []
    seen = set()
    for item in soup.select("div.structItem--thread"):
        title_div = item.select_one(".structItem-title")
        if not title_div:
            continue
        a = None
        for cand in title_div.select("a[href^='/threads/']"):
            if "labelLink" in (cand.get("class") or []):
                continue
            a = cand
            break
        if not a:
            continue
        m = THREAD_LINK_RE.match(a["href"])
        if not m:
            continue
        slug, tid = m.group(1), int(m.group(2))
        if tid in seen:
            continue
        seen.add(tid)
        threads.append((tid, slug, a.get_text(strip=True)))
    return threads


def discover_forum_tree(root_url: str, max_depth: int = 5) -> list[tuple[int, str, str]]:
    """
    Рекурсивно обходит раздел форума: все вложенные подразделы + все темы во всех
    (включая пагинированные) списках тем. Возвращает [(tid, slug, title), ...] без дублей.
    """
    visited_forums: set[str] = set()
    found: dict[int, tuple[str, str]] = {}

    def visit(url: str, depth: int) -> None:
        if url in visited_forums or depth > max_depth:
            return
        visited_forums.add(url)
        html = fetch(url)

        for tid, slug, title in _extract_threads(html):
            found.setdefault(tid, (slug, title))

        n_pages = extract_max_page(html)
        for p in range(2, n_pages + 1):
            time.sleep(0.5)
            page_html = fetch(url + f"page-{p}")
            for tid, slug, title in _extract_threads(page_html):
                found.setdefault(tid, (slug, title))

        for sub_url in _extract_subforums(html):
            time.sleep(0.5)
            visit(sub_url, depth + 1)

    visit(root_url, 0)
    return [(tid, slug, title) for tid, (slug, title) in found.items()]


PRECEDENT_HEADER = re.compile(
    r"(Прецедент\s*№\s*\d+|Решение\s+Верховного\s+Суда|Толкование\s*№\s*\d+|"
    r"Постановление\s*№\s*\d+|Дело\s*№\s*\d+|Апелляция\s*№\s*\d+)",
    re.IGNORECASE,
)


def derive_post_code(text: str, fallback: str) -> str:
    """Достаём короткий код-заголовок поста-прецедента."""
    m = PRECEDENT_HEADER.search(text)
    if m:
        return m.group(1).strip()
    # Первая непустая строка, обрезанная
    for line in text.splitlines():
        line = line.strip()
        if len(line) >= 5:
            return line[:80]
    return fallback


def clean_text(text: str) -> str:
    # Нормализуем неразрывные пробелы и zero-width
    text = text.replace(" ", " ").replace("​", "")
    # Множественные пробелы
    text = re.sub(r"[ \t]+", " ", text)
    # Пробелы вокруг переноса
    text = re.sub(r" *\n *", "\n", text)
    # Сжимаем > 2 переносов до 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ──────────────────────────────────────────────────────────────
# Разбиение на чанки по статьям
# ──────────────────────────────────────────────────────────────

ARTICLE_PATTERNS = [
    # «Статья 12.», «Статья 12 », «Статья 12.8»
    re.compile(r"^\s*(Статья\s+\d+(?:\.\d+)?\.?)\s", re.MULTILINE),
    # «12.8 ...», «1.1 ...» — нумерация УК
    re.compile(r"^(\d+\.\d+)\s", re.MULTILINE),
    # «Глава I», «Глава 1»
    re.compile(r"^\s*(Глава\s+[IVXLCDM\d]+\.?)\s", re.MULTILINE),
]


def split_into_chunks(text: str, law_name: str) -> list[tuple[str, str]]:
    """
    Возвращает список (article_code, body) — куски документа.
    Сначала пробует разбить по «Статья X»/«1.1»; если не получилось — режет по размеру.
    """
    # Пробуем найти точки разреза по статьям
    splits: list[tuple[int, str]] = []
    for pat in ARTICLE_PATTERNS:
        for m in pat.finditer(text):
            splits.append((m.start(), m.group(1).strip()))
        if splits:
            break  # используем первый паттерн который сработал

    if not splits:
        # Не нашли структуру — режем по размеру с overlap
        return _chunk_by_size(text, default_code="(весь документ)")

    splits.sort(key=lambda x: x[0])
    chunks: list[tuple[str, str]] = []

    # Преамбула — текст до первой статьи
    if splits[0][0] > 0:
        preamble = text[: splits[0][0]].strip()
        if len(preamble) > 50:
            chunks.append(("(преамбула)", preamble))

    for i, (start, code) in enumerate(splits):
        end = splits[i + 1][0] if i + 1 < len(splits) else len(text)
        body = text[start:end].strip()
        if len(body) > CHUNK_SIZE * 1.5:
            # Слишком длинная статья — режем дальше
            for sub_code, sub_body in _chunk_by_size(body, default_code=code):
                chunks.append((sub_code, sub_body))
        elif body:
            chunks.append((code, body))

    return chunks


def _chunk_by_size(text: str, default_code: str) -> list[tuple[str, str]]:
    chunks = []
    i = 0
    n = len(text)
    part = 1
    while i < n:
        end = min(i + CHUNK_SIZE, n)
        if end < n:
            # Пытаемся обрезать на границе абзаца
            br = text.rfind("\n\n", i, end)
            if br > i + CHUNK_SIZE // 2:
                end = br
        body = text[i:end].strip()
        if body:
            code = f"{default_code} ч.{part}" if part > 1 or end < n else default_code
            chunks.append((code, body))
            part += 1
        if end >= n:
            break
        i = max(end - CHUNK_OVERLAP, i + 1)
    return chunks


# ──────────────────────────────────────────────────────────────
# БД
# ──────────────────────────────────────────────────────────────

def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS laws (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER NOT NULL,
            law_name TEXT NOT NULL,
            article_code TEXT NOT NULL,
            body TEXT NOT NULL,
            url TEXT NOT NULL,
            scraped_at TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS laws_fts USING fts5(
            law_name, article_code, body,
            content='laws', content_rowid='id',
            tokenize='unicode61 remove_diacritics 0'
        );

        CREATE TRIGGER IF NOT EXISTS laws_ai AFTER INSERT ON laws BEGIN
            INSERT INTO laws_fts(rowid, law_name, article_code, body)
            VALUES (new.id, new.law_name, new.article_code, new.body);
        END;
        CREATE TRIGGER IF NOT EXISTS laws_ad AFTER DELETE ON laws BEGIN
            INSERT INTO laws_fts(laws_fts, rowid, law_name, article_code, body)
            VALUES('delete', old.id, old.law_name, old.article_code, old.body);
        END;
    """)
    conn.commit()


def write_markdown(tid: int, slug: str, name: str, url: str,
                   text: str, chunks: list[tuple[str, str]], scraped_at: str) -> Path:
    """Сохраняет человекочитаемую копию темы для git."""
    RAW_DIR.mkdir(exist_ok=True)
    short_slug = slug[:60].rstrip("-")
    path = RAW_DIR / f"{tid}_{short_slug}.md"

    lines = [
        f"# {name}",
        "",
        f"- **Thread ID**: {tid}",
        f"- **URL**: {url}",
        f"- **Скрейп**: {scraped_at}",
        f"- **Чанков**: {len(chunks)}",
        f"- **Длина текста**: {len(text)} символов",
        "",
        "---",
        "",
    ]
    for code, body in chunks:
        lines.append(f"## {code}")
        lines.append("")
        lines.append(body)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def replace_thread(conn: sqlite3.Connection, thread_id: int, rows: list[dict]) -> int:
    conn.execute("DELETE FROM laws WHERE thread_id = ?", (thread_id,))
    conn.executemany(
        "INSERT INTO laws(thread_id, law_name, article_code, body, url, scraped_at) "
        "VALUES (:thread_id, :law_name, :article_code, :body, :url, :scraped_at)",
        rows,
    )
    conn.commit()
    return len(rows)


# ──────────────────────────────────────────────────────────────
# Pipeline
# ──────────────────────────────────────────────────────────────

def process_multipage_thread(conn: sqlite3.Connection, spec: dict) -> int:
    """
    Обходит все страницы темы, фильтрует посты по автору (и опционально берёт
    первый пост целиком как оглавление). Каждый пост → отдельный чанк.
    """
    tid = spec["tid"]
    slug = spec["slug"]
    name = spec["name"]
    author_filter = spec.get("author_filter")
    include_first = spec.get("include_first_post", True)
    max_pages = spec.get("max_pages", 50)

    base_url = THREAD_URL.format(slug=slug, tid=tid)
    print(f"  ↻ {name}  [multi-page]")
    print(f"    {base_url}")
    print(f"    Фильтр: автор={author_filter or 'все'}, include_first={include_first}")

    # Страница 1 — определяем количество страниц
    html = fetch(base_url)
    n_pages = min(extract_max_page(html), max_pages)
    print(f"    Страниц: {n_pages}")

    all_posts: list[dict] = []
    first_page_posts = extract_all_posts(html)
    all_posts.extend(first_page_posts)

    for p in range(2, n_pages + 1):
        time.sleep(0.6)
        page_url = base_url + f"page-{p}"
        page_html = fetch(page_url)
        page_posts = extract_all_posts(page_html)
        # На дополнительных страницах первый пост — НЕ оглавление темы
        for post in page_posts:
            post["is_first_on_page"] = False
        all_posts.extend(page_posts)
        print(f"    стр. {p}: +{len(page_posts)} постов")

    # Фильтрация: сохраняем самый первый пост темы (оглавление)
    # и все посты от author_filter
    kept: list[dict] = []
    for i, post in enumerate(all_posts):
        is_thread_first = (i == 0)
        if is_thread_first and include_first:
            kept.append(post)
        elif author_filter is None or post["author"] == author_filter:
            # Игнорируем совсем короткие реплики (< 100 символов)
            if len(post["text"]) >= 100:
                kept.append(post)

    print(f"    Постов всего: {len(all_posts)}, оставлено после фильтра: {len(kept)}")

    chunks: list[tuple[str, str]] = []
    for i, post in enumerate(kept):
        is_thread_first = (i == 0 and include_first)
        if is_thread_first:
            code = "Оглавление"
        else:
            code = derive_post_code(post["text"], fallback=f"Пост #{i}")
        body = post["text"]
        # Если пост очень большой — режем по размеру (на статьи прецеденты не делятся)
        if len(body) > CHUNK_SIZE * 1.5:
            for sub_code, sub_body in _chunk_by_size(body, default_code=code):
                chunks.append((sub_code, sub_body))
        else:
            chunks.append((code, body))

    now = datetime.now().isoformat(timespec="seconds")
    # URL первого поста темы как канонический
    first_anchor = all_posts[0]["anchor"] if all_posts else ""
    canonical_url = f"{base_url}#{first_anchor}" if first_anchor else base_url
    rows = [
        dict(
            thread_id=tid,
            law_name=name,
            article_code=code,
            body=body,
            url=canonical_url,
            scraped_at=now,
        )
        for code, body in chunks
    ]
    md_path = write_markdown(tid, slug, name, canonical_url,
                             "\n\n".join(c[1] for c in chunks), chunks, now)
    print(f"    md: {md_path}")
    return replace_thread(conn, tid, rows)


def process_thread(conn: sqlite3.Connection, tid: int, slug: str, name: str) -> int:
    url = THREAD_URL.format(slug=slug, tid=tid)
    print(f"  ↻ {name}")
    print(f"    {url}")
    html = fetch(url)
    text, anchor = extract_first_post_text(html)
    print(f"    Длина текста: {len(text)} символов")

    chunks = split_into_chunks(text, name)
    print(f"    Чанков: {len(chunks)}")

    now = datetime.now().isoformat(timespec="seconds")
    post_url = f"{url}#{anchor}" if anchor else url
    rows = [
        dict(
            thread_id=tid,
            law_name=name,
            article_code=code,
            body=body,
            url=post_url,
            scraped_at=now,
        )
        for code, body in chunks
    ]
    md_path = write_markdown(tid, slug, name, post_url, text, chunks, now)
    print(f"    md: {md_path}")
    return replace_thread(conn, tid, rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thread", type=int, help="Обработать только одну тему по ID")
    parser.add_argument("--stats", action="store_true", help="Показать содержимое БД и выйти")
    parser.add_argument(
        "--no-discover", action="store_true",
        help="Не обходить ROOT_FORUM_URLS динамически (только THREADS/MULTI_THREADS)",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(DB_FILE)
    init_db(conn)

    if args.stats:
        rows = conn.execute(
            "SELECT law_name, COUNT(*), MAX(scraped_at) "
            "FROM laws GROUP BY law_name ORDER BY law_name"
        ).fetchall()
        if not rows:
            print("База пуста.")
            return 0
        total = 0
        for name, cnt, ts in rows:
            print(f"  {cnt:>4}  {name}  ({ts})")
            total += cnt
        print(f"\nИтого: {total} записей в {len(rows)} документах")
        return 0

    single_targets = list(THREADS)
    multi_targets = list(MULTI_THREADS)
    known_ids = {t[0] for t in THREADS} | {t["tid"] for t in MULTI_THREADS}

    if not args.no_discover and not args.thread:
        print(f"Обхожу {len(ROOT_FORUM_URLS)} раздела форума (подразделы + темы)...")
        for root_url in ROOT_FORUM_URLS:
            print(f"  → {root_url}")
            try:
                discovered = discover_forum_tree(root_url)
            except Exception as e:
                print(f"    ✗ ОШИБКА обхода раздела: {e}")
                continue
            new_count = 0
            for tid, slug, title in discovered:
                if tid in known_ids:
                    continue  # уже покрыт статическим списком (напр. Законодательная база)
                known_ids.add(tid)
                single_targets.append((tid, slug, title))
                new_count += 1
            print(f"    найдено тем: {len(discovered)}, новых: {new_count}")
        print()

    if args.thread:
        single_targets = [t for t in single_targets if t[0] == args.thread]
        multi_targets = [t for t in multi_targets if t["tid"] == args.thread]
        if not single_targets and not multi_targets:
            print(f"Тема {args.thread} не найдена ни в THREADS/MULTI_THREADS, "
                  f"ни в обнаруженных разделах (обход при --thread не выполняется).")
            return 1

    total = len(single_targets) + len(multi_targets)
    print(f"Обновляю {total} тем ({len(single_targets)} одностраничных, "
          f"{len(multi_targets)} многостраничных)...\n")
    ok = 0
    fail = 0
    total_chunks = 0

    for tid, slug, name in single_targets:
        try:
            n = process_thread(conn, tid, slug, name)
            total_chunks += n
            ok += 1
            print(f"    ✓ записано {n} чанков\n")
        except Exception as e:
            fail += 1
            print(f"    ✗ ОШИБКА: {e}\n")
        time.sleep(0.6)

    for spec in multi_targets:
        try:
            n = process_multipage_thread(conn, spec)
            total_chunks += n
            ok += 1
            print(f"    ✓ записано {n} чанков\n")
        except Exception as e:
            fail += 1
            print(f"    ✗ ОШИБКА: {e}\n")
        time.sleep(0.6)

    pruned = 0
    if not args.thread and fail == 0:
        current_ids = {t[0] for t in single_targets} | {t["tid"] for t in multi_targets}
        stale = [
            row[0] for row in
            conn.execute("SELECT DISTINCT thread_id FROM laws").fetchall()
            if row[0] not in current_ids
        ]
        for tid in stale:
            row = conn.execute(
                "SELECT law_name FROM laws WHERE thread_id = ? LIMIT 1", (tid,)
            ).fetchone()
            law_name = row[0] if row else "?"
            n = conn.execute("DELETE FROM laws WHERE thread_id = ?", (tid,)).rowcount
            conn.commit()
            pruned += n
            print(f"  ✂ удалена устаревшая тема {tid} «{law_name}» ({n} чанков — "
                  f"темы больше нет в текущем обходе разделов)")
            for md in RAW_DIR.glob(f"{tid}_*.md"):
                md.unlink()

    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"Готово: {ok} OK, {fail} ошибок, {total_chunks} чанков всего"
          f"{f', удалено устаревших: {pruned}' if pruned else ''}.")
    print(f"База: {DB_FILE}")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
