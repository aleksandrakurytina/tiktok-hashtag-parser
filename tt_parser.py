#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tt_parser.py — сбор публичной аналитики видео TikTok по хештегам.

Что делает:
  1) Открывает страницу хештега (https://www.tiktok.com/tag/<тег>), аккуратно скроллит,
     собирает ссылки на видео.
  2) Опционально заходит на каждое видео и достаёт метрики: автор, дата, длительность,
     просмотры, лайки, комменты, репосты, сохранения, музыка, хештеги.
  3) Кладёт результат в CSV / JSON / XLSX + лист со сводкой по нише.

ВАЖНО: только чтение публичных данных. Никаких комментариев, лайков и подписок —
это спам и нарушение правил площадки.

Примеры:
  python tt_parser.py --tags работа,заработок,удаленка --limit 40 --out out
  python tt_parser.py --tags заработок --no-details --format csv
  python tt_parser.py --tags заработок --storage-state session.json     # под своей сессией
  python tt_parser.py --save-login session.json                         # один раз залогиниться
  python tt_parser.py --demo --tags работа,заработок --limit 6          # прогон на тестовых данных
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import random
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from bs4 import BeautifulSoup

LOG = logging.getLogger("tt")

VIDEO_RE = re.compile(r"/@([A-Za-z0-9_.\-]+)/video/(\d+)")
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


# ─────────────────────────────── модель строки ───────────────────────────────
@dataclass
class VideoRow:
    tag: str = ""
    video_id: str = ""
    url: str = ""
    author: str = ""
    nickname: str = ""
    created_at: str = ""
    duration_sec: int = 0
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    engagement_rate: float = 0.0
    hashtags: str = ""
    music: str = ""
    description: str = ""
    collected_at: str = ""

    COLUMNS = [
        "tag", "video_id", "url", "author", "nickname", "created_at", "duration_sec",
        "views", "likes", "comments", "shares", "saves", "engagement_rate",
        "hashtags", "music", "description", "collected_at",
    ]


# ─────────────────────────── утилиты ───────────────────────────
def sleep_jitter(base: float, spread: float = 0.8) -> None:
    """Не дёргаем сайт как бот: случайная пауза вокруг base секунд."""
    time.sleep(max(0.0, random.uniform(base - spread / 2, base + spread / 2)))


def to_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().lower().replace(" ", "").replace(" ", "")
    mult = 1
    for suffix, factor in (("k", 1_000), ("m", 1_000_000), ("b", 1_000_000_000),
                           ("тыс", 1_000), ("млн", 1_000_000)):
        if text.endswith(suffix):
            mult, text = factor, text[: -len(suffix)]
            break
    text = text.replace(",", ".")
    try:
        return int(float(text) * mult)
    except ValueError:
        digits = re.sub(r"[^\d]", "", text)
        return int(digits) if digits else 0


def ts_to_iso(ts: Any) -> str:
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return ""
    if ts > 10 ** 12:
        ts //= 1000
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def normalize_tag(raw: str) -> str:
    return raw.strip().lstrip("#").strip().lower()


