#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
web_app.py — локальный веб-интерфейс для tt_parser.py.

Запуск:
    python web_app.py
    → http://127.0.0.1:8000

Что внутри:
  • форма: хештеги, лимит, режим (демо/реальный), сессия TikTok;
  • сбор идёт в фоне, строки падают в таблицу по мере нахождения;
  • таблица сортируется по любому столбцу, есть поиск и живой лог;
  • демо-режим работает без интернета и браузера — на тестовых страницах.

Только чтение публичных данных. Никаких комментариев и действий от лица аккаунта.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

import collector
import tt_parser as tt

BASE = Path(__file__).resolve().parent
FIXTURES = BASE / "fixtures"
SESSIONS = BASE / "sessions"
SESSIONS.mkdir(exist_ok=True)

app = Flask(__name__)
logging.getLogger("werkzeug").setLevel(logging.WARNING)


# ───────────────────────────── задачи ─────────────────────────────
class Job:
    """Одна задача сбора. Живёт в памяти, ничего не пишет на диск."""

    def __init__(self, params: dict):
        import uuid

        self.id = uuid.uuid4().hex[:8]
        self.params = params
        self.rows: list[tt.VideoRow] = []
        self.log: list[str] = []
        self.running = True
        self.error: str | None = None
        self.stop = threading.Event()
        self.started = datetime.now().strftime("%H:%M:%S")
        self.progress = {"tag": "", "done": 0, "total": 0, "overall": 0}
        self._lock = threading.Lock()

    def add_log(self, line: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self.log.append(f"{stamp} {line}")
            if len(self.log) > 400:
                del self.log[:-400]

    def add_row(self, row: tt.VideoRow) -> None:
        with self._lock:
            self.rows.append(row)
            self.progress["overall"] = len(self.rows)

    def snapshot(self, since: int = 0) -> dict:
        with self._lock:
            rows = [{c: getattr(r, c) for c in tt.VideoRow.COLUMNS}
                    for r in self.rows[since:]]
            total_rows = len(self.rows)
            log = self.log[-60:]
        return {
            "id": self.id,
            "running": self.running,
            "error": self.error,
            "progress": self.progress,
            "rows": rows,
            "start_index": since,
            "total_rows": total_rows,
            "log": log,
            "params": {k: v for k, v in self.params.items() if k != "storage_state"},
        }


JOBS: list[Job] = []
CURRENT: Job | None = None
_LOCK = threading.Lock()


class JobLogHandler(logging.Handler):
    """Забираем логи tt_parser в окно браузера."""

    def __init__(self, job: Job):
        super().__init__(level=logging.INFO)
        self.job = job

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.job.add_log(self.format(record))
        except Exception:
            pass


def run_job(job: Job) -> None:
    handler = JobLogHandler(job)
    tt.LOG.addHandler(handler)
    tt.LOG.setLevel(logging.INFO)
    try:
        _run_collection(job)
    except Exception as exc:
        job.error = f"{type(exc).__name__}: {exc}"
        job.add_log(f"ОШИБКА: {job.error}")
        job.add_log(traceback.format_exc(limit=3).strip().replace("\n", " | "))
    finally:
        job.running = False
        job.add_log("готово" if not job.error else "остановлено с ошибкой")
        tt.LOG.removeHandler(handler)


def _run_collection(job: Job) -> None:
    """Сбор через общий collector: и демо, и реальный режим."""

    def on_row(row: tt.VideoRow) -> None:
        job.add_row(row)

    def on_progress(progress: dict) -> None:
        job.progress.update(progress)
        job.progress["overall"] = len(job.rows)

    collector.collect_rows(job.params, on_row=on_row, should_stop=job.stop.is_set,
                           on_progress=on_progress)

    if job.params.get("min_views"):
        job.rows = [r for r in job.rows if r.views >= job.params["min_views"]]
        job.progress["overall"] = len(job.rows)


# ───────────────────────────── HTTP ─────────────────────────────
@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/start")
def api_start():
    global CURRENT
    data = request.get_json(force=True) or {}

    raw_tags = str(data.get("tags", "")).replace(",", " ").split()
    tags = list(dict.fromkeys(tt.normalize_tag(t) for t in raw_tags if t.strip()))
    if not tags:
        return jsonify({"error": "введите хотя бы один хештег"}), 400

    session_file = data.get("session")
    storage_state = str(SESSIONS / session_file) if session_file else None
    if storage_state and not Path(storage_state).exists():
        storage_state = None

    params = {
        "tags": tags,
        "limit": max(1, min(int(data.get("limit", 30)), 500)),
        "scrolls": max(1, min(int(data.get("scrolls", 12)), 60)),
        "delay": max(0.5, min(float(data.get("delay", 2.0)), 15.0)),
        "no_details": bool(data.get("no_details")),
        "headless": bool(data.get("headless", True)),
        "demo": bool(data.get("demo")),
        "storage_state": storage_state,
        "proxy": (data.get("proxy") or "").strip() or None,
        "min_views": int(data.get("min_views", 0) or 0),
    }

    with _LOCK:
        if CURRENT and CURRENT.running:
            return jsonify({"error": "сбор уже идёт — остановите его или подождите"}), 409
        job = Job(params)
        CURRENT = job
        JOBS.append(job)
        JOBS[:] = JOBS[-5:]

    job.add_log(f"старт: теги {', '.join(tags)}, лимит {params['limit']}"
                + (" (демо)" if params["demo"] else ""))
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return jsonify({"id": job.id})


@app.post("/api/stop")
def api_stop():
    if CURRENT:
        CURRENT.stop.set()
        CURRENT.add_log("останавливаюсь…")
    return jsonify({"ok": True})


@app.get("/api/status")
def api_status():
    since = int(request.args.get("since", 0))
    if CURRENT is None:
        return jsonify({"running": False, "rows": [], "log": [], "progress": {},
                        "total_rows": 0, "params": {}, "idle": True})
    return jsonify(CURRENT.snapshot(since))


@app.get("/api/export")
def api_export():
    """Скачать текущую таблицу — на всякий случай, если приспичит."""
    if CURRENT is None or not CURRENT.rows:
        return jsonify({"error": "нечего выгружать"}), 400
    fmt = request.args.get("format", "xlsx")
    stem = f"tiktok_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    tmp = BASE / "out"
    tmp.mkdir(exist_ok=True)
    files = tt.write_outputs(list(CURRENT.rows), tmp, fmt, stem)
    if not files:
        return jsonify({"error": "не удалось сформировать файл"}), 500
    return send_file(files[0], as_attachment=True)


@app.get("/api/sessions")
def api_sessions():
    files = sorted(p.name for p in SESSIONS.glob("*.json"))
    return jsonify({"sessions": files})


@app.post("/api/session")
def api_session_upload():
    file = request.files.get("file")
    if not file or not file.filename.lower().endswith(".json"):
        return jsonify({"error": "нужен .json файл сессии"}), 400
    target = SESSIONS / Path(file.filename).name
    file.save(target)
    return jsonify({"ok": True, "name": target.name})


@app.get("/health")
def health():
    return jsonify({"ok": True, "jobs": len(JOBS)})


if __name__ == "__main__":
    port = int(__import__("os").environ.get("PORT", 8000))
    print(f"\n  Открывай в браузере: http://127.0.0.1:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
