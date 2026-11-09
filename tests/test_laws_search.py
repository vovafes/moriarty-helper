"""Assistant retrieval: article numbers, document aliases, labels. Run: python -m tests.test_laws_search"""
import asyncio
import os
import sqlite3
import tempfile

os.chdir(tempfile.mkdtemp())  # laws.sqlite is resolved relative to cwd

import laws_module as L  # noqa: E402

conn = sqlite3.connect(L.DB_FILE)
conn.executescript("""
CREATE TABLE laws (id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id INT, law_name TEXT,
    article_code TEXT, body TEXT, url TEXT, scraped_at TEXT);
CREATE VIRTUAL TABLE laws_fts USING fts5(law_name, article_code, body, content='laws', content_rowid='id',
    tokenize='unicode61 remove_diacritics 0');
CREATE TRIGGER laws_ai AFTER INSERT ON laws BEGIN
  INSERT INTO laws_fts(rowid, law_name, article_code, body) VALUES (new.id, new.law_name, new.article_code, new.body);
END;
""")
rows = [
    ("Дорожный кодекс (ПДД)", "Статья 17.1", "Статья 17.1 Запрещено ездить на самокатах по дорогам."),
    ("Правила и обязанности лидеров", "17.1", "17.1 Лидеру доступна замена транспорта."),
    ("Уголовно-административный кодекс", "12.8", "12.8 Незаконное хранение оружия. Штраф $5000."),
    ("Уголовно-административный кодекс", "3.10 ч.1", "3.10 Не является преступлением ... часть первая"),
    ("Уголовно-административный кодекс", "3.10 ч.2", "продолжение статьи 3.10"),
    ("Процессуальный кодекс", "1.1", "1.1 Общие положения процесса."),
]
conn.executemany("INSERT INTO laws(thread_id, law_name, article_code, body, url, scraped_at) VALUES (1,?,?,?, 'u', 't')", rows)
conn.commit()
conn.close()


def search(q):
    return asyncio.run(L.search_laws(q))


def names(res):
    return [(r["law_name"], r["article_code"]) for r in res]


# bare number: every document that has it, no FTS noise
assert {n for n, _ in names(search("17.1"))} == {"Дорожный кодекс (ПДД)", "Правила и обязанности лидеров"}
# number + alias: only that document, and a missing article is stated, not guessed
r = search("что за статья 17.1 УАК")
assert len(r) == 1 and "нет пункта 17.1" in r[0]["body"], r
# alias + number finds all parts of a long article
assert [c for _, c in names(search("3.10 уак"))] == ["3.10 ч.1", "3.10 ч.2"]
# alias without number narrows the FTS to the document
assert names(search("хранение оружия по УАК"))[0] == ("Уголовно-административный кодекс", "12.8")
# ambiguous short alias ("пк") without a number is just a word, not a document filter
assert L._search_sync("оружие пк", 5)[0]["law_name"] == "Уголовно-административный кодекс"  # not forced into ПК
# labels
assert L._ref_label("Уголовно-административный кодекс", "3.10 ч.2") == "ст. 3.10 УАК (продолжение, часть 2)"
assert L._ref_label("Дорожный кодекс (ПДД)", "Статья 31") == "ст. 31 ДК"
assert L._ref_label("Правила и обязанности лидеров", "17.1") == "п. 17.1"
assert L._ref_label("Закон «О СМИ»", "(преамбула)") == "Закон «О СМИ» — (преамбула)"
print("laws search tests OK")
