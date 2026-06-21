#!/usr/bin/env python3
"""
News TV pipeline
-----------------
1. Забирает свежие новости из RSS-лент (ТАСС, РИА, Интерфакс, Guardian, BBC).
2. Сохраняет статьи в локальную SQLite-базу (pipeline/news.db), чтобы не дублировать
   уже виденные статьи между запусками.
3. Берёт последние N статей и отправляет их на анализ в LLM (Openmodel,
   Anthropic-совместимый API) — модель строит дайджест: ключевые события,
   связи между регионами, паттерны, неожиданные инсайты.
4. Пишет результат в data.json в корне репозитория — именно его читает
   script.js на сайте.

Переменные окружения (задаются как GitHub Actions secrets):
  OPENMODEL_API_KEY   — обязательный, ключ для Openmodel (Anthropic-совместимый)
  OPENMODEL_BASE_URL  — опционально, по умолчанию https://api.openmodel.ai/v1
  OPENMODEL_MODEL     — опционально, по умолчанию deepseek-v4-flash
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import anthropic

# ---------- Пути ----------
ROOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(__file__).resolve().parent / "news.db"
DATA_JSON_PATH = ROOT_DIR / "data.json"

# ---------- Источники ----------
RSS_FEEDS = {
    "ТАСС": "https://tass.ru/rss/v2.xml",
    "РИА Новости": "https://ria.ru/export/rss2/index.xml",
    "Интерфакс": "https://www.interfax.ru/rss.asp",
    "The Guardian World": "https://www.theguardian.com/world/rss",
    "BBC World": "https://feeds.bbci.co.uk/news/world/rss.xml",
}

ARTICLES_PER_SOURCE = 20      # сколько свежих статей тянем за один источник
ANALYZE_COUNT = 30            # сколько последних статей отдаём в LLM на анализ
RECENT_ARTICLES_IN_OUTPUT = 20  # сколько статей показываем в ленте на сайте

# ---------- Openmodel (Anthropic-совместимый API) ----------
OPENMODEL_API_KEY = os.environ.get("OPENMODEL_API_KEY")
OPENMODEL_BASE_URL = os.environ.get("OPENMODEL_BASE_URL", "https://api.openmodel.ai/v1")
OPENMODEL_MODEL = os.environ.get("OPENMODEL_MODEL", "deepseek-v4-flash")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            url TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            published_at TEXT,
            fetched_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def fetch_rss(source_name, url):
    """Скачивает RSS-ленту и возвращает список статей в унифицированном формате."""
    articles = []
    try:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            print(f"  [!] {source_name}: ошибка парсинга ({feed.bozo_exception})", file=sys.stderr)
            return articles

        for entry in feed.entries[:ARTICLES_PER_SOURCE]:
            link = entry.get("link", "").strip()
            title = entry.get("title", "").strip()
            if not link or not title:
                continue

            published = None
            if entry.get("published_parsed"):
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
            elif entry.get("updated_parsed"):
                published = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc).isoformat()

            articles.append({
                "source": source_name,
                "title": title,
                "url": link,
                "published_at": published or datetime.now(timezone.utc).isoformat(),
            })
    except Exception as e:
        print(f"  [!] {source_name}: исключение при загрузке ({e})", file=sys.stderr)

    return articles


def save_new_articles(conn, articles):
    """Сохраняет новые статьи в базу (по url), возвращает кол-во реально новых."""
    now = datetime.now(timezone.utc).isoformat()
    new_count = 0
    for a in articles:
        try:
            conn.execute(
                "INSERT INTO articles (url, source, title, published_at, fetched_at) VALUES (?, ?, ?, ?, ?)",
                (a["url"], a["source"], a["title"], a["published_at"], now),
            )
            new_count += 1
        except sqlite3.IntegrityError:
            pass  # уже есть — пропускаем
    conn.commit()
    return new_count


def get_recent_articles(conn, limit):
    cur = conn.execute(
        "SELECT source, title, url, published_at FROM articles "
        "ORDER BY published_at DESC LIMIT ?",
        (limit,),
    )
    rows = cur.fetchall()
    return [
        {"source": r[0], "title": r[1], "url": r[2], "published_at": r[3]}
        for r in rows
    ]


def build_analysis_prompt(articles):
    lines = []
    for a in articles:
        lines.append(f"- [{a['source']}] {a['title']}")
    articles_block = "\n".join(lines)

    return f"""Ты — редактор-аналитик новостного дайджеста. Вот список последних новостных заголовков из разных источников (Россия и мир):

