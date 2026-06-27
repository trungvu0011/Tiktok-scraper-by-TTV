import uvicorn
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import json
import sys
import time
from pathlib import Path

from src.config.settings import Settings
from src.utils.logger import get_logger
from src.scrapers.profile.scraper import ProfileScraper
from src.scrapers.video.scraper import VideoScraper
from src.scrapers.video.profile_videos import ProfileVideosScraper
from src.scrapers.video.video_detail import VideoDetailScraper
from src.scrapers.transcript.scraper import TranscriptScraper
from src.scrapers.comments.scraper import CommentsScraper
from src.scrapers.hashtags.scraper import HashtagScraper
from src.scrapers.social.scraper import SocialScraper

log = get_logger(__name__)

app = FastAPI(title="TikTok Scraper API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _restore_proactor_loop_policy() -> None:
    # On Windows, uvicorn forces a Selector event-loop policy, but the sync
    # Playwright scrapers (run in worker threads) need a Proactor loop to spawn
    # the Chromium subprocess — a Selector loop raises NotImplementedError.
    # This runs after uvicorn's loop setup, so it wins.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

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

@app.get("/api/scrape")
def scrape_endpoint(type: str, target: str, date_from: str = "", date_to: str = ""):
    # NOTE: sync endpoint on purpose — the video scraper uses Playwright's sync
    # API, which cannot run inside FastAPI's asyncio loop. FastAPI runs sync
    # path operations in a worker thread, where Playwright sync works fine.
    settings = Settings()
    start_time = time.time()

    # Target may carry a '@' (profile) or '#' (hashtag) prefix; strip it.
    target_clean = target.lstrip("@").lstrip("#")

    if type == "profile":
        result = ProfileScraper(settings).scrape(username=target_clean)
        payload = {"profile": result.get("profile")}
    elif type == "video":
        result = VideoScraper(settings).scrape(video_url=target)
        payload = {"video": result.get("video")}
    elif type == "comments":
        result = CommentsScraper(settings).scrape(video_url=target)
        payload = {"comments": result.get("comments")}
    elif type == "hashtag":
        result = HashtagScraper(settings).scrape(hashtag=target_clean)
        payload = {"hashtag": result.get("hashtag")}
    elif type == "profile_videos":
        result = ProfileVideosScraper(settings).scrape(
            username=target_clean, date_from=date_from, date_to=date_to
        )
        payload = {
            "profile": result.get("profile"),
            "videos": result.get("videos"),
            "video_count": result.get("video_count"),
        }
    elif type == "video_detail":
        result = VideoDetailScraper(settings).scrape(video_url=target)
        payload = {
            "video": result.get("video"),
            "comments": result.get("comments"),
            "comment_count": result.get("comment_count"),
            "comment_total": result.get("comment_total"),
        }
    elif type == "transcript":
        result = TranscriptScraper(settings).scrape(video_url=target)
        payload = {
            "video": result.get("video"),
            "transcripts": result.get("transcripts"),
            "original_language": result.get("original_language"),
            "has_transcript": result.get("has_transcript"),
            "method": result.get("method"),
        }
    elif type == "social":
        result = SocialScraper(settings).scrape(url=target)
        payload = {
            "media": result.get("media"),
            "comments": result.get("comments"),
            "comment_total": result.get("comment_total"),
        }
    else:
        return {"status": "error", "message": "Unknown type"}

    duration_str = f"{time.time() - start_time:.1f}s"

    # Surface real scraper failures instead of masking them as success.
    if result.get("status") == "error":
        return {
            "status": "error",
            "type": type,
            "target": target,
            "duration": duration_str,
            "message": (result.get("error") or {}).get("message", "Scrape failed"),
            **payload,
        }

    records = 1
    if type == "comments" and payload.get("comments") is not None:
        records = len(payload["comments"])
    elif type == "profile_videos":
        records = payload.get("video_count") or 0
    elif type == "video_detail":
        records = payload.get("comment_count") or 0
    elif type == "transcript":
        records = len(payload.get("transcripts") or [])
    elif type == "social":
        records = len(payload.get("comments") or []) or 1

    base = {
        "status": "success",
        "type": type,
        "target": target,
        "records": records,
        "duration": duration_str,
        "execution_id": result.get("execution_id", "n/a"),
        "platform": "tiktok",
        **payload,
    }
    base["json"] = json.dumps(base, indent=2, ensure_ascii=False)
    return base

if __name__ == "__main__":
    uvicorn.run("src.api:app", host="0.0.0.0", port=8000, reload=True)
