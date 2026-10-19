"""
update_wiki.py — скрейпер игровой вики https://wiki.gta5rp.com/ для ассистента.

Берёт из sitemap.xml страницы /docs/* (механики, организации, работы, телефон...)
и /factions/* (описания фракций). Каталоги (/vehicles, /businesses, /skins и т.п.)
пропускаются — это динамические списки, а не текст.

Контент страницы Next.js присылает в скрытом блоке <div hidden id="S:N">, поэтому
разбираем самый большой такой блок.

Кладёт чанки в таблицы wiki / wiki_fts той же laws.sqlite (законы в таблице laws
не трогаются — update_laws.py чистит свою таблицу независимо). Человекочитаемые
копии пишет в wiki_raw/.

Запуск:
    python update_wiki.py                      # обновить все страницы
    python update_wiki.py --page docs/inf-org/family-war
    python update_wiki.py --stats
"""

import argparse
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from update_laws import CHUNK_SIZE, _chunk_by_size, clean_text, fetch

DB_FILE = "laws.sqlite"
RAW_DIR = Path("wiki_raw")
WIKI_BASE = "https://wiki.gta5rp.com"
INCLUDE_PREFIXES = ("docs/", "factions/")

_LOC = re.compile(r"<loc>([^<]+)</loc>")


def discover_pages() -> list[str]:
    xml = fetch(WIKI_BASE + "/sitemap.xml")
    paths = []
    for url in _LOC.findall(xml):
        path = url.removeprefix(WIKI_BASE).strip("/")
        if path.startswith(INCLUDE_PREFIXES):
            paths.append(path)
    return paths


# ──────────────────────────────────────────────────────────────
# HTML → markdown-подобный текст
# ──────────────────────────────────────────────────────────────

def _content_root(soup: BeautifulSoup):
    blocks = [
        d for d in soup.find_all("div", attrs={"hidden": True})
        if d.get("id", "").startswith("S:")
    ]
    return max(blocks, key=lambda d: len(d.get_text()), default=None)


def _inline(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).replace(" ,", ",").replace(" .", ".")


def _is_toc(el) -> bool:
    return any("toc" in c.lower() for c in el.get("class", []))


def _cls_suffix(el, *suffixes: str) -> bool:
    """Классы CSS-модулей вида 'hash_row' — хеш меняется, суффикс стабилен."""
    return any(c.endswith(suffixes) for c in el.get("class", []))


_BLOCK_TAGS = {"h2", "h3", "h4", "p", "li", "tr", "summary", "pre"}


def _is_block(el) -> bool:
    return el.name in _BLOCK_TAGS or (
        el.name == "div" and _cls_suffix(el, "_row", "_head", "_infoCard", "_chips")
    )


def html_to_text(html: str) -> tuple[str, str]:
    """Возвращает (заголовок страницы, текст с '## ' / '### ' заголовками)."""
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "svg", "button", "img", "input"]):
        t.decompose()
    root = _content_root(soup)
    if root is None:
        return "", ""

    # Хлебные крошки и оглавление дублируют текст страницы
    for nav in root.find_all("nav"):
        nav.decompose()
    for el in root.find_all(_is_toc):
        el.decompose()

    title_el = root.find("h1")
    title = _inline(title_el) if title_el else ""

    lines: list[str] = []
    seen_block = set()
    for el in root.find_all(_is_block):
        # вложенные p внутри li и т.п. уже учтены родителем
        if any(id(p) in seen_block for p in el.parents):
            continue
        if el.name in ("h2", "h3", "h4"):
            # у заголовков в конце стоит якорь «#»
            text = _inline(el).rstrip("#").strip()
            if text:
                lines.append("\n" + ("## " if el.name == "h2" else "### ") + text)
        elif el.name == "li":
            text = _inline(el)
            if text:
                lines.append("• " + text)
            seen_block.add(id(el))
        elif el.name == "tr":
            cells = [_inline(c) for c in el.find_all(["th", "td"])]
            cells = [c for c in cells if c]
            if cells:
                lines.append(" | ".join(cells))
            seen_block.add(id(el))
        elif _cls_suffix(el, "_infoCard"):
            spans = [_inline(c) for c in el.find_all("span")]
            if len(spans) >= 2:
                lines.append(f"{spans[0]}: {' '.join(spans[1:])}")
            seen_block.add(id(el))
        elif _cls_suffix(el, "_chips"):
            tags = [_inline(c) for c in el.find_all("span")]
            if tags:
                lines.append(", ".join(t for t in tags if t))
            seen_block.add(id(el))
        elif _cls_suffix(el, "_row", "_head"):
            # строка div-таблицы: ячейки — листовые span (номер ранга склеиваем с названием)
            num = el.find("span", class_=lambda c: c and c.endswith("_num"))
            num_text = _inline(num) if num else ""
            if num:
                num.decompose()
            cells = [_inline(c) for c in el.find_all("span", recursive=False)] or [_inline(el)]
            cells = [c for c in cells if c]
            num = num_text
            if cells:
                lines.append((f"{num}. " if num else "") + " | ".join(cells))
            seen_block.add(id(el))
        else:
            text = _inline(el)
            if text:
                lines.append(text)

    text = clean_text("\n".join(lines))
    # Оглавление страницы («• 1 Требования ...») до первого заголовка не нужно
    head, sep, rest = text.partition("\n## ")
    head = "\n".join(l for l in head.splitlines() if not re.match(r"• \d+(\.\d+)* ", l))
    return title, clean_text(head + sep + rest)