{articles_block}

Составь аналитический обзор на русском языке строго в следующей markdown-структуре (используй заголовки ровно такого уровня и формулировки разделов):

### 1. КЛЮЧЕВЫЕ СОБЫТИЯ

Сгруппируй главные события по темам/регионам, кратко опиши суть каждого.

### 2. СВЯЗИ МЕЖДУ РЕГИОНАМИ

Найди и опиши причинно-следственные или тематические связи между разными новостями.

### 3. ПАТТЕРНЫ И ТРЕНДЫ

Выяви повторяющиеся паттерны, тренды, географические или тематические закономерности.

### 4. НЕОЖИДАННЫЕ ИНСАЙТЫ

Отметь неочевидные совпадения, контрасты или наблюдения, которые не лежат на поверхности.

Пиши содержательно, избегай воды, используй маркированные списки и выделение **жирным** для ключевых тезисов."""


def call_llm(articles):
    if not OPENMODEL_API_KEY:
        raise RuntimeError("OPENMODEL_API_KEY не задан в переменных окружения")

    client = anthropic.Anthropic(
        api_key=OPENMODEL_API_KEY,
        base_url=OPENMODEL_BASE_URL,
    )

    prompt = build_analysis_prompt(articles)

    message = client.messages.create(
        model=OPENMODEL_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    # Некоторые модели (например, с режимом "thinking") возвращают несколько
    # блоков контента, где первый — ThinkingBlock (рассуждения), а не текст.
    # Поэтому ищем именно текстовый блок, а не берём content[0] вслепую.
    for block in message.content:
        if getattr(block, "type", None) == "text":
            return block.text

    # На случай неожиданного формата ответа — лучше явная ошибка, чем тихий сбой.
    raise RuntimeError(f"В ответе LLM не найден текстовый блок: {message.content!r}")


def main():
    print(f"=== News TV pipeline run: {datetime.now(timezone.utc).isoformat()} ===")

    conn = init_db()

    # 1. Сбор новостей по всем RSS
    all_fetched = []
    by_source_counts = {}
    for source_name, url in RSS_FEEDS.items():
        print(f"Fetching {source_name} ...")
        articles = fetch_rss(source_name, url)
        print(f"  -> {len(articles)} статей получено")
        all_fetched.extend(articles)
        by_source_counts[source_name] = len(articles)

    new_count = save_new_articles(conn, all_fetched)
    print(f"Новых статей сохранено в базу: {new_count}")

    total_articles = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]

    # 2. Выбираем статьи для анализа LLM
    articles_for_analysis = get_recent_articles(conn, ANALYZE_COUNT)

    analysis_text = ""
    digests_count = 0
    if articles_for_analysis:
        try:
            print(f"Запрашиваем анализ у LLM ({OPENMODEL_MODEL}) по {len(articles_for_analysis)} статьям...")
            analysis_text = call_llm(articles_for_analysis)
            digests_count = 1
            print("Анализ получен.")
        except Exception as e:
            print(f"  [!] Ошибка вызова LLM: {e}", file=sys.stderr)
            # Не валим весь пайплайн — просто оставим data.json без нового анализа,
            # подставив предыдущий, если он есть.
            if DATA_JSON_PATH.exists():
                try:
                    prev = json.loads(DATA_JSON_PATH.read_text(encoding="utf-8"))
                    analysis_text = prev.get("analysis", {}).get("text", "")
                    digests_count = prev.get("stats", {}).get("digests", 0)
                except Exception:
                    pass

    # 3. Лента статей для сайта (последние N по дате публикации)
    recent_for_output = get_recent_articles(conn, RECENT_ARTICLES_IN_OUTPUT)

    # 4. Собираем data.json
    now_iso_minutes = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    output = {
        "updated_at": now_iso_minutes,
        "stats": {
            "total_articles": total_articles,
            "analyzed": len(articles_for_analysis),
            "digests": digests_count,
            "by_source": by_source_counts,
        },
        "analysis": {
            "text": analysis_text,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        },
        "recent_articles": recent_for_output,
    }

    DATA_JSON_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"data.json обновлён: {DATA_JSON_PATH}")

    conn.close()


if __name__ == "__main__":
    main()
