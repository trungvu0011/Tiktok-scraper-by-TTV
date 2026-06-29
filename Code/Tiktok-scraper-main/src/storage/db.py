"""SQLite persistence for the automation layer (jobs, schedules, competitor
snapshots, alerts, notifications).

Design notes
------------
- Single file DB (``scraper.db`` at the package root by default). WAL mode lets
  the worker/scheduler threads read while a request handler writes.
- One short-lived connection per operation (``sqlite3.connect`` is cheap). All
  writes go through ``_WRITE_LOCK`` so the worker, scheduler and HTTP threads
  never interleave a write mid-statement.
- Rows are returned as plain ``dict`` (``sqlite3.Row`` -> dict) so callers/JSON
  serialisation stay simple. JSON columns (params/result/raw/...) are decoded
  on read and encoded on write by the helpers below.

This module intentionally keeps the per-table CRUD helpers in one file: the
surface is small and pragmatic for v1, and it avoids a premature repository
split.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.utils.logger import get_logger

log = get_logger(__name__)

# Serialises every write across threads (worker / scheduler / request handlers).
_WRITE_LOCK = threading.Lock()

# Resolved once at init; default is scraper.db next to the package root.
_DB_PATH: Optional[Path] = None


# --------------------------------------------------------------------------- #
# time helpers — everything stored as ISO-8601 UTC strings (sortable as text)
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _loads(value: Optional[str]) -> Any:
    if value is None or value == "":
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# connection / schema
# --------------------------------------------------------------------------- #
def configure(db_path: str) -> None:
    """Point the module at a DB file. Call once before :func:`init_db`."""
    global _DB_PATH
    _DB_PATH = Path(db_path).expanduser().resolve()


def _path() -> Path:
    if _DB_PATH is None:
        # Sensible fallback so import-time helpers never crash: package root.
        return Path(__file__).resolve().parents[2] / "scraper.db"
    return _DB_PATH


@contextmanager
def get_conn():
    """Yield a connection with row->dict factory. Reads are lock-free; callers
    that write must hold ``_WRITE_LOCK`` (use :func:`_write`)."""
    conn = sqlite3.connect(str(_path()), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
    finally:
        conn.close()


@contextmanager
def _write():
    """Serialised write transaction: one writer at a time, auto commit/rollback."""
    with _WRITE_LOCK:
        with get_conn() as conn:
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise


def _rows(cur) -> List[Dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


def _row(cur) -> Optional[Dict[str, Any]]:
    r = cur.fetchone()
    return dict(r) if r is not None else None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    target      TEXT NOT NULL,
    params      TEXT,
    status      TEXT NOT NULL DEFAULT 'queued',
    source      TEXT NOT NULL DEFAULT 'manual',
    schedule_id TEXT,
    records     INTEGER,
    duration_s  REAL,
    result      TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);

CREATE TABLE IF NOT EXISTS schedules (
    id               TEXT PRIMARY KEY,
    name             TEXT,
    job_type         TEXT NOT NULL,
    target           TEXT NOT NULL,
    params           TEXT,
    interval_minutes INTEGER NOT NULL,
    enabled          INTEGER NOT NULL DEFAULT 1,
    last_run_at      TEXT,
    next_run_at      TEXT,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tracked_profiles (
    id        TEXT PRIMARY KEY,
    username  TEXT NOT NULL UNIQUE,
    nickname  TEXT,
    note      TEXT,
    color     TEXT,
    added_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL,
    captured_at   TEXT NOT NULL,
    followers     INTEGER,
    following     INTEGER,
    total_likes   INTEGER,
    video_count   INTEGER,
    avg_views     REAL,
    avg_er        REAL,
    new_video_ids TEXT,
    raw           TEXT
);
CREATE INDEX IF NOT EXISTS idx_snap_user ON snapshots(username, captured_at);

CREATE TABLE IF NOT EXISTS alerts (
    id             TEXT PRIMARY KEY,
    name           TEXT,
    scope_username TEXT,
    metric         TEXT NOT NULL,
    operator       TEXT NOT NULL,
    threshold      REAL,
    enabled        INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id         TEXT PRIMARY KEY,
    alert_id   TEXT,
    title      TEXT NOT NULL,
    body       TEXT,
    level      TEXT NOT NULL DEFAULT 'info',
    data       TEXT,
    is_read    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notif_read ON notifications(is_read, created_at);
"""


