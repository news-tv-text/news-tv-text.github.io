#!/usr/bin/env python3
"""Собирает story/content.json из рукописи (.md) и quest-метаданных (meta.json).

Рукопись — единственный источник текста: и сайт, и телеграм-бот читают
получившийся content.json, так что править прозу нужно только в .md.

    python3 stories/build_content.py
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANUSCRIPT = ROOT / "stories" / "lampy-cherez-odnu.md"
META = ROOT / "stories" / "meta.json"
OUT = ROOT / "story" / "content.json"


def parse_manuscript(text):
    """Режет рукопись по заголовкам '### ' на главы с абзацами."""
    chapters = []
    current = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("### "):
            current = {"title": line[4:].strip(), "paras": []}
            chapters.append(current)
        elif current is not None and line and not line.startswith("---"):
            current["paras"].append(line.strip())
    return chapters


def split_dialogue(paras):
    """Реплики (— ...) помечаются отдельным типом: на экране они идут другим цветом."""
    out = []
    for p in paras:
        kind = "line" if p.startswith("—") else "para"
        out.append({"kind": kind, "text": p})
    return out


def main():
    meta = json.loads(META.read_text(encoding="utf-8"))
    chapters = parse_manuscript(MANUSCRIPT.read_text(encoding="utf-8"))

    chapter_meta = meta.get("chapter_meta", [])
    if len(chapter_meta) != len(chapters):
        raise SystemExit(
            f"chapter_meta описывает {len(chapter_meta)} глав, "
            f"а в рукописи их {len(chapters)} — поправьте meta.json"
        )

    first = meta["first_page"]
    built = []
    for i, ch in enumerate(chapters):
        cm = chapter_meta[i]
        built.append(
            {
                "page": str(first + i),
                "n": i + 1,
                "title": ch["title"],
                "anchor": cm.get("anchor"),
                "hint": cm.get("hint"),
                "blocks": split_dialogue(ch["paras"]),
                "night": bool(re.match(r"^15 ноября, 0[0-3]", ch["title"])),
            }
        )

    content = {
        "title": meta["title"],
        "channel": meta["channel"],
        "subtitle": meta["subtitle"],
        "boot": meta["boot"],
        "help": meta["help"],
        "first_page": str(first),
        "final_page": str(meta["final_page"]),
        "keys_required": meta["keys_required"],
        "chapters": built,
        "pages": meta["pages"],
        "ticker": meta["ticker"],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{OUT.relative_to(ROOT)}: {len(built)} глав, {len(meta['pages'])} служебных страниц")


if __name__ == "__main__":
    main()
