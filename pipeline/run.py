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
  GDELT_CLOUD_API_KEY — опционально, ключ gdelt_sk_... для GDELT Cloud (платный)
"""

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
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
    # Россия
    "ТАСС": "https://tass.ru/rss/v2.xml",
    "РИА Новости": "https://ria.ru/export/rss2/index.xml",
    "Интерфакс": "https://www.interfax.ru/rss.asp",
    # Великобритания / международные англоязычные
    "The Guardian World": "https://www.theguardian.com/world/rss",
    "BBC World": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "NYT World": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "NPR World": "https://feeds.npr.org/1004/rss.xml",
    # Ближний Восток
    "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    # Европа (континентальная)
    "France24": "https://www.france24.com/en/rss",
    "DW (Германия)": "https://rss.dw.com/rdf/rss-en-all",
    # Азия
    "Times of India": "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
    # Африка
    "AllAfrica": "https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf",
}

ARTICLES_PER_SOURCE = 20      # сколько свежих статей тянем за один RSS-источник
ANALYZE_COUNT = 30            # сколько последних статей отдаём в LLM на анализ
RECENT_ARTICLES_IN_OUTPUT = 20  # сколько статей показываем в ленте на сайте
FETCH_TIMEOUT_SECONDS = 15    # таймаут на скачивание одной RSS-ленты

# GDELT DOC 2.0 API: глобальный индекс новостей на 100+ языках, обновляется
# каждые 15 минут, без ключа. Используем как ДОПОЛНИТЕЛЬНЫЙ источник поверх RSS —
# у него бывает нестабильный rate-limit (изредка отдаёт 429), поэтому ошибки
# по нему не должны ронять весь пайплайн.
GDELT_BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_QUERIES = {
    "GDELT: Россия/СНГ (rus)": "sourcelang:rus",
    "GDELT: Китай (zho)": "sourcelang:zho",
    "GDELT: Латинская Америка (spa)": "sourcelang:spa",
    "GDELT: Ближний Восток (ara)": "sourcelang:ara",
}
GDELT_TIMESPAN = "2h"          # окно поиска для каждого запуска (с запасом на час между запусками)
GDELT_MAX_RECORDS = 20         # статей на один GDELT-запрос
GDELT_TIMEOUT_SECONDS = 20
GDELT_MAX_RETRIES = 3          # попыток при 429, прежде чем сдаться
GDELT_RETRY_DELAY_SECONDS = 10 # базовая пауза перед повтором (умножается на номер попытки)
GDELT_DELAY_BETWEEN_QUERIES = 5  # пауза между отдельными GDELT-запросами, чтобы не словить 429

# Некоторые сайты (France24, DW, Times of India и др.) блокируют запросы без
# "браузерного" User-Agent или отдают иной ответ ботам. Подставляем его явно.
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# GDELT Cloud (gdeltcloud.com) — сторонний платный сервис поверх данных GDELT
# Project: кластеризованные "Stories" (истории) со ссылками на статьи. Это
# НЕ официальный бесплатный GDELT Project API, а отдельный аккаунт-сервис.
# Используется только если задан GDELT_CLOUD_API_KEY — если ключа нет,
# источник просто молча пропускается, не ломая пайплайн.
GDELT_CLOUD_API_KEY = os.environ.get("GDELT_CLOUD_API_KEY")
GDELT_CLOUD_BASE_URL = "https://gdeltcloud.com/api/v2/stories"
GDELT_CLOUD_QUERIES = {
    "GDELT Cloud: Европа": {"continent": "Europe"},
    "GDELT Cloud: Азия": {"continent": "Asia"},
    "GDELT Cloud: Ближний Восток": {"region": "Middle East"},
    "GDELT Cloud: Африка": {"continent": "Africa"},
    "GDELT Cloud: Северная Америка": {"continent": "North America"},
}
GDELT_CLOUD_LIMIT = 20         # историй на один запрос (максимум по API — 100)
GDELT_CLOUD_TIMEOUT_SECONDS = 20

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
    """Скачивает RSS-ленту и возвращает список статей в унифицированном формате.

    Сначала пробуем скачать вручную через urllib с таймаутом и "браузерным"
    User-Agent (некоторые сайты блокируют запросы без него или зависают).
    Если это не получилось — пробуем напрямую через feedparser как запасной
    вариант. Падение одного источника не должно останавливать весь пайплайн.
    """
    articles = []
    raw_bytes = None

    try:
        request = urllib.request.Request(url, headers=HTTP_HEADERS)
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as resp:
            raw_bytes = resp.read()
    except Exception as e:
        print(f"  [!] {source_name}: не удалось скачать напрямую ({e}), пробуем через feedparser...", file=sys.stderr)

    try:
        feed = feedparser.parse(raw_bytes) if raw_bytes is not None else feedparser.parse(url)

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
        print(f"  [!] {source_name}: исключение при разборе ({e})", file=sys.stderr)

    return articles


def fetch_gdelt(source_name, query):
    """Запрашивает GDELT DOC 2.0 API и возвращает статьи в унифицированном формате.

    У бесплатного GDELT DOC 2.0 API нет официальной документированной квоты —
    он может изредка отдавать 429 без предупреждения, особенно при частых
    запросах подряд (например, с общих IP-диапазонов GitHub Actions). При 429
    делаем до GDELT_MAX_RETRIES попыток с паузой, прежде чем сдаться. Любая
    другая ошибка, как и исчерпанные попытки, просто пропускает источник —
    не должны прерывать остальной пайплайн.
    """
    articles = []
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": str(GDELT_MAX_RECORDS),
        "timespan": GDELT_TIMESPAN,
        "format": "json",
        "sort": "datedesc",
    }
    url = GDELT_BASE_URL + "?" + urllib.parse.urlencode(params)

    data = None
    for attempt in range(1, GDELT_MAX_RETRIES + 1):
        try:
            request = urllib.request.Request(url, headers=HTTP_HEADERS)
            with urllib.request.urlopen(request, timeout=GDELT_TIMEOUT_SECONDS) as resp:
                raw = resp.read()
            data = json.loads(raw)
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < GDELT_MAX_RETRIES:
                wait = GDELT_RETRY_DELAY_SECONDS * attempt
                print(f"  [!] {source_name}: 429 (попытка {attempt}/{GDELT_MAX_RETRIES}), жду {wait}с...", file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"  [!] {source_name}: GDELT HTTP ошибка {e.code} — пропускаем", file=sys.stderr)
            return articles
        except Exception as e:
            print(f"  [!] {source_name}: GDELT недоступен сейчас ({e}) — пропускаем", file=sys.stderr)
            return articles

    if data is None:
        return articles

    for item in data.get("articles", []):
        link = (item.get("url") or "").strip()
        title = (item.get("title") or "").strip()
        if not link or not title:
            continue

        # GDELT отдаёт seendate в формате YYYYMMDDTHHMMSSZ
        published = None
        seendate = item.get("seendate")
        if seendate:
            try:
                published = datetime.strptime(seendate, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                pass

        domain = item.get("domain", "")
        articles.append({
            "source": f"{source_name} ({domain})" if domain else source_name,
            "title": title,
            "url": link,
            "published_at": published or datetime.now(timezone.utc).isoformat(),
        })

    return articles


def fetch_gdelt_cloud(source_name, query_params):
    """Запрашивает GDELT Cloud (gdeltcloud.com) /api/v2/stories — сторонний платный
    сервис поверх данных GDELT Project, требует API-ключ (Authorization: Bearer).

    Берём top_articles из каждой Story как отдельные статьи (заголовок + ссылка).
    Любая ошибка (нет ключа, истёк, лимит исчерпан, временный сбой) просто
    пропускает источник — не должна ронять весь пайплайн.
    """
    articles = []

    if not GDELT_CLOUD_API_KEY:
        return articles  # источник отключён, ключ не задан — это нормально

    params = dict(query_params)
    params["limit"] = str(GDELT_CLOUD_LIMIT)
    params["sort"] = "recent"  # для новостной ленты важна свежесть, а не "значимость"
    url = GDELT_CLOUD_BASE_URL + "?" + urllib.parse.urlencode(params)

    headers = dict(HTTP_HEADERS)
    headers["Authorization"] = f"Bearer {GDELT_CLOUD_API_KEY}"

    try:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=GDELT_CLOUD_TIMEOUT_SECONDS) as resp:
            raw = resp.read()
        data = json.loads(raw)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print(f"  [!] {source_name}: GDELT Cloud — неверный или отозванный API-ключ (401)", file=sys.stderr)
        elif e.code == 429:
            retry_after = e.headers.get("Retry-After", "?") if e.headers else "?"
            print(f"  [!] {source_name}: GDELT Cloud — превышен лимит запросов (429, Retry-After={retry_after}с)", file=sys.stderr)
        else:
            print(f"  [!] {source_name}: GDELT Cloud HTTP ошибка {e.code} — пропускаем", file=sys.stderr)
        return articles
    except Exception as e:
        print(f"  [!] {source_name}: GDELT Cloud недоступен сейчас ({e}) — пропускаем", file=sys.stderr)
        return articles

    if not data.get("success"):
        print(f"  [!] {source_name}: GDELT Cloud вернул success=false — пропускаем", file=sys.stderr)
        return articles

    for story in data.get("data", []):
        story_date = story.get("story_date")
        published = None
        if story_date:
            try:
                published = datetime.strptime(story_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                pass

        # Каждая Story может содержать до 3 статей-первоисточников — берём их все,
        # это даёт больше реальных заголовков, а не только заголовок кластера.
        for art in story.get("top_articles", []) or []:
            link = (art.get("url") or "").strip()
            title = (art.get("title") or "").strip()
            if not link or not title:
                continue
            domain = art.get("domain", "")
            articles.append({
                "source": f"{source_name} ({domain})" if domain else source_name,
                "title": title,
                "url": link,
                "published_at": published or datetime.now(timezone.utc).isoformat(),
            })

    return articles


def save_new_articles(conn, articles):
    """Сохраняет новые статьи в базу (по url), возвращает кол-во реально новых."""
    now = datetime.now(timezone.utc).isoformat()
    new_count = 0
    for a in articles:
        try:
            conn.execu