def init_db(db_path: Optional[str] = None) -> None:
    """Create tables if missing. Safe to call repeatedly (idempotent)."""
    if db_path:
        configure(db_path)
    with _write() as conn:
        conn.executescript(_SCHEMA)
        # Recover any jobs left 'running' by a previous crash/restart so the
        # queue never shows a phantom in-flight job.
        conn.execute(
            "UPDATE jobs SET status='error', error='interrupted by restart', "
            "finished_at=? WHERE status='running'",
            (now_iso(),),
        )
    log.info("DB ready at %s", _path())


# --------------------------------------------------------------------------- #
# jobs
# --------------------------------------------------------------------------- #
def insert_job(
    job_type: str,
    target: str,
    params: Optional[dict] = None,
    source: str = "manual",
    schedule_id: Optional[str] = None,
) -> str:
    jid = _new_id()
    with _write() as conn:
        conn.execute(
            "INSERT INTO jobs (id, type, target, params, status, source, "
            "schedule_id, created_at) VALUES (?,?,?,?,'queued',?,?,?)",
            (jid, job_type, target, _dumps(params or {}), source, schedule_id, now_iso()),
        )
    return jid


def next_queued_job() -> Optional[Dict[str, Any]]:
    """Atomically claim the oldest queued job (mark it running) and return it."""
    with _write() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        job = dict(row)
        conn.execute(
            "UPDATE jobs SET status='running', started_at=? WHERE id=?",
            (now_iso(), job["id"]),
        )
    job["status"] = "running"
    job["params"] = _loads(job.get("params")) or {}
    return job


def finish_job(
    job_id: str,
    status: str,
    *,
    records: Optional[int] = None,
    duration_s: Optional[float] = None,
    result: Any = None,
    error: Optional[str] = None,
) -> None:
    with _write() as conn:
        conn.execute(
            "UPDATE jobs SET status=?, records=?, duration_s=?, result=?, "
            "error=?, finished_at=? WHERE id=?",
            (status, records, duration_s, _dumps(result), error, now_iso(), job_id),
        )


def cancel_job(job_id: str) -> bool:
    """Cancel a job only while it is still queued (can't kill a live browser)."""
    with _write() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status='canceled', finished_at=? "
            "WHERE id=? AND status='queued'",
            (now_iso(), job_id),
        )
        return cur.rowcount > 0


def get_job(job_id: str, with_result: bool = True) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        job = _row(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)))
    if job is None:
        return None
    job["params"] = _loads(job.get("params")) or {}
    if with_result:
        job["result"] = _loads(job.get("result"))
    else:
        job.pop("result", None)
    return job