# ─────────────────────── разбор JSON из страницы ───────────────────────
def _iter_dicts(node: Any):
    """Обходим всё дерево JSON — структура у TikTok меняется, поэтому ищем по форме."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_dicts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_dicts(value)


def looks_like_video(node: dict) -> bool:
    """Похоже ли это на объект видео: есть метрики + автор."""
    if "stats" not in node or not isinstance(node["stats"], dict):
        return False
    if not ({"id", "author", "authorInfo", "authorinfo"} & set(node.keys())):
        return False
    return bool({"playCount", "diggCount", "commentCount"} & set(node["stats"].keys()))


def extract_embedded_json(html: str) -> list[dict]:
    """Достаём объекты видео из __UNIVERSAL_DATA_FOR_REHYDRATION__ / SIGI_STATE."""
    soup = BeautifulSoup(html, "html.parser")
    blobs: list[str] = []

    for script in soup.find_all("script"):
        script_id = (script.get("id") or "").lower()
        text = script.string or script.get_text() or ""
        if not text:
            continue
        if any(marker in script_id for marker in
               ("universal_data", "__next_f", "sigi", "frontdoor")) or \
           any(marker in text[:4000] for marker in
               ("__UNIVERSAL_DATA_FOR_REHYDRATION__", "SIGI_STATE", "ItemModule")):
            blobs.append(text)

    if not blobs:  # на всякий случай — весь документ
        blobs.append(html)

    results: list[dict] = []
    seen: set[str] = set()
    for blob in blobs:
        payload = _json_from_blob(blob)
        if payload is None:
            continue
        for node in _iter_dicts(payload):
            if not looks_like_video(node):
                continue
            row = video_dict_to_row(node)
            if row.video_id and row.video_id not in seen:
                seen.add(row.video_id)
                results.append(row)
    return results


def _json_from_blob(text: str) -> Any | None:
    """Выдираем JSON из скрипта: либо целиком, либо по первой подходящей скобке."""
    text = text.strip()
    if not text:
        return None
    # Типичный вид: window['SIGI_STATE'] = {...};  /  <script id=... type="application/json">{...}
    m = re.search(r"^\s*(?:window\.[A-Za-z0-9_$]+\s*=|var\s+[A-Za-z0-9_$]+\s*=)?\s*([\{\[])", text)
    if m:
        candidate = text[m.start(1):]
    else:
        start = min([i for i in (text.find("{"), text.find("[")) if i != -1], default=-1)
        if start == -1:
            return None
        candidate = text[start:]
    candidate = candidate.rstrip().rstrip(";")
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(candidate)
        return obj
    except ValueError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = candidate.find(opener)
        end = candidate.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start:end + 1])
            except ValueError:
                continue
    return None


def video_dict_to_row(node: dict) -> VideoRow:
    stats = node.get("stats") or node.get("statsV2") or {}
    author = node.get("author") or node.get("authorInfo") or node.get("authorinfo") or {}
    if isinstance(author, str):
        author = {"uniqueId": author}
    music = node.get("music") or {}
    if isinstance(music, str):
        music = {"title": music}

    video_id = str(node.get("id") or node.get("video", {}).get("id") or "").strip()
    handle = str(author.get("uniqueId") or author.get("id") or "").strip().lstrip("@")

    desc = str(node.get("desc") or node.get("description") or node.get("title") or "")
    tags = [t.get("title" if "title" in t else "name", "")
            for t in (node.get("challenges") or node.get("textExtra") or [])
            if isinstance(t, dict)]
    tags = [t for t in tags if t]
    if not tags:
        tags = re.findall(r"#([\w\u0400-\u04FF]+)", desc)

    duration = node.get("duration") or node.get("video", {}).get("duration") or 0
    create_time = node.get("createTime") or node.get("createTimeISO") or node.get("create_time")

    row = VideoRow(
        video_id=video_id,
        url=f"https://www.tiktok.com/@{handle}/video/{video_id}" if handle and video_id else "",
        author=handle,
        nickname=str(author.get("nickname") or author.get("nickName") or ""),
        created_at=create_time if isinstance(create_time, str) and "-" in str(create_time)
                   else ts_to_iso(create_time),
        duration_sec=to_int(duration),
        views=to_int(stats.get("playCount")),
        likes=to_int(stats.get("diggCount")),
        comments=to_int(stats.get("commentCount")),
        shares=to_int(stats.get("shareCount")),
        saves=to_int(stats.get("collectCount")),
        hashtags=" ".join(f"#{t.lstrip('#')}" for t in dict.fromkeys(tags)),
        music=str(music.get("title") or music.get("musicName") or ""),
        description=" ".join(desc.split()),
    )
    row.engagement_rate = calc_er(row)
    return row


def calc_er(row: VideoRow) -> float:
    if row.views <= 0:
        return 0.0
    return round((row.likes + row.comments + row.shares + row.saves) / row.views * 100, 3)


def extract_links(html: str) -> list[tuple[str, str, str]]:
    """Ссылки на видео из DOM: возвращает (author, video_id, caption-черновик)."""
    soup = BeautifulSoup(html, "html.parser")
    found: dict[tuple[str, str], str] = {}
    for anchor in soup.find_all("a", href=True):
        m = VIDEO_RE.search(anchor["href"])
        if not m:
            continue
        key = (m.group(1), m.group(2))
        if key in found:
            continue
        text = " ".join(anchor.get_text(" ", strip=True).split())
        found[key] = text or (anchor.get("aria-label") or "")
    return [(a, v, c) for (a, v), c in found.items()]


# ─────────────────────────── браузер ───────────────────────────
BLOCK_MARKERS = ("captcha", "tiktok-verify", "verify-ele", "Please wait",
                 "Проверьте, что вы не робот", "Подтвердите, что вы человек")


class Browser:
    """Тонкая обёртка над Playwright: один контекст, вежливые паузы, детект капчи."""

    def __init__(self, headless: bool = True, storage_state: str | None = None,
                 proxy: str | None = None, user_agent: str = DEFAULT_UA,
                 locale: str = "ru-RU", timezone_id: str = "Europe/Moscow",
                 timeout: int = 45_000):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        launch_kwargs: dict[str, Any] = {
            "headless": headless,
            "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        }
        if proxy:
            launch_kwargs["proxy"] = {"server": proxy}
        self.browser = self._pw.chromium.launch(**launch_kwargs)
        ctx_kwargs: dict[str, Any] = {
            "user_agent": user_agent,
            "locale": locale,
            "timezone_id": timezone_id,
            "viewport": {"width": 1366, "height": 900},
        }
        if storage_state and Path(storage_state).exists():
            ctx_kwargs["storage_state"] = storage_state
            LOG.info("Использую сохранённую сессию: %s", storage_state)
        self.ctx = self.browser.new_context(**ctx_kwargs)
        self.ctx.set_default_timeout(timeout)
        # Прячем самый очевидный признак automations.
        self.ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome={runtime:{}};"
        )
        self.page = self.ctx.new_page()
        self.captcha_hit = False  # если поймали проверку — дальше не дёргаем сайт

    def blocked(self) -> bool:
        try:
            html = self.page.content()
        except Exception:
            return False
        low = html.lower()
        hit = any(marker.lower() in low for marker in BLOCK_MARKERS)
        if hit:
            self.captcha_hit = True
        return hit

    def goto(self, url: str, wait: float = 3.0) -> str:
        self.page.goto(url, wait_until="domcontentloaded")
        sleep_jitter(wait, 1.0)
        try:
            self.page.mouse.wheel(0, 400)
        except Exception:
            pass
        return self.page.content()

    def close(self) -> None:
        for closer in (self.ctx, self.browser):
            try:
                closer.close()
            except Exception:
                pass
        try:
            self._pw.stop()
        except Exception:
            pass


def scroll_tag_page(browser: Browser, tag: str, max_scrolls: int,
                    pause: float) -> tuple[list[tuple[str, str, str]], list[VideoRow]]:
    """Скроллим выдачу хештега и собираем всё, что увидели."""
    url = f"https://www.tiktok.com/tag/{tag}"
    LOG.info("[%s] открываю %s", tag, url)
    browser.goto(url, wait=4.0)

    links: dict[tuple[str, str], str] = {}
    rows: dict[str, VideoRow] = {}
    stale = 0

    for step in range(max_scrolls):
        html = browser.page.content()
        for author, vid, caption in extract_links(html):
            links.setdefault((author, vid), caption)
        for row in extract_embedded_json(html):
            rows.setdefault(row.video_id, row)

        if browser.blocked():
            LOG.warning("[%s] похоже на капчу/проверку — останавливаюсь. "
                        "Залогиньтесь (--save-login) или уменьшите --limit.", tag)
            break

        before = len(links)
        try:
            browser.page.mouse.wheel(0, 6000)
            browser.page.evaluate("window.scrollBy(0, 6000)")
        except Exception as exc:
            LOG.debug("скролл не удался: %s", exc)
            break
        sleep_jitter(pause, 1.0)
        after = len(links)
        LOG.info("[%s] прокрутка %d/%d → ссылок: %d", tag, step + 1, max_scrolls, after)
        stale = stale + 1 if after == before else 0
        if stale >= 3:
            LOG.info("[%s] новых видео нет — выдача кончилась", tag)
            break

    return [(a, v, c) for (a, v), c in links.items()], list(rows.values())


def enrich_video(browser: Browser, author: str, video_id: str,
                 retries: int = 2, pause: float = 2.0) -> VideoRow | None:
    url = f"https://www.tiktok.com/@{author}/video/{video_id}"
    for attempt in range(1, retries + 1):
        try:
            html = browser.goto(url, wait=1.5)
            if browser.blocked():
                LOG.warning("капча на %s — пропуск", url)
                return None
            rows = extract_embedded_json(html)
            if rows:
                row = rows[0]
                row.url = url
                return row
            LOG.debug("метрики не нашлись в JSON (%d/%d): %s", attempt, retries, url)
        except Exception as exc:
            LOG.debug("ошибка загрузки %s (%d/%d): %s", url, attempt, retries, exc)
        sleep_jitter(pause * attempt, 1.0)
    return None


def _is_missing_browser(exc: Exception) -> bool:
    """Playwright установлен, а сам браузер не скачан — подсказываем команду, а не стектрейс."""
    msg = str(exc)
    if "Executable doesn't exist" not in msg and "playwright install" not in msg.lower():
        return False
    print("\n" + "!" * 72)
    print("! Playwright установлен, но браузер для него не скачан.")
    print("! Выполните в терминале (подставьте свой python, если запускаете не им):")
    print(f"!     {sys.executable} -m playwright install chromium")
    print("! Если сразу после этого снова ругается на номер сборки — обновите сам пакет:")
    print(f"!     {sys.executable} -m pip install -U playwright")
    print(f"!     {sys.executable} -m playwright install chromium")
    print("!" * 72 + "\n")
    return True


def save_login_state(path: str, headless: bool = False) -> None:
    """Открываем браузер, ждём ручной логин, сохраняем куки."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless, args=["--no-sandbox"])
        ctx = browser.new_context(user_agent=DEFAULT_UA, locale="ru-RU",
                                  viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        page.goto("https://www.tiktok.com/login", wait_until="domcontentloaded")
        print("\n>>> Залогиньтесь в открывшемся окне, потом нажмите Enter здесь <<<")
        input()
        ctx.storage_state(path=path)
        print(f"Сессия сохранена в {path}. Дальше запускайте с --storage-state {path}")
        browser.close()


# ─────────────────────────── отчётность ───────────────────────────
def write_outputs(rows: list[VideoRow], outdir: Path, fmt: str, stem: str) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    data = [{c: getattr(r, c) for c in VideoRow.COLUMNS} for r in rows]

    if fmt in ("csv", "all"):
        path = outdir / f"{stem}.csv"
        with path.open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=VideoRow.COLUMNS)
            writer.writeheader()
            writer.writerows(data)
        written.append(path)

    if fmt in ("json", "all"):
        path = outdir / f"{stem}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(path)

    if fmt in ("xlsx", "all"):
        path = outdir / f"{stem}.xlsx"
        _write_xlsx(path, rows)
        written.append(path)

    return written


