"""In-process scheduler: a lightweight ticker thread that enqueues due
schedules. Runs only while the server is up (by design — no OS-level service).

Every ``TICK_SECONDS`` it asks the DB for schedules whose ``next_run_at`` has
passed, enqueues a job for each via the shared :data:`worker.queue`, then bumps
``last_run_at`` / ``next_run_at`` by the schedule's interval.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from src.storage import db
from src.jobs.worker import queue
from src.utils.logger import get_logger

log = get_logger(__name__)

TICK_SECONDS = 60


class Scheduler:
    def __init__(self) -> None:
        self._thread = None
        self._stop = threading.Event()
        self._first = True

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="scheduler", daemon=True
        )
        self._thread.start()
        log.info("Scheduler started (tick=%ds).", TICK_SECONDS)

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        # First tick shortly after boot so a just-added schedule fires promptly.
        while not self._stop.wait(5 if self._first else TICK_SECONDS):
            self._first = False
            try:
                self._tick()
            except Exception:
                log.exception("Scheduler tick failed")

    def _tick(self) -> None:
        due = db.due_schedules()
        if not due:
            return
        now = datetime.now(timezone.utc)
        for sch in due:
            queue.enqueue(
                sch["job_type"], sch["target"], sch.get("params") or {},
                source="schedule", schedule_id=sch["id"],
            )
            interval = max(1, int(sch.get("interval_minutes") or 60))
            nxt = (now + timedelta(minutes=interval)).isoformat()
            db.mark_schedule_run(sch["id"], now.isoformat(), nxt)
            log.info("Scheduled job enqueued for %s (next run %s)",
                     sch["target"], nxt)


scheduler = Scheduler()