def list_jobs(status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Job log, newest first. Omits the heavy ``result`` blob (use get_job)."""
    sql = (
        "SELECT id, type, target, params, status, source, schedule_id, records, "
        "duration_s, error, created_at, started_at, finished_at FROM jobs"
    )
    args: list = []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    with get_conn() as conn:
        rows = _rows(conn.execute(sql, args))
    for r in rows:
        r["params"] = _loads(r.get("params")) or {}
    return rows


def latest_job_result(target: str, job_type: Optional[str] = None) -> Any:
    """Full stored result of the most recent *successful* job for ``target``.

    Used to re-open a competitor scrape in the rich account view without
    re-scraping. Matches the target case-insensitively, ignoring a leading '@'.
    """
    t = target.lstrip("@")
    sql = ("SELECT result FROM jobs WHERE status='success' AND "
           "lower(replace(target,'@','')) = lower(?)")
    args: list = [t]
    if job_type:
        sql += " AND type=?"
        args.append(job_type)
    sql += " ORDER BY created_at DESC LIMIT 1"
    with get_conn() as conn:
        row = conn.execute(sql, args).fetchone()
    return _loads(row["result"]) if row else None


# --------------------------------------------------------------------------- #
# schedules
# --------------------------------------------------------------------------- #
def insert_schedule(
    job_type: str,
    target: str,
    interval_minutes: int,
    name: Optional[str] = None,
    params: Optional[dict] = None,
    enabled: bool = True,
) -> str:
    sid = _new_id()
    ts = now_iso()
    with _write() as conn:
        conn.execute(
            "INSERT INTO schedules (id, name, job_type, target, params, "
            "interval_minutes, enabled, next_run_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (sid, name, job_type, target, _dumps(params or {}),
             int(interval_minutes), 1 if enabled else 0, ts, ts),
        )
    return sid


def list_schedules() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = _rows(conn.execute("SELECT * FROM schedules ORDER BY created_at DESC"))
    for r in rows:
        r["params"] = _loads(r.get("params")) or {}
        r["enabled"] = bool(r.get("enabled"))
    return rows


def due_schedules(now: Optional[str] = None) -> List[Dict[str, Any]]:
    now = now or now_iso()
    with get_conn() as conn:
        rows = _rows(conn.execute(
            "SELECT * FROM schedules WHERE enabled=1 AND "
            "(next_run_at IS NULL OR next_run_at <= ?)", (now,)
        ))
    for r in rows:
        r["params"] = _loads(r.get("params")) or {}
    return rows


def mark_schedule_run(schedule_id: str, last_run_at: str, next_run_at: str) -> None:
    with _write() as conn:
        conn.execute(
            "UPDATE schedules SET last_run_at=?, next_run_at=? WHERE id=?",
            (last_run_at, next_run_at, schedule_id),
        )


def update_schedule(schedule_id: str, fields: Dict[str, Any]) -> bool:
    allowed = {"name", "interval_minutes", "enabled", "target", "params", "next_run_at"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "params":
            v = _dumps(v or {})
        if k == "enabled":
            v = 1 if v else 0
        sets.append(f"{k}=?")
        args.append(v)
    if not sets:
        return False
    args.append(schedule_id)
    with _write() as conn:
        cur = conn.execute(f"UPDATE schedules SET {', '.join(sets)} WHERE id=?", args)
        return cur.rowcount > 0


def delete_schedule(schedule_id: str) -> bool:
    with _write() as conn:
        cur = conn.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
        return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# tracked profiles (competitors)
# --------------------------------------------------------------------------- #
_PALETTE = ["#fe2c55", "#1877f2", "#22c55e", "#f59e0b", "#8b5cf6", "#06b6d4",
            "#ec4899", "#14b8a6"]


def add_tracked_profile(username: str, note: Optional[str] = None) -> Dict[str, Any]:
    username = username.lstrip("@").strip()
    existing = get_tracked_profile(username)
    if existing:
        return existing
    pid = _new_id()
    with get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM tracked_profiles").fetchone()["c"]
    color = _PALETTE[n % len(_PALETTE)]
    with _write() as conn:
        conn.execute(
            "INSERT INTO tracked_profiles (id, username, note, color, added_at) "
            "VALUES (?,?,?,?,?)",
            (pid, username, note, color, now_iso()),
        )
    return get_tracked_profile(username)


def get_tracked_profile(username: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        return _row(conn.execute(
            "SELECT * FROM tracked_profiles WHERE username=?", (username.lstrip('@'),)
        ))


def list_tracked_profiles() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        return _rows(conn.execute(
            "SELECT * FROM tracked_profiles ORDER BY added_at"
        ))


def set_profile_nickname(username: str, nickname: str) -> None:
    with _write() as conn:
        conn.execute(
            "UPDATE tracked_profiles SET nickname=? WHERE username=?",
            (nickname, username.lstrip("@")),
        )


def delete_tracked_profile(profile_id: str) -> bool:
    with _write() as conn:
        cur = conn.execute("DELETE FROM tracked_profiles WHERE id=?", (profile_id,))
        return cur.rowcount > 0


def is_tracked(username: str) -> bool:
    return get_tracked_profile(username) is not None


# --------------------------------------------------------------------------- #
# snapshots
# --------------------------------------------------------------------------- #
def insert_snapshot(username: str, metrics: Dict[str, Any]) -> str:
    sid = _new_id()
    with _write() as conn:
        conn.execute(
            "INSERT INTO snapshots (id, username, captured_at, followers, "
            "following, total_likes, video_count, avg_views, avg_er, "
            "new_video_ids, raw) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                sid, username.lstrip("@"), now_iso(),
                metrics.get("followers"), metrics.get("following"),
                metrics.get("total_likes"), metrics.get("video_count"),
                metrics.get("avg_views"), metrics.get("avg_er"),
                _dumps(metrics.get("new_video_ids") or []),
                _dumps(metrics.get("raw")),
            ),
        )
    return sid


def latest_snapshot(username: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        snap = _row(conn.execute(
            "SELECT * FROM snapshots WHERE username=? "
            "ORDER BY captured_at DESC LIMIT 1", (username.lstrip("@"),)
        ))
    if snap:
        snap["new_video_ids"] = _loads(snap.get("new_video_ids")) or []
        snap.pop("raw", None)
    return snap


def snapshot_series(username: str, limit: int = 90) -> List[Dict[str, Any]]:
    """Oldest-first metric time series for trend charts (raw blob omitted)."""
    with get_conn() as conn:
        rows = _rows(conn.execute(
            "SELECT captured_at, followers, following, total_likes, video_count, "
            "avg_views, avg_er FROM snapshots WHERE username=? "
            "ORDER BY captured_at DESC LIMIT ?", (username.lstrip("@"), limit)
        ))
    return list(reversed(rows))


# --------------------------------------------------------------------------- #
# alerts
# --------------------------------------------------------------------------- #
def insert_alert(
    metric: str,
    operator: str,
    threshold: Optional[float],
    scope_username: Optional[str] = None,
    name: Optional[str] = None,
    enabled: bool = True,
) -> str:
    aid = _new_id()
    with _write() as conn:
        conn.execute(
            "INSERT INTO alerts (id, name, scope_username, metric, operator, "
            "threshold, enabled, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (aid, name, (scope_username or None), metric, operator,
             threshold, 1 if enabled else 0, now_iso()),
        )
    return aid


def list_alerts(scope_username: Optional[str] = None,
                only_enabled: bool = False) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM alerts"
    clauses, args = [], []
    if only_enabled:
        clauses.append("enabled=1")
    if scope_username is not None:
        # rules scoped to this user OR global (scope NULL)
        clauses.append("(scope_username=? OR scope_username IS NULL)")
        args.append(scope_username.lstrip("@"))
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC"
    with get_conn() as conn:
        rows = _rows(conn.execute(sql, args))
    for r in rows:
        r["enabled"] = bool(r.get("enabled"))
    return rows


def update_alert(alert_id: str, fields: Dict[str, Any]) -> bool:
    allowed = {"name", "scope_username", "metric", "operator", "threshold", "enabled"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "enabled":
            v = 1 if v else 0
        sets.append(f"{k}=?")
        args.append(v)
    if not sets:
        return False
    args.append(alert_id)
    with _write() as conn:
        cur = conn.execute(f"UPDATE alerts SET {', '.join(sets)} WHERE id=?", args)
        return cur.rowcount > 0


def delete_alert(alert_id: str) -> bool:
    with _write() as conn:
        cur = conn.execute("DELETE FROM alerts WHERE id=?", (alert_id,))
        return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# notifications
# --------------------------------------------------------------------------- #
def add_notification(
    title: str,
    body: str = "",
    level: str = "info",
    alert_id: Optional[str] = None,
    data: Any = None,
) -> str:
    nid = _new_id()
    with _write() as conn:
        conn.execute(
            "INSERT INTO notifications (id, alert_id, title, body, level, data, "
            "created_at) VALUES (?,?,?,?,?,?,?)",
            (nid, alert_id, title, body, level, _dumps(data), now_iso()),
        )
    return nid


def list_notifications(unread_only: bool = False, limit: int = 50) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM notifications"
    if unread_only:
        sql += " WHERE is_read=0"
    sql += " ORDER BY created_at DESC LIMIT ?"
    with get_conn() as conn:
        rows = _rows(conn.execute(sql, (limit,)))
    for r in rows:
        r["is_read"] = bool(r.get("is_read"))
        r["data"] = _loads(r.get("data"))
    return rows


def unread_count() -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM notifications WHERE is_read=0"
        ).fetchone()["c"]


def mark_notification_read(notif_id: str) -> bool:
    with _write() as conn:
        cur = conn.execute(
            "UPDATE notifications SET is_read=1 WHERE id=?", (notif_id,)
        )
        return cur.rowcount > 0


def mark_all_notifications_read() -> int:
    with _write() as conn:
        cur = conn.execute("UPDATE notifications SET is_read=1 WHERE is_read=0")
        return cur.rowcount
