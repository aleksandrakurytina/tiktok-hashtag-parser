#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Генерирует локальные копии страниц TikTok для демо-режима (без сети).

Запуск:  python fixtures/make_fixtures.py
Результат: fixtures/tag_page.html и fixtures/video_page_*.html

Это синтетика, максимально похожая по структуре на настоящие страницы:
те же контейнеры скриптов (__UNIVERSAL_DATA_FOR_REHYDRATION__ / SIGI_STATE),
те же ссылки /@user/video/id. Нужна, чтобы проверить парсер офлайн.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

HERE = Path(__file__).parent
random.seed(7)

AUTHORS = [
    ("money_blog_ru", "Мария | доход"),
    ("freelance_daily", "Фриланс каждый день"),
    ("rabota_online", "Работа онлайн"),
    ("cashflow_pro", "Деньги под контролем"),
    ("udalenka_life", "Удалёнка Life"),
    ("zarabotok_2026", "Заработок 2026"),
    ("biznes_start_ru", "Старт в бизнесе"),
    ("do_it_today", "Сделай сегодня"),
]
CAPTIONS = [
    "Заработок в интернете без вложений #заработок #работа #удаленка",
    "Как я вышел на 100к в месяц #работа #деньги #мотивация",
    "Три способа удалёнки, о которых молчат #удаленка #заработок",
    "Работа на телефоне: разбор без розовых очков #работа #заработок",
    "Сколько реально платят на фрилансе #фриланс #заработок",
    "Мой первый доход онлайн: честные цифры #заработок #работа",
    "Подработка после основной работы #подработка #работа",
    "Где искать заказы новичку #фриланс #работа #заработок",
]
MUSIC = ["original sound", "phonk drive", "lofi money", "upbeat pop", "ambient work"]


def video_payload(idx: int, author: str, nickname: str, desc: str, views: int) -> dict:
    video_id = str(7400000000000000000 + idx * 137)
    likes = int(views * random.uniform(0.04, 0.13))
    comments = int(likes * random.uniform(0.03, 0.09))
    shares = int(likes * random.uniform(0.02, 0.08))
    saves = int(likes * random.uniform(0.05, 0.20))
    return {
        "__DEFAULT_SCOPE__": {
            "webapp.video-detail": {
                "itemInfo": {
                    "itemStruct": {
                        "id": video_id,
                        "desc": desc,
                        "createTime": 1_720_000_000 + idx * 86_400,
                        "duration": random.randint(15, 95),
                        "challenges": [
                            {"title": t} for t in ("заработок", "работа",
                                                   "удаленка", "деньги")[: random.randint(2, 4)]
                        ],
                        "music": {"title": random.choice(MUSIC)},
                        "author": {"id": author, "uniqueId": author, "nickname": nickname},
                        "stats": {
                            "playCount": views,
                            "diggCount": likes,
                            "commentCount": comments,
                            "shareCount": shares,
                            "collectCount": saves,
                        },
                    }
                }
            }
        }
    }


def wrap_universal(payload: dict) -> str:
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>TikTok</title></head><body>'
        f'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
        f"{json.dumps(payload, ensure_ascii=False)}"
        f"</script></body></html>"
    )


def build() -> None:
    # 1) Страница хештега: набор ссылок + пара встроенных объектов (как в реальной выдаче).
    links = []
    for i, (author, _nick) in enumerate(AUTHORS):
        vid = str(7400000000000000000 + i * 137)
        links.append(
            f'<a href="/@{author}/video/{vid}" aria-label="{CAPTIONS[i]}">'
            f'<div>{CAPTIONS[i]}</div></a>'
        )
    embedded = {
        "__DEFAULT_SCOPE__": {
            "webapp.challenge-detail": {
                "itemList": [
                    video_payload(100 + i, a, n, CAPTIONS[i], random.randint(3_000, 900_000))
                    ["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]
                    for i, (a, n) in enumerate(AUTHORS[:3])
                ]
            }
        }
    }
    tag_html = (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        "<title>заработок — TikTok</title></head><body>"
        '<div data-e2e="challenge-item-list">' + "".join(links) + "</div>"
        '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
        + json.dumps(embedded, ensure_ascii=False)
        + "</script></body></html>"
    )
    (HERE / "tag_page.html").write_text(tag_html, encoding="utf-8")

    # 2) Несколько страниц видео с разными метриками.
    for i, (author, nickname) in enumerate(AUTHORS[:6]):
        payload = video_payload(i, author, nickname, CAPTIONS[i],
                                random.randint(2_000, 1_400_000))
        (HERE / f"video_page_{i}.html").write_text(wrap_universal(payload), encoding="utf-8")

    print(f"готово: {len(list(HERE.glob('video_page_*.html')))} страниц видео + tag_page.html в {HERE}")


if __name__ == "__main__":
    build()
