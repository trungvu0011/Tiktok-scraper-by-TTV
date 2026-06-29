"""Background job queue: a single worker thread that runs scrapes serially.

Why serial? The heavy scraper (``ProfileVideosScraper``) launches a real,
off-screen Chromium window with a logged-in session. Running several at once
fights over that session and the machine's resources, so the queue processes one
job at a time (FIFO). This is intentional, not a limitation to "fix".

The worker also drives the monitoring features: after a successful scrape of a
*tracked* profile it records a metrics snapshot and evaluates alert rules.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from typing import Any, Dict, Optional

from src.config.settings import Settings
from src.scrapers.dispatch import run_scrape
from src.storage import db
from src.jobs.snapshots import record_snapshot
from src.jobs.alerts import evaluate_alerts
from src.utils.logger import get_logger

log = get_logger(__name__)


class JobQueue:
    """Owns the single worker thread. Use the module-level :data:`queue`."""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._settings = Settings()

    # -- public API ---------------------------------------------------------
    def enqueue(
        self,
        job_type: str,
        target: str,
        params: Optional[dict] = None,
        source: str = "manual",
        schedule_id: Optional[str] = None,
    ) -> str:
        """Add a job to the queue and return its id."""
        return db.insert_job(job_type, target, params, source, schedule_id)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="scrape-worker", daemon=True
        )
        self._thread.start()
        log.info("Job worker started.")

    def stop(self) -> None:
        self._stop.set()

    # -- internals ----------------------------------------------------------
    def _run(self) -> None:
        # Playwright sync needs a Proactor loop on Windows to spawn Chromium.
        # The process-wide policy is set at app startup, but set it on this
        # thread too so the worker is robust if started independently.
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        while not self._stop.is_set():
            job = db.next_queued_job()
            if job is None:
                time.sleep(1.0)
                continue
            self._process(job)

    def _process(self, job: Dict[str, Any]) -> None:
        job_id = job["id"]
        log.info("Running job %s (%s %s)", job_id, job["type"], job["target"])
        start = time.time()
        try:
            result = run_scrape(
                job["type"], job["target"], job.get("params") or {}, self._settings
            )
        except Exception as e:  # scraper blew up unexpectedly
            log.exception("Job %s crashed", job_id)
            db.finish_job(job_id, "error", duration_s=time.time() - start, error=str(e))
            return

        duration = time.time() - start
        status = "success" if result.get("status") == "success" else "error"
        db.finish_job(
            job_id,
            status,
            records=result.get("records"),
            duration_s=duration,
            result=result,
            error=result.get("message") if status == "error" else None,
        )

        if status == "success":
            self._post_success(job, result)

    def _post_success(self, job: Dict[str, Any], result: Dict[str, Any]) -> None:
        """Snapshot + alert evaluation for tracked profiles."""
        profile = result.get("profile") or {}
        username = (profile.get("username")
                    or str(job.get("target") or "").lstrip("@").lstrip("#"))
        if not username or not db.is_tracked(username):
            return
        try:
            if profile.get("nickname"):
                db.set_profile_nickname(username, profile["nickname"])
            snap_data = record_snapshot(username, result)
            if snap_data:
                evaluate_alerts(username, snap_data)
        except Exception:
            log.exception("Post-success snapshot/alert failed for @%s", username)


# Module-level singleton.
queue = JobQueue()
