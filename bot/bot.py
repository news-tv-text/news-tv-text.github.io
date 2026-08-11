#!/usr/bin/env python3
"""ТВ-ЛУНЕВО // ночной телетекст — телеграм-бот к рассказу «Лампы через одну».

Бот работает как второй декодер: те же страницы, те же трёхзначные номера,
что и на сайте. Отличие в том, что он умеет писать сам — ночью, в эфирное
время бюро находок.

Запуск:
    export BOT_TOKEN="123456:AA..."
    export SITE_URL="https://news-tv-text.github.io/story/"   # необязательно
    python3 bot/bot.py
"""

import asyncio
import json
import logging
import os
import random
from datetime import datetime, time as dtime, timezone, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "story" / "content.json"
STATE_FILE = Path(os.getenv("STATE_FILE", ROOT / "bot" / "state.json"))
SITE_URL = os.getenv("SITE_URL", "")
MSK = timezone(timedelta(hours=3))

# Ночной эфир: бюро находок работает в 00:40
NIGHT_FROM, NIGHT_TO = dtime(0, 0), dtime(4, 0)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lunevo")

C = json.loads(CONTENT.read_text(encoding="utf-8"))
CHAPTERS = {ch["page"]: ch for ch in C["chapters"]}
ORDER = [ch["page"] for ch in C["chapters"]]


# --------------------------------------------------------------------------- state


class Store:
    """Прогресс читателей. Плоский JSON — читателей тут не миллионы."""

    def __init__(self, path: Path):
        self.path = path
        self.data = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("state.json повреждён, начинаем с чистого")
        self._dirty = False

    def user(self, uid: int) -> dict:
        u = self.data.setdefault(
            str(uid), {"read": [], "visited": [], "night": True, "last_night": 0}
        )
        return u

    def touch(self):
        self._dirty = True

    async def autosave(self):
        while True:
            await asyncio.sleep(20)
            if self._dirty:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(
                    json.dumps(self.data, ensure_ascii=False), encoding="utf-8"
                )
                self._dirty = False


store = Store(STATE_FILE)