def _write_xlsx(path: Path, rows: list[VideoRow]) -> None:
    import pandas as pd

    df = pd.DataFrame([{c: getattr(r, c) for c in VideoRow.COLUMNS} for r in rows])
    summary = build_summary(df)

    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        df.to_excel(xl, sheet_name="videos", index=False)
        summary.to_excel(xl, sheet_name="сводка", index=True)
        if not df.empty:
            top = df.sort_values("views", ascending=False).head(30)
            top.to_excel(xl, sheet_name="топ по просмотрам", index=False)
            er = df[df["views"] > 1000].sort_values("engagement_rate", ascending=False).head(30)
            er.to_excel(xl, sheet_name="топ по вовлечению", index=False)

        for sheet in xl.book.worksheets:
            for column_cells in sheet.columns:
                width = max((len(str(c.value)) for c in column_cells if c.value is not None),
                            default=10)
                sheet.column_dimensions[column_cells[0].column_letter].width = min(42, max(12, width + 2))
            sheet.freeze_panes = "A2"


def build_summary(df) -> "Any":
    import pandas as pd

    if df.empty:
        return pd.DataFrame({"значение": ["нет данных"]})

    items = {
        "всего видео": len(df),
        "уникальных авторов": df["author"].nunique(),
        "сумма просмотров": int(df["views"].sum()),
        "медиана просмотров": int(df["views"].median()),
        "средние просмотры": int(df["views"].mean()),
        "максимум просмотров": int(df["views"].max()),
        "медиана лайков": int(df["likes"].median()),
        "медиана ER, %": round(float(df["engagement_rate"].median()), 3),
        "видео с ER > 5%": int((df["engagement_rate"] > 5).sum()),
        "средняя длительность, сек": int(df["duration_sec"].mean()),
    }
    return pd.DataFrame({"значение": pd.Series(items)})


