#!/usr/bin/env python3
"""Собирает однофайловую версию декодера — для превью-ссылки или офлайна.

    python3 stories/build_preview.py [выходной_файл.html]

Источники те же, что у сайта: story/index.html, story.css, story.js,
content.json. Отдельной копии текста не появляется.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "story"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "story" / "preview.html"


def body_of(html: str) -> str:
    m = re.search(r"<body>(.*?)</body>", html, re.S)
    body = m.group(1) if m else html
    return re.sub(r'\s*<script src="story\.js"></script>\s*', "\n", body)


def main():
    html = (SRC / "index.html").read_text(encoding="utf-8")
    css = (SRC / "story.css").read_text(encoding="utf-8")
    js = (SRC / "story.js").read_text(encoding="utf-8")
    content = json.loads((SRC / "content.json").read_text(encoding="utf-8"))

    title = re.search(r"<title>(.*?)</title>", html, re.S).group(1)

    page = (
        f"<title>{title}</title>\n"
        f"<style>\n{css}\n</style>\n"
        f"{body_of(html)}\n"
        "<script>\n"
        f"window.__CONTENT__ = {json.dumps(content, ensure_ascii=False)};\n"
        "</script>\n"
        f"<script>\n{js}\n</script>\n"
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print(f"{OUT}: {len(page) // 1024} КБ, один файл")


if __name__ == "__main__":
    main()
