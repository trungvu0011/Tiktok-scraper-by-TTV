import uvicorn
from fastapi import FastAPI, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config.settings import Settings
from src.utils.logger import get_logger
from src.scrapers.dispatch import run_scrape
from src.scrapers.online.scraper import OnlineProfileScraper
from src.storage import db
from src.jobs.worker import queue
from src.jobs.scheduler import scheduler

log = get_logger(__name__)

app = FastAPI(title="TikTok Scraper API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Package root (Code/Tiktok-scraper-main) — used to resolve the DB path.
_ROOT = Path(__file__).resolve().parent.parent


@app.on_event("startup")
def _startup() -> None:
    # On Windows, uvicorn forces a Selector event-loop policy, but the sync
    # Playwright scrapers (run in worker threads) need a Proactor loop to spawn
    # the Chromium subprocess — a Selector loop raises NotImplementedError.
    # This runs after uvicorn's loop setup, so it wins, and is inherited by the
    # worker/scheduler threads we start below.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    settings = Settings()
    db_path = settings.db_path
    if not Path(db_path).is_absolute():
        db_path = str(_ROOT / db_path)
    db.init_db(db_path)

    queue.start()
    scheduler.start()


# Mount static folder for frontend
static_dir = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


def _serve(name: str) -> str:
    with open(static_dir / name, "r", encoding="utf-8") as f:
        return f.read()


@app.get("/", response_class=HTMLResponse)
async def read_index():
    # New unified dashboard (profile info + full video analysis).
    return _serve("dashboard.html")


@app.get("/videos", response_class=HTMLResponse)
async def read_videos_ui():
    # Kept for backward-compatible bookmarks; serves the same dashboard.
    return _serve("dashboard.html")


# --------------------------------------------------------------------------- #
# Synchronous scrape (unchanged contract — used by the existing dashboard tools)
# --------------------------------------------------------------------------- #
@app.get("/api/scrape")
def scrape_endpoint(type: str, target: str, date_from: str = "", date_to: str = ""):
    # NOTE: sync endpoint on purpose — the video scraper uses Playwright's sync
    # API, which cannot run inside FastAPI's asyncio loop. FastAPI runs sync
    # path operations in a worker thread, where Playwright sync works fine.
    result = run_scrape(type, target, {"date_from": date_from, "date_to": date_to})
    if result.get("status") == "error" and "type" not in result:
        result["type"] = type
    return result


# --------------------------------------------------------------------------- #
# "Scrape online" — standalone, HTTP-only profile scrape (no browser, no queue,
# no DB). This is the one endpoint that works on a serverless / browserless host.
# Kept fully separate from the worker/dispatch path on purpose.
# --------------------------------------------------------------------------- #
@app.get("/api/scrape_online")
def scrape_online_endpoint(username: str):
    return OnlineProfileScraper(Settings()).scrape(username)


# --------------------------------------------------------------------------- #
# Async job queue
# --------------------------------------------------------------------------- #
class JobIn(BaseModel):
    type: str
    target: str
    params: Optional[Dict[str, Any]] = None


@app.post("/api/jobs")
def create_job(body: JobIn):
    target = (body.target or "").strip()
    if not body.type or not target:
        return JSONResponse({"error": "type and target are required"}, status_code=400)
    job_id = queue.enqueue(body.type, target, body.params or {}, source="manual")
    return {"id": job_id, "status": "queued"}


@app.get("/api/jobs")
def get_jobs(status: str = "", limit: int = 50):
    return {"jobs": db.list_jobs(status=status or None, limit=limit)}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = db.get_job(job_id, with_result=True)
    if job is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return job


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    ok = db.cancel_job(job_id)
    return {"canceled": ok}


# --------------------------------------------------------------------------- #
# Schedules
# --------------------------------------------------------------------------- #
class ScheduleIn(BaseModel):
    job_type: str = "profile_videos"
    target: str
    interval_minutes: int = 1440  # default: daily
    name: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    enabled: bool = True


class SchedulePatch(BaseModel):
    name: Optional[str] = None
    interval_minutes: Optional[int] = None
    enabled: Optional[bool] = None
    target: Optional[str] = None
    params: Optional[Dict[str, Any]] = None


@app.get("/api/schedules")
def get_schedules():
    return {"schedules": db.list_schedules()}


@app.post("/api/schedules")
def create_schedule(body: ScheduleIn):
    target = (body.target or "").strip()
    if not target:
        return JSONResponse({"error": "target is required"}, status_code=400)
    sid = db.insert_schedule(
        body.job_type, target, max(1, int(body.interval_minutes)),
        name=body.name, params=body.params or {}, enabled=body.enabled,
    )
    return {"id": sid}


@app.patch("/api/schedules/{schedule_id}")
def patch_schedule(schedule_id: str, body: SchedulePatch):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    ok = db.update_schedule(schedule_id, fields)
    return {"updated": ok}


@app.delete("/api/schedules/{schedule_id}")
def remove_schedule(schedule_id: str):
    return {"deleted": db.delete_schedule(schedule_id)}


# --------------------------------------------------------------------------- #
# Competitors (tracked TikTok profiles)
# --------------------------------------------------------------------------- #
class CompetitorIn(BaseModel):
    username: str
    note: Optional[str] = None
    schedule: bool = True          # also create a daily schedule
    interval_minutes: int = 1440


@app.get("/api/competitors")
def get_competitors():
    out: List[Dict[str, Any]] = []
    for p in db.list_tracked_profiles():
        snap = db.latest_snapshot(p["username"])
        out.append({**p, "latest": snap})
    return {"competitors": out}


@app.post("/api/competitors")
def add_competitor(body: CompetitorIn):
    username = (body.username or "").strip().lstrip("@")
    if not username:
        return JSONResponse({"error": "username is required"}, status_code=400)
    profile = db.add_tracked_profile(username, note=body.note)
    # Kick off an immediate scrape so the first snapshot lands.
    job_id = queue.enqueue("profile_videos", username, {}, source="manual")
    sid = None
    if body.schedule:
        sid = db.insert_schedule(
            "profile_videos", username, max(1, int(body.interval_minutes)),
            name=f"Theo dõi @{username}", enabled=True,
        )
        # Stagger the first scheduled run so it doesn't double-fire with the
        # immediate scrape above.
        nxt = (datetime.now(timezone.utc)
               + timedelta(minutes=max(1, int(body.interval_minutes)))).isoformat()
        db.update_schedule(sid, {"next_run_at": nxt})
    return {"profile": profile, "job_id": job_id, "schedule_id": sid}


@app.delete("/api/competitors/{profile_id}")
def remove_competitor(profile_id: str):
    return {"deleted": db.delete_tracked_profile(profile_id)}


@app.get("/api/competitors/result")
def competitor_result(username: str):
    """Full latest scrape result (profile + videos) for a tracked profile, so
    the dashboard can render it in the rich account view."""
    res = db.latest_job_result(username, "profile_videos")
    if not res:
        return {"status": "empty"}
    return res


@app.get("/api/competitors/compare")
def compare_competitors(usernames: str = ""):
    names = [u.strip().lstrip("@") for u in usernames.split(",") if u.strip()]
    series = {}
    latest = {}
    for u in names:
        series[u] = db.snapshot_series(u)
        latest[u] = db.latest_snapshot(u)
    return {"usernames": names, "series": series, "latest": latest}


# --------------------------------------------------------------------------- #
# Alerts
# --------------------------------------------------------------------------- #
class AlertIn(BaseModel):
    metric: str
    operator: str = "gt"
    threshold: Optional[float] = None
    scope_username: Optional[str] = None
    name: Optional[str] = None
    enabled: bool = True


class AlertPatch(BaseModel):
    name: Optional[str] = None
    metric: Optional[str] = None
    operator: Optional[str] = None
    threshold: Optional[float] = None
    scope_username: Optional[str] = None
    enabled: Optional[bool] = None


@app.get("/api/alerts")
def get_alerts(scope: str = ""):
    return {"alerts": db.list_alerts(scope_username=scope or None)}


@app.post("/api/alerts")
def create_alert(body: AlertIn):
    if not body.metric or not body.operator:
        return JSONResponse({"error": "metric and operator are required"},
                            status_code=400)
    aid = db.insert_alert(
        body.metric, body.operator, body.threshold,
        scope_username=(body.scope_username or None),
        name=body.name, enabled=body.enabled,
    )
    return {"id": aid}


@app.patch("/api/alerts/{alert_id}")
def patch_alert(alert_id: str, body: AlertPatch):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    return {"updated": db.update_alert(alert_id, fields)}


@app.delete("/api/alerts/{alert_id}")
def remove_alert(alert_id: str):
    return {"deleted": db.delete_alert(alert_id)}


# --------------------------------------------------------------------------- #
# Notifications (in-dashboard alert feed)
# --------------------------------------------------------------------------- #
@app.get("/api/notifications")
def get_notifications(unread: int = 0, limit: int = 50):
    return {
        "notifications": db.list_notifications(unread_only=bool(unread), limit=limit),
        "unread": db.unread_count(),
    }


@app.post("/api/notifications/{notif_id}/read")
def read_notification(notif_id: str):
    return {"read": db.mark_notification_read(notif_id)}


@app.post("/api/notifications/read-all")
def read_all_notifications():
    return {"read": db.mark_all_notifications_read()}


if __name__ == "__main__":
    # NOTE: no reload — the Windows Proactor loop policy set at startup must
    # survive, and reload spawns a Selector-loop subprocess that breaks
    # Playwright sync.
    uvicorn.run("src.api:app", host="0.0.0.0", port=8000)
