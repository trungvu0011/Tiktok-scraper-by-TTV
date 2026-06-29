"""Single scrape dispatch shared by the synchronous HTTP endpoint and the
background job worker.

``run_scrape`` runs exactly one scrape (Playwright sync, so it must execute in a
worker thread, never the asyncio loop) and returns the same normalised dict the
``/api/scrape`` endpoint used to build inline. Extracting it here means the
endpoint and the queue worker stay byte-for-byte consistent.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from src.config.settings import Settings
from src.scrapers.profile.scraper import ProfileScraper
from src.scrapers.video.scraper import VideoScraper
from src.scrapers.video.profile_videos import ProfileVideosScraper
from src.scrapers.video.video_detail import VideoDetailScraper
from src.scrapers.transcript.scraper import TranscriptScraper
from src.scrapers.comments.scraper import CommentsScraper
from src.scrapers.hashtags.scraper import HashtagScraper
from src.scrapers.social.scraper import SocialScraper


def _dispatch(
    scrape_type: str, target: str, params: Dict[str, Any], settings: Settings
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Run the right scraper and return ``(raw_result, payload)``.

    ``target`` may carry an '@' (profile) or '#' (hashtag) prefix; strip it for
    the scrapers that take a bare name, but keep the original for URL-based ones.
    """
    target_clean = target.lstrip("@").lstrip("#")
    date_from = str(params.get("date_from") or "")
    date_to = str(params.get("date_to") or "")

    if scrape_type == "profile":
        result = ProfileScraper(settings).scrape(username=target_clean)
        return result, {"profile": result.get("profile")}
    if scrape_type == "video":
        result = VideoScraper(settings).scrape(video_url=target)
        return result, {"video": result.get("video")}
    if scrape_type == "comments":
        result = CommentsScraper(settings).scrape(video_url=target)
        return result, {"comments": result.get("comments")}
    if scrape_type == "hashtag":
        result = HashtagScraper(settings).scrape(hashtag=target_clean)
        return result, {"hashtag": result.get("hashtag")}
    if scrape_type == "profile_videos":
        result = ProfileVideosScraper(settings).scrape(
            username=target_clean, date_from=date_from, date_to=date_to
        )
        return result, {
            "profile": result.get("profile"),
            "videos": result.get("videos"),
            "video_count": result.get("video_count"),
        }
    if scrape_type == "video_detail":
        result = VideoDetailScraper(settings).scrape(video_url=target)
        return result, {
            "video": result.get("video"),
            "comments": result.get("comments"),
            "comment_count": result.get("comment_count"),
            "comment_total": result.get("comment_total"),
        }
    if scrape_type == "transcript":
        result = TranscriptScraper(settings).scrape(video_url=target)
        return result, {
            "video": result.get("video"),
            "transcripts": result.get("transcripts"),
            "original_language": result.get("original_language"),
            "has_transcript": result.get("has_transcript"),
            "method": result.get("method"),
        }
    if scrape_type == "social":
        result = SocialScraper(settings).scrape(url=target)
        return result, {
            "media": result.get("media"),
            "comments": result.get("comments"),
            "comment_total": result.get("comment_total"),
        }
    raise ValueError(f"Unknown type: {scrape_type}")


def _count_records(scrape_type: str, payload: Dict[str, Any]) -> int:
    if scrape_type == "comments" and payload.get("comments") is not None:
        return len(payload["comments"])
    if scrape_type == "profile_videos":
        return payload.get("video_count") or 0
    if scrape_type == "video_detail":
        return payload.get("comment_count") or 0
    if scrape_type == "transcript":
        return len(payload.get("transcripts") or [])
    if scrape_type == "social":
        return len(payload.get("comments") or []) or 1
    return 1


def run_scrape(
    scrape_type: str,
    target: str,
    params: Optional[Dict[str, Any]] = None,
    settings: Optional[Settings] = None,
) -> Dict[str, Any]:
    """Execute one scrape and return the normalised response dict.

    Always returns a dict with at least ``status`` ('success'|'error'),
    ``type``, ``target``, ``duration``. On success it also carries the
    type-specific payload, ``records`` and a pretty ``json`` string.
    """
    settings = settings or Settings()
    params = params or {}
    start_time = time.time()

    try:
        result, payload = _dispatch(scrape_type, target, params, settings)
    except ValueError as e:
        return {"status": "error", "type": scrape_type, "target": target,
                "message": str(e)}

    duration_str = f"{time.time() - start_time:.1f}s"

    # Surface real scraper failures instead of masking them as success.
    if result.get("status") == "error":
        return {
            "status": "error",
            "type": scrape_type,
            "target": target,
            "duration": duration_str,
            "message": (result.get("error") or {}).get("message", "Scrape failed"),
            **payload,
        }

    base = {
        "status": "success",
        "type": scrape_type,
        "target": target,
        "records": _count_records(scrape_type, payload),
        "duration": duration_str,
        "execution_id": result.get("execution_id", "n/a"),
        "platform": "tiktok",
        **payload,
    }
    base["json"] = json.dumps(base, indent=2, ensure_ascii=False)
    return base
