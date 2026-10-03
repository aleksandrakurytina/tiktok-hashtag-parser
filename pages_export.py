#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pages_export.py — собирает данные и пишет docs/data.json для GitHub Pages.

Локально:
    python pages_export.py --tags работа,заработок --limit 30 --mode demo
    python pages_export.py --tags заработок --limit 30 --mode live --session session.json

В CI (см. .github/workflows/collect.yml) запускается по кнопке или по расписанию,
после чего docs/data.json коммитится обратно в репозиторий — страница обновляется сама.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import collector
import tt_parser as tt

BASE = Path(__file__).resolve().parent
DEFAULT_OUT = BASE / "docs" / "data.json"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Экспорт данных для GitHub Pages")
    p.add_argument("--tags", default="работа,заработок,удаленка",
                   help="хештеги через запятую или пробел")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--scrolls", type=int, default=12)
    p.add_argument("--delay", type=float, default=2.0)
    p.add_argument("--mode", choices=["demo", "live"], default="demo",
                   help="demo — тестовые данные без сети, live — реальный сбор")
    p.add_argument("--no-details", action="store_true")
    p.add_argument("--session", default=None, help="путь к session.json")
    p.add_argument("--proxy", default=None)
    p.add_argument("--out", default=str(DEFAULT_OUT))
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    tags = list(dict.fromkeys(
        tt.normalize_tag(t) for t in args.tags.replace(",", " ").split() if t.strip()))
    if not tags:
        print("не переданы хештеги", file=sys.stderr)
        return 2

    session = args.session
    if session and not Path(session).exists():
        logging.warning("сессия %s не найдена — работаю без неё", session)
        session = None

    params = {
        "tags": tags,
        "limit": max(1, min(args.limit, 500)),
        "scrolls": max(1, min(args.scrolls, 60)),
        "delay": max(0.5, min(args.delay, 15.0)),
        "demo": args.mode == "demo",
        "no_details": args.no_details,
        "headless": True,
        "storage_state": session,
        "proxy": args.proxy,
    }

    logging.info("режим %s, теги: %s", args.mode, ", ".join(tags))
    rows = collector.collect_rows(params)
    logging.info("собрано строк: %d", len(rows))

    payload = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "mode": args.mode,
        "params": {"tags": tags, "limit": params["limit"]},
        "stats": collector.summarize(rows),
        "rows": [{c: getattr(r, c) for c in tt.VideoRow.COLUMNS} for r in rows],
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"записано {len(rows)} строк → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