def print_console_summary(rows: list[VideoRow]) -> None:
    if not rows:
        LOG.warning("ничего не собрали")
        return
    views = sorted(r.views for r in rows)
    mid = views[len(views) // 2]
    print("\n─── Итог ──────────────────────────────")
    print(f"видео: {len(rows)} | авторов: {len({r.author for r in rows if r.author})}")
    print(f"просмотры: медиана {mid:,} | макс {max(views):,}".replace(",", " "))
    top = sorted(rows, key=lambda r: r.views, reverse=True)[:5]
    print("топ-5:")
    for i, r in enumerate(top, 1):
        print(f"  {i}. {r.views:>9,} {r.url}  @{r.author}  ER {r.engagement_rate}%".replace(",", " "))
    best = [r for r in rows if r.views > 1000]
    best.sort(key=lambda r: r.engagement_rate, reverse=True)
    if best:
        print("лучший ER:", f"{best[0].engagement_rate}% — {best[0].url}")
    print("───────────────────────────────────────\n")


# ─────────────────────────── демо-режим (без сети) ───────────────────────────
def run_demo(tags: list[str], limit: int, outdir: Path, fmt: str) -> list[VideoRow]:
    """Прогон пайплайна на локальных файлах из ./fixtures — чтобы проверить код без TikTok."""
    fx = Path(__file__).parent / "fixtures"
    tag_html = (fx / "tag_page.html").read_text(encoding="utf-8")
    video_files = sorted(fx.glob("video_page_*.html"))
    if not (fx / "tag_page.html").exists() or not video_files:
        LOG.error("фикстуры не найдены — запустите: python fixtures/make_fixtures.py")
        return []

    rows: list[VideoRow] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    for tag in tags:
        links = extract_links(tag_html)[:limit] or []
        embedded = extract_embedded_json(tag_html)
        for i, (author, vid, _caption) in enumerate(links):
            src = video_files[i % len(video_files)].read_text(encoding="utf-8")
            parsed = extract_embedded_json(src)
            if parsed:
                row = parsed[0]
            else:
                row = VideoRow()
            row.tag, row.video_id, row.author = tag, vid, author
            row.url = f"https://www.tiktok.com/@{author}/video/{vid}"
            row.collected_at = now
            row.engagement_rate = calc_er(row)
            rows.append(row)
        LOG.info("[демо][%s] ссылок %d, встроенных объектов %d", tag, len(links), len(embedded))
    return rows


# ─────────────────────────── CLI ───────────────────────────
def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Сбор публичной аналитики TikTok по хештегам (только чтение).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--tags", nargs="*", default=["работа", "заработок"],
                   help="хештеги через пробел или запятую")
    p.add_argument("--limit", type=int, default=30, help="сколько видео максимум на тег")
    p.add_argument("--out", default="out", help="папка для результатов")
    p.add_argument("--format", choices=["csv", "json", "xlsx", "all"], default="all")
    p.add_argument("--no-details", action="store_true",
                   help="не заходить на страницы видео (быстрее, но меньше полей)")
    p.add_argument("--scrolls", type=int, default=12, help="максимум прокруток выдачи")
    p.add_argument("--delay", type=float, default=2.0, help="базовая пауза между действиями, сек")
    p.add_argument("--headless", action="store_true", default=True, help="браузер без окна")
    p.add_argument("--no-headless", dest="headless", action="store_false", help="показать браузер")
    p.add_argument("--storage-state", default=None, help="файл сессии (см. --save-login)")
    p.add_argument("--save-login", default=None, metavar="FILE", help="залогиниться и сохранить куки")
    p.add_argument("--proxy", default=None, help="например http://user:pass@host:port")
    p.add_argument("--min-views", type=int, default=0, help="отсечь видео с просмотрами ниже")
    p.add_argument("--demo", action="store_true", help="прогон на тестовых файлах без сети")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S",
    )

    if args.save_login:
        save_login_state(args.save_login)
        return 0

    tags: list[str] = []
    for chunk in args.tags:
        tags.extend(normalize_tag(t) for t in str(chunk).split(",") if t.strip())
    tags = [t for t in dict.fromkeys(tags) if t]
    if not tags:
        LOG.error("не переданы хештеги")
        return 2

    outdir = Path(args.out)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    all_rows: list[VideoRow] = []

    if args.demo:
        all_rows = run_demo(tags, args.limit, outdir, args.format)
    else:
        try:
            browser = Browser(headless=args.headless, storage_state=args.storage_state,
                              proxy=args.proxy)
        except Exception as exc:
            if _is_missing_browser(exc):
                return 3
            raise
        try:
            for tag in tags:
                links, embedded = scroll_tag_page(browser, tag, args.scrolls, args.delay)
                by_id = {r.video_id: r for r in embedded}
                collected: list[VideoRow] = []

                if browser.captcha_hit:
                    LOG.error("[%s] TikTok показывает проверку. Останавливаюсь: "
                              "запустите с --save-login session.json и уменьшите --limit.", tag)
                    break

                for author, vid, _caption in links[: args.limit]:
                    row = by_id.get(vid)
                    if row is None and not args.no_details and not browser.captcha_hit:
                        row = enrich_video(browser, author, vid, pause=args.delay)
                        sleep_jitter(args.delay)
                    if row is None:
                        row = VideoRow(video_id=vid, author=author,
                                       url=f"https://www.tiktok.com/@{author}/video/{vid}")
                    row.tag = tag
                    row.collected_at = now
                    row.engagement_rate = calc_er(row)
                    collected.append(row)
                    LOG.info("[%s] %d/%d: @%s %s — %s просмотров", tag, len(collected),
                             min(len(links), args.limit), author, vid, f"{row.views:,}".replace(",", " "))

                LOG.info("[%s] собрано %d видео", tag, len(collected))
                all_rows.extend(collected)
                if browser.captcha_hit:
                    break
                sleep_jitter(args.delay * 2)
        finally:
            browser.close()

    if args.min_views:
        before = len(all_rows)
        all_rows = [r for r in all_rows if r.views >= args.min_views]
        LOG.info("фильтр по просмотрам: %d → %d", before, len(all_rows))

    stem = f"tiktok_{'_'.join(tags)[:60]}_{datetime.now().strftime('%Y%m%d_%H%M')}"
    files = write_outputs(all_rows, outdir, args.format, stem)
    print_console_summary(all_rows)
    for f in files:
        print("сохранено:", f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