# ──────────────────────────────────────────────────────────────
# Чанки по разделам
# ──────────────────────────────────────────────────────────────

def split_sections(title: str, text: str) -> list[tuple[str, str]]:
    """Режем по заголовкам '## ' — один раздел вики = один чанк (длинные дробим)."""
    parts = re.split(r"(?m)^(?=## )", text)
    chunks: list[tuple[str, str]] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = re.match(r"## (.+)", part)
        code = m.group(1).strip() if m else "Введение"
        if m:
            part = part[m.end():].strip() or part  # заголовок хранится в section, не в теле
        if len(part) > CHUNK_SIZE * 1.5:
            chunks.extend(_chunk_by_size(part, default_code=code))
        else:
            chunks.append((code, part))

    # Крошечные разделы (< 150 символов) склеиваем со следующим, чтобы не терять контекст
    merged: list[tuple[str, str]] = []
    buf_code, buf_body = None, ""
    for code, body in chunks:
        if buf_body:
            body = buf_body + "\n\n" + body
            code = buf_code
            buf_code, buf_body = None, ""
        if len(body) < 150:
            buf_code, buf_body = code, body
        else:
            merged.append((code, body))
    if buf_body:
        merged.append((buf_code, buf_body))
    return merged


# ──────────────────────────────────────────────────────────────
# БД
# ──────────────────────────────────────────────────────────────

def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS wiki (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page TEXT NOT NULL,
            page_title TEXT NOT NULL,
            section TEXT NOT NULL,
            body TEXT NOT NULL,
            url TEXT NOT NULL,
            scraped_at TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5(
            page_title, section, body,
            content='wiki', content_rowid='id',
            tokenize='unicode61 remove_diacritics 0'
        );

        CREATE TRIGGER IF NOT EXISTS wiki_ai AFTER INSERT ON wiki BEGIN
            INSERT INTO wiki_fts(rowid, page_title, section, body)
            VALUES (new.id, new.page_title, new.section, new.body);
        END;
        CREATE TRIGGER IF NOT EXISTS wiki_ad AFTER DELETE ON wiki BEGIN
            INSERT INTO wiki_fts(wiki_fts, rowid, page_title, section, body)
            VALUES('delete', old.id, old.page_title, old.section, old.body);
        END;
    """)
    conn.commit()


def write_markdown(page: str, title: str, url: str, chunks: list[tuple[str, str]], now: str) -> Path:
    RAW_DIR.mkdir(exist_ok=True)
    path = RAW_DIR / (page.replace("/", "__") + ".md")
    lines = [f"# {title}", "", f"- **URL**: {url}", f"- **Скрейп**: {now}", f"- **Чанков**: {len(chunks)}", "", "---", ""]
    for code, body in chunks:
        lines += [f"## {code}", "", body, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def process_page(conn: sqlite3.Connection, page: str) -> int:
    url = f"{WIKI_BASE}/{page}"
    title, text = html_to_text(fetch(url))
    if len(text) < 100:
        raise RuntimeError(f"пустой контент ({len(text)} символов) — вёрстка вики изменилась?")
    title = title or page
    chunks = split_sections(title, text)
    now = datetime.now().isoformat(timespec="seconds")
    rows = [
        dict(page=page, page_title=title, section=code, body=body, url=url, scraped_at=now)
        for code, body in chunks
    ]
    write_markdown(page, title, url, chunks, now)
    conn.execute("DELETE FROM wiki WHERE page = ?", (page,))
    conn.executemany(
        "INSERT INTO wiki(page, page_title, section, body, url, scraped_at) "
        "VALUES (:page, :page_title, :section, :body, :url, :scraped_at)",
        rows,
    )
    conn.commit()
    print(f"  ✓ {page}: «{title}», {len(text)} симв., {len(rows)} чанков")
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", help="Только одна страница, напр. docs/inf-org/family-war")
    parser.add_argument("--stats", action="store_true", help="Показать содержимое БД и выйти")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_FILE)
    init_db(conn)

    if args.stats:
        rows = conn.execute(
            "SELECT page_title, COUNT(*), MAX(scraped_at) FROM wiki GROUP BY page ORDER BY page"
        ).fetchall()
        for name, cnt, ts in rows:
            print(f"  {cnt:>4}  {name}  ({ts})")
        print(f"\nИтого: {sum(r[1] for r in rows)} чанков на {len(rows)} страницах")
        return 0

    pages = [args.page.strip("/")] if args.page else discover_pages()
    print(f"Страниц вики: {len(pages)}\n")
    ok = fail = total = 0
    for page in pages:
        try:
            total += process_page(conn, page)
            ok += 1
        except Exception as e:
            fail += 1
            print(f"  ✗ {page}: {e}")
        time.sleep(0.4)

    pruned = 0
    if not args.page and fail == 0:
        marks = ",".join("?" * len(pages))
        pruned = conn.execute(f"DELETE FROM wiki WHERE page NOT IN ({marks})", pages).rowcount
        conn.commit()

    print(f"\nГотово: {ok} OK, {fail} ошибок, {total} чанков"
          f"{f', удалено устаревших: {pruned}' if pruned else ''}.")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
