"""Alert evaluation: after a tracked profile is scraped, compare the fresh
snapshot against the previous one and the scraped videos, and raise in-dashboard
notifications for any enabled rule that fires.

Supported metrics / operators
------------------------------
- ``followers`` / ``total_likes`` / ``video_count`` / ``avg_er`` / ``avg_views``
  with ``gt`` (above), ``lt`` (below) or ``change_pct_gt`` (abs % change vs the
  previous snapshot exceeds the threshold).
- ``new_video``  — fires when the profile posted at least one new video.
- ``video_views`` — fires when any scraped video's view count exceeds the
  threshold (great for "a competitor's video crossed 1M views").
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.storage import db
from src.utils.logger import get_logger

log = get_logger(__name__)

# metrics read straight off a snapshot row
_SNAPSHOT_METRICS = {"followers", "total_likes", "video_count", "avg_er", "avg_views"}

_METRIC_LABEL = {
    "followers": "Follower",
    "total_likes": "Tổng lượt thích",
    "video_count": "Số video",
    "avg_er": "ER trung bình",
    "avg_views": "View trung bình",
    "new_video": "Video mới",
    "video_views": "View video",
}


def _fmt(n: Any) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    if abs(n) >= 1e9:
        return f"{n / 1e9:.1f}B"
    if abs(n) >= 1e6:
        return f"{n / 1e6:.1f}M"
    if abs(n) >= 1e3:
        return f"{n / 1e3:.1f}K"
    return f"{n:.0f}" if float(n).is_integer() else f"{n:.2f}"


def evaluate_alerts(username: str, snapshot_data: Dict[str, Any]) -> List[str]:
    """Evaluate every enabled rule for ``username`` (plus global rules) against
    the snapshot data produced by :func:`snapshots.record_snapshot`.

    Returns the list of created notification IDs.
    """
    snap = snapshot_data.get("snapshot") or {}
    prev = snapshot_data.get("prev") or {}
    videos = snapshot_data.get("videos") or []
    rules = db.list_alerts(scope_username=username, only_enabled=True)

    created: List[str] = []
    for rule in rules:
        try:
            note = _check_rule(rule, username, snap, prev, videos)
        except Exception as e:  # never let one bad rule break the worker
            log.warning("Alert rule %s failed: %s", rule.get("id"), e)
            note = None
        if note:
            nid = db.add_notification(
                title=note["title"], body=note["body"], level=note["level"],
                alert_id=rule["id"], data=note.get("data"),
            )
            created.append(nid)
    if created:
        log.info("Raised %d alert(s) for @%s", len(created), username)
    return created


def _check_rule(rule, username, snap, prev, videos) -> Optional[Dict[str, Any]]:
    metric = rule["metric"]
    op = rule["operator"]
    threshold = rule.get("threshold")
    handle = "@" + username
    label = _METRIC_LABEL.get(metric, metric)

    if metric == "new_video":
        new_ids = snap.get("new_video_ids") or []
        if not new_ids:
            return None
        return {
            "title": f"{handle} đăng {len(new_ids)} video mới",
            "body": f"Phát hiện {len(new_ids)} video mới so với lần quét trước.",
            "level": "info",
            "data": {"username": username, "new_video_ids": new_ids},
        }

    if metric == "video_views":
        if threshold is None:
            return None
        hits = [v for v in videos if (v.get("views") or 0) >= threshold]
        if not hits:
            return None
        top = max(hits, key=lambda v: v.get("views") or 0)
        return {
            "title": f"{handle}: video vượt {_fmt(threshold)} view",
            "body": (f"{len(hits)} video vượt mốc. Cao nhất: "
                     f"{_fmt(top.get('views'))} view — "
                     f"{(top.get('description') or '')[:80]}"),
            "level": "success",
            "data": {"username": username, "video_url": top.get("video_url"),
                     "views": top.get("views")},
        }

    if metric in _SNAPSHOT_METRICS:
        cur = snap.get(metric)
        if cur is None:
            return None
        if op == "gt":
            if threshold is not None and cur > threshold:
                return _metric_note(handle, label, metric, username,
                                    f"đang là {_fmt(cur)} (vượt ngưỡng {_fmt(threshold)})",
                                    "warn", cur)
            return None
        if op == "lt":
            if threshold is not None and cur < threshold:
                return _metric_note(handle, label, metric, username,
                                    f"đang là {_fmt(cur)} (dưới ngưỡng {_fmt(threshold)})",
                                    "warn", cur)
            return None
        if op == "change_pct_gt":
            old = prev.get(metric)
            if old in (None, 0) or threshold is None:
                return None
            pct = (cur - old) / abs(old) * 100.0
            if abs(pct) >= threshold:
                direction = "tăng" if pct >= 0 else "giảm"
                return _metric_note(
                    handle, label, metric, username,
                    f"{direction} {abs(pct):.1f}% ({_fmt(old)} → {_fmt(cur)})",
                    "warn" if pct < 0 else "success", cur, pct=pct, prev=old,
                )
            return None
    return None


def _metric_note(handle, label, metric, username, detail, level, value,
                 pct=None, prev=None) -> Dict[str, Any]:
    return {
        "title": f"{handle}: {label} {detail.split(' (')[0]}",
        "body": f"{label} {detail}.",
        "level": level,
        "data": {"username": username, "metric": metric, "value": value,
                 "pct": pct, "prev": prev},
    }
