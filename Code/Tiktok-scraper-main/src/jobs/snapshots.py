"""Turn a successful scrape result into a metrics snapshot for a tracked
profile, and persist it as a time-series row used by the competitor-comparison
and alert features.

A snapshot captures the headline profile stats plus, when the scrape included
the video grid, the average views / engagement rate and the set of video IDs
that are new since the previous snapshot.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.storage import db
from src.utils.logger import get_logger

log = get_logger(__name__)


def _engagement_rate(v: Dict[str, Any]) -> float:
    """ER % for a single video — mirrors the dashboard's `er` helper:
    (likes + comments + shares) / views * 100."""
    views = v.get("views") or 0
    if not views:
        return 0.0
    inter = (v.get("likes") or 0) + (v.get("comments") or 0) + (v.get("shares") or 0)
    return inter / views * 100.0


def compute_metrics(scrape_result: Dict[str, Any],
                    prev: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the metrics dict for a snapshot from a normalised scrape result.

    ``prev`` is the previous snapshot (or None) — used to flag new video IDs.
    """
    profile = scrape_result.get("profile") or {}
    videos: List[Dict[str, Any]] = scrape_result.get("videos") or []

    metrics: Dict[str, Any] = {
        "followers": profile.get("followers_count"),
        "following": profile.get("following_count"),
        "total_likes": profile.get("total_likes"),
        # Prefer the live grid count; fall back to the profile's declared count.
        "video_count": len(videos) if videos else profile.get("video_count"),
    }

    if videos:
        n = len(videos)
        metrics["avg_views"] = sum(v.get("views") or 0 for v in videos) / n
        metrics["avg_er"] = sum(_engagement_rate(v) for v in videos) / n

        current_ids = {str(v.get("video_id")) for v in videos if v.get("video_id")}
        # Only diff against a prior snapshot that actually saw the grid, else
        # the first scrape would report the whole back-catalogue as "new".
        prev_ids = set((prev or {}).get("_video_ids") or [])
        if prev is not None and prev_ids:
            metrics["new_video_ids"] = sorted(current_ids - prev_ids)
        else:
            metrics["new_video_ids"] = []
        # Stash the full id set so the NEXT snapshot can diff against it.
        metrics["raw"] = {"video_ids": sorted(current_ids)}

    return metrics


def record_snapshot(username: str, scrape_result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Persist a snapshot for ``username`` from a successful scrape result.

    Returns ``(new_snapshot, prev_snapshot)`` style data merged into one dict
    via the keys ``snapshot`` / ``prev`` so the alert engine can compare. Only
    runs for profile / profile_videos scrapes that carry a profile blob.
    """
    if scrape_result.get("status") != "success":
        return None
    if not scrape_result.get("profile"):
        return None

    prev_full = db.latest_snapshot(username)
    prev = _with_video_ids(prev_full)

    metrics = compute_metrics(scrape_result, prev)
    db.insert_snapshot(username, metrics)
    new_full = db.latest_snapshot(username)

    log.info(
        "Snapshot for @%s — followers=%s videos=%s new=%d",
        username, metrics.get("followers"), metrics.get("video_count"),
        len(metrics.get("new_video_ids") or []),
    )
    return {"snapshot": new_full, "prev": prev_full, "metrics": metrics,
            "videos": scrape_result.get("videos") or []}


def _with_video_ids(snap: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Attach the stored video-id list (from raw) onto a snapshot for diffing.

    ``latest_snapshot`` drops the heavy raw blob, so re-read it here.
    """
    if not snap:
        return None
    ids: List[str] = []
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT raw FROM snapshots WHERE id=?", (snap["id"],)
            ).fetchone()
        if row and row["raw"]:
            import json
            ids = (json.loads(row["raw"]) or {}).get("video_ids") or []
    except Exception:
        ids = []
    snap = dict(snap)
    snap["_video_ids"] = ids
    return snap
