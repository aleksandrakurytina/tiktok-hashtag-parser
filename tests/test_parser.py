# -*- coding: utf-8 -*-
"""Тесты парсера: запуск `pytest -q` (или `python -m pytest -q`) из корня репозитория.

Браузер и сеть не нужны — всё проверяется на фикстурах из ./fixtures.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tt_parser import (  # noqa: E402
    calc_er,
    extract_embedded_json,
    extract_links,
    normalize_tag,
    to_int,
    ts_to_iso,
    VideoRow,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def test_to_int():
    assert to_int("12 345") == 12345
    assert to_int("1.2K") == 1200
    assert to_int("3,5 млн") == 3_500_000
    assert to_int(None) == 0
    assert to_int("нет") == 0


def test_ts_to_iso():
    assert ts_to_iso(1_720_000_000) == "2024-07-03 09:46:40"
    assert ts_to_iso(0) == ""
    assert ts_to_iso("мусор") == ""


def test_normalize_tag():
    assert normalize_tag("  #Заработок ") == "заработок"
    assert normalize_tag("удаленка") == "удаленка"


def test_calc_er():
    row = VideoRow(views=10_000, likes=500, comments=100, shares=50, saves=50)
    assert calc_er(row) == 7.0
    assert calc_er(VideoRow(views=0, likes=10)) == 0.0


def test_extract_links_from_tag_page():
    html = (FIXTURES / "tag_page.html").read_text(encoding="utf-8")
    links = extract_links(html)
    assert links, "в выдаче должны найтись ссылки на видео"
    authors = {a for a, _v, _c in links}
    assert "money_blog_ru" in authors
    for author, video_id, _caption in links:
        assert author and video_id.isdigit()


def test_extract_metrics_from_video_page():
    html = (FIXTURES / "video_page_0.html").read_text(encoding="utf-8")
    rows = extract_embedded_json(html)
    assert len(rows) == 1
    row = rows[0]
    assert row.author == "money_blog_ru"
    assert row.views > 0
    assert row.likes > 0
    assert row.created_at.startswith("20")
    assert row.engagement_rate > 0


def test_extract_embedded_from_tag_page():
    html = (FIXTURES / "tag_page.html").read_text(encoding="utf-8")
    rows = extract_embedded_json(html)
    assert rows, "из выдачи должны достаться встроенные объекты с метриками"
    assert all(r.video_id for r in rows)


def test_garbage_html_is_safe():
    assert extract_links("<html><body>привет</body></html>") == []
    assert extract_embedded_json("<html><body>привет</body></html>") == []


if __name__ == "__main__":
    raise SystemExit(pytest.main(["-q", __file__]))
