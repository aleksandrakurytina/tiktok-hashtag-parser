#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collector.py — общая логика сбора, чтобы её переиспользовали CLI, веб и экспорт для Pages.

    from collector import collect_rows
    rows = collect_rows({"tags": ["заработок"], "limit": 30, "demo": False})

Прогресс и ошибки идут в логгер tt_parser.LOG — кто вызывает, тот навешивает свой handler.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import tt_parser as tt

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def collect_rows(params: dict,
                 on_row: Callable[[tt.VideoRow], None] | None = None,
                 should_stop: Callable[[], bool] | None = None,
                 on_progress: Callable[[dict], None] | None = None) -> list[tt.VideoRow]:
    """Собрать видео по списку тегов.

    on_row      — вызывается на каждую найденную строку (для живой таблицы);
    on_progress — на каждое изменение прогресса, отдаёт {"tag", "done", "total"};
    should_stop — проверка флага отмены.
    """
    rows: list[tt.VideoRow] = []
    prog = {"tag": "", "done": 0, "total": 0}
    limit = params["limit"]

    def emit(row: tt.VideoRow) -> None:
        row.tag = row.tag or prog["tag"]
        row.collected_at = row.collected_at or _stamp()
        row.engagement_rate = tt.calc_er(row)
        rows.append(row)
        prog["done"] += 1
        if on_row:
            on_row(row)
        if on_progress:
            on_progress(dict(prog))

    def start_tag(tag: str, total: int) -> None:
        prog.update(tag=tag, done=0, total=total)
        if on_progress:
            on_progress(dict(prog))

    tag = ""
    stopped = should_stop or (lambda: False)

    if params.get("demo"):
        for tag in params["tags"]:
            if stopped():
                break
            links = _demo_links(params)
            start_tag(tag, min(len(links), limit))
            for row in _demo_rows(params, tag, links):
                if stopped():
                    break
                emit(row)
        return rows

    browser = _open_browser(params)
    try:
        for tag in params["tags"]:
            if stopped():
                break
            links, embedded = tt.scroll_tag_page(browser, tag, params["scrolls"], params["delay"])
            tt.LOG.info("[%s] найдено ссылок: %d", tag, len(links))
            start_tag(tag, min(len(links), limit))
            if browser.captcha_hit:
                tt.LOG.warning("TikTok показывает проверку — останавливаюсь")
                break

            by_id = {r.video_id: r for r in embedded}
            for author, vid, _caption in links[:limit]:
                if stopped() or browser.captcha_hit:
                    break
                row = by_id.get(vid)
                if row is None and not params.get("no_details"):
                    row = tt.enrich_video(browser, author, vid, pause=params["delay"])
                    tt.sleep_jitter(params["delay"])
                if row is None:
                    row = tt.VideoRow(video_id=vid, author=author,
                                      url=f"https://www.tiktok.com/@{author}/video/{vid}")
                emit(row)

            if browser.captcha_hit:
                break
            tt.sleep_jitter(params["delay"] * 2)
    finally:
        browser.close()
    return rows


def _open_browser(params: dict) -> tt.Browser:
    try:
        return tt.Browser(headless=params.get("headless", True),
                          storage_state=params.get("storage_state"),
                          proxy=params.get("proxy"))
    except Exception as exc:
        if not tt._is_missing_browser(exc):
            raise
        raise RuntimeError("браузер Playwright не установлен — выполните: "
                           "python -m playwright install chromium")


def _demo_links(params: dict):
    """Ссылки из тестовой выдачи fixtures/tag_page.html."""
    tag_html_file = FIXTURES / "tag_page.html"
    if not tag_html_file.exists():
        raise RuntimeError("нет фикстур — запустите: python fixtures/make_fixtures.py")
    return tt.extract_links(tag_html_file.read_text(encoding="utf-8"))


def _demo_rows(params: dict, tag: str, links=None) -> list[tt.VideoRow]:
    """Тестовые данные из fixtures/ — без сети и браузера."""
    video_files = sorted(FIXTURES.glob("video_page_*.html"))
    if not video_files:
        raise RuntimeError("нет фикстур — запустите: python fixtures/make_fixtures.py")

    links = links if links is not None else _demo_links(params)
    out: list[tt.VideoRow] = []
    for i, (author, vid, _caption) in enumerate(links[: params["limit"]]):
        parsed = tt.extract_embedded_json(video_files[i % len(video_files)].read_text(encoding="utf-8"))
        row = parsed[0] if parsed else tt.VideoRow()
        row.tag, row.video_id, row.author = tag, vid, author
        row.url = f"https://www.tiktok.com/@{author}/video/{vid}"
        out.append(row)
    return out


def summarize(rows: list[tt.VideoRow]) -> dict:
    """Короткая сводка для шапки страницы."""
    if not rows:
        return {"total": 0}
    views = sorted(r.views for r in rows)
    ers = sorted(r.engagement_rate for r in rows)
    mid = views[len(views) // 2]
    return {
        "total": len(rows),
        "authors": len({r.author for r in rows if r.author}),
        "views_total": sum(r.views for r in rows),
        "views_median": mid,
        "views_max": max(views),
        "er_median": ers[len(ers) // 2],
        "hot_er": sum(1 for r in rows if r.engagement_rate >= 5),
        "tags": sorted({r.tag for r in rows if r.tag}),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    demo = collect_rows({"tags": ["работа", "заработок"], "limit": 5, "demo": True,
                         "scrolls": 3, "delay": 1})
    print(f"демо: {len(demo)} строк, сводка: {summarize(demo)}")