# --------------------------------------------------------------------------- render


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def keyboard(page: str, u: dict) -> InlineKeyboardMarkup:
    rows = []
    if page in CHAPTERS:
        i = ORDER.index(page)
        nav = []
        if i > 0:
            nav.append(InlineKeyboardButton(text="◀", callback_data=f"p:{ORDER[i-1]}"))
        if i + 1 < len(ORDER):
            nav.append(
                InlineKeyboardButton(text="ДАЛЬШЕ ▶", callback_data=f"p:{ORDER[i+1]}")
            )
        if nav:
            rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton(text="100 оглавление", callback_data="p:100"),
            InlineKeyboardButton(text="300 бюро находок", callback_data="p:300"),
        ]
    )
    if SITE_URL:
        rows.append(
            [InlineKeyboardButton(text="ОТКРЫТЬ ДЕКОДЕР", url=f"{SITE_URL}#{page}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def chapter_chunks(ch: dict) -> list[str]:
    """Глава уезжает в чат в два-три захода: так читается медленнее и ровнее."""
    head = f"<b>{esc(ch['title'])}</b>\n<code>СТР. {ch['page']}</code>"
    parts, buf = [], [head]
    size = len(head)
    for b in ch["blocks"]:
        text = esc(b["text"])
        if b["kind"] == "line":
            text = f"<i>{text}</i>"
        if size + len(text) > 900 and len(buf) > 1:
            parts.append("\n\n".join(buf))
            buf, size = [], 0
        buf.append(text)
        size += len(text) + 2
    if buf:
        parts.append("\n\n".join(buf))
    return parts


def index_text(u: dict) -> str:
    lines = ["<b>ОГЛАВЛЕНИЕ</b>", "<code>СТР. 100</code>", ""]
    for ch in C["chapters"]:
        mark = "·" if ch["page"] in u["read"] else "▪"
        lines.append(f"{mark} <code>{ch['page']}</code>  {esc(ch['title'])}")
    lines += ["", "Наберите три цифры — страница откроется.", esc(C["help"][2]), esc(C["help"][3])]
    return "\n".join(lines)


def inventory_text(u: dict) -> str:
    found = [
        f"▪ <code>{code}</code> — {esc(p['found'])}"
        for code, p in C["pages"].items()
        if p.get("found") and code in u["visited"]
    ]
    lines = ["<b>БЮРО НАХОДОК</b>", "<code>ПРИЁМ И ВЫДАЧА 00:40</code>", ""]
    lines += found or ["· ничего не сдано"]
    lines += [
        "",
        f"<code>ПРОЧИТАНО ГЛАВ: {len(u['read'])} ИЗ {len(ORDER)}</code>",
        f"<code>НАЙДЕНО НОМЕРОВ: {len(found)} ИЗ {len(C['keys_required'])}</code>",
    ]
    return "\n".join(lines)


def static_page_text(code: str, p: dict) -> str:
    body = "\n\n".join(esc(t) for t in p["paras"])
    return f"<b>{esc(p['title'])}</b>\n<code>СТР. {code}</code>\n\n{body}"


def locked_text(code: str, u: dict) -> str:
    if code != C["final_page"]:
        return (
            f"<b>ОШИБКА ПРИЁМА</b>\n<code>СТР. {code}</code>\n\n"
            "Такого номера в сетке нет. Проверьте по оглавлению — страница 100."
        )
    unread = len(ORDER) - len(u["read"])
    missing = [k for k in C["keys_required"] if k not in u["visited"]]
    out = [
        f"<b>ОШИБКА ПРИЁМА</b>\n<code>СТР. {code}</code>",
        "",
        "Декодер её знает, но не отдаёт. Чего-то не хватает.",
    ]
    if unread:
        out.append(f"<code>НЕ ПРОЧИТАНО ГЛАВ: {unread}</code>")
    if missing:
        out.append(f"<code>НЕ СДАНО В БЮРО НАХОДОК: {len(missing)}</code>")
        out.append("Числа лежат в тексте. Их не прячут — их просто произносят вслух.")
    return "\n".join(out)


# --------------------------------------------------------------------------- bot

bot = Bot(
    token=os.environ["BOT_TOKEN"],
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()


async def send_slow(chat_id: int, parts: list[str], markup=None):
    for i, part in enumerate(parts):
        await bot.send_chat_action(chat_id, ChatAction.TYPING)
        await asyncio.sleep(min(1.6, 0.4 + len(part) / 900))
        await bot.send_message(
            chat_id, part, reply_markup=markup if i == len(parts) - 1 else None
        )


async def open_page(chat_id: int, uid: int, code: str):
    u = store.user(uid)

    if code == "100":
        await bot.send_message(chat_id, index_text(u), reply_markup=keyboard(code, u))
        return
    if code == "300":
        await bot.send_message(chat_id, inventory_text(u), reply_markup=keyboard(code, u))
        return

    if code in CHAPTERS:
        ch = CHAPTERS[code]
        if code not in u["visited"]:
            u["visited"].append(code)
        if code not in u["read"]:
            u["read"].append(code)
        store.touch()
        await send_slow(chat_id, chapter_chunks(ch), keyboard(code, u))
        if ch.get("hint"):
            await bot.send_message(chat_id, f"<code>⌁ {esc(ch['hint'])}</code>")
        return

    p = C["pages"].get(code)
    if not p:
        await bot.send_message(chat_id, locked_text(code, u))
        return

    if p["kind"] == "final":
        ok = len(u["read"]) >= len(ORDER) and all(
            k in u["visited"] for k in C["keys_required"]
        )
        if not ok:
            await bot.send_message(chat_id, locked_text(code, u))
            return

    if code not in u["visited"]:
        u["visited"].append(code)
        store.touch()
    await bot.send_message(chat_id, static_page_text(code, p), reply_markup=keyboard(code, u))


@dp.message(CommandStart())
async def start(m: Message):
    u = store.user(m.from_user.id)
    store.touch()
    boot = "\n".join(f"<code>{esc(l)}</code>" for l in C["boot"])
    await m.answer(
        f"{boot}\n\n<b>{esc(C['title'])}</b>\n\n"
        "Наберите три цифры — страница откроется. "
        f"Начало — <code>{C['first_page']}</code>.\n"
        "<code>/noch</code> — ночной эфир (бот пишет сам после полуночи).",
        reply_markup=keyboard(C["first_page"], u),
    )


@dp.message(Command("dalshe"))
async def cont(m: Message):
    u = store.user(m.from_user.id)
    nxt = next((p for p in ORDER if p not in u["read"]), C["final_page"])
    await open_page(m.chat.id, m.from_user.id, nxt)


@dp.message(Command("noch"))
async def night_toggle(m: Message):
    u = store.user(m.from_user.id)
    u["night"] = not u["night"]
    store.touch()
    await m.answer(
        "<code>НОЧНОЙ ЭФИР ВКЛЮЧЁН. БЮРО НАХОДОК РАБОТАЕТ 00:40.</code>"
        if u["night"]
        else "<code>НОЧНОЙ ЭФИР ОТКЛЮЧЁН.</code>"
    )


@dp.message(Command("stroka"))
async def one_line(m: Message):
    pool = C["ticker"]["normal"]
    await m.answer(f"<code>••• {esc(random.choice(pool))}</code>")


@dp.message(F.text.regexp(r"^\s*\d{3}\s*$"))
async def dial(m: Message):
    await open_page(m.chat.id, m.from_user.id, m.text.strip())


@dp.message()
async def fallback(m: Message):
    await m.answer(
        "<code>ДЕКОДЕР ПРИНИМАЕТ ТОЛЬКО НОМЕРА СТРАНИЦ.</code>\n"
        "Три цифры. Оглавление — <code>100</code>."
    )


@dp.callback_query(F.data.startswith("p:"))
async def on_button(cq: CallbackQuery):
    await cq.answer()
    await open_page(cq.message.chat.id, cq.from_user.id, cq.data[2:])


# --------------------------------------------------------------------------- ночной эфир


async def night_broadcast():
    """После полуночи бот сам присылает строку, которой никто не набирал."""
    while True:
        await asyncio.sleep(300)
        now = datetime.now(MSK)
        if not (NIGHT_FROM <= now.time() <= NIGHT_TO):
            continue
        stamp = int(now.timestamp())
        for uid, u in list(store.data.items()):
            if not u.get("night") or not u.get("read"):
                continue
            if stamp - u.get("last_night", 0) < 20 * 3600:
                continue
            if random.random() > 0.35:
                continue
            u["last_night"] = stamp
            store.touch()
            line = random.choice(C["ticker"]["intrusions"])
            try:
                await bot.send_message(int(uid), f"<code>••• {esc(line)}</code>")
            except Exception as e:  # заблокировал бота, удалил чат и т.п.
                log.info("не доставлено %s: %s", uid, e)


async def main():
    asyncio.create_task(store.autosave())
    asyncio.create_task(night_broadcast())
    log.info("эфир начат: %d глав, %d служебных страниц", len(ORDER), len(C["pages"]))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
