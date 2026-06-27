from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urlparse, parse_qs, urlencode

from src.scrapers.base import BaseScraper
from src.utils.logger import get_logger
from src.scrapers.video.profile_videos import (
    ProfileVideosScraper,
    _STATE_PATH,
    _PROFILE_DIR,
)

log = get_logger(__name__)

_UNIVERSAL_DATA_RE = re.compile(
    r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
    re.DOTALL,
)
_ID_RE = re.compile(r"/(?:video|photo)/(\d+)")


def _to_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _iso(create_time) -> Optional[str]:
    if not create_time:
        return None
    try:
        return (
            datetime.fromtimestamp(int(create_time), tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except (ValueError, OSError):
        return None


class VideoDetailScraper(BaseScraper):
    """
    Scrape a single TikTok video by URL: full performance stats (from the page's
    embedded JSON) plus its comments.

    The video detail is read from ``__UNIVERSAL_DATA_FOR_REHYDRATION__``. Comments
    are not auto-loaded on a directly-opened video page, so we borrow the common
    web params from any signed ``item_list`` request the page fires, build the
    ``/api/comment/list`` request ourselves, and page through it via in-page
    fetch (TikTok serves it for same-origin requests with the logged-in session).
    """

    def scrape(self, video_url: str, max_comments: int = 500) -> dict:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return self.fail(
                "Playwright is not installed. Run: pip install playwright "
                "&& python -m playwright install chromium"
            )

        m = _ID_RE.search(video_url or "")
        if not m:
            return self.fail(
                "Link video không hợp lệ. Dán link dạng "
                "https://www.tiktok.com/@user/video/123..."
            )
        aweme_id = m.group(1)

        try:
            from playwright_stealth import Stealth

            playwright_cm = Stealth().use_sync(sync_playwright())
        except ImportError:
            playwright_cm = sync_playwright()

        captured: dict[str, str] = {}

        def handle_request(request):
            u = request.url
            if "/api/" not in u:
                return
            # Prefer an item_list request (same param shape); fall back to any
            # signed web API request.
            if "item_list" in u and "item_list" not in captured:
                captured["item_list"] = u
            elif "X-Bogus" in u and "any" not in captured:
                captured["any"] = u

        use_state = _STATE_PATH.exists()

        try:
            with playwright_cm as p:
                browser = None
                if use_state:
                    browser = p.chromium.launch(
                        headless=False,
                        args=[
                            "--disable-blink-features=AutomationControlled",
                            "--window-position=-32000,-32000",
                            "--window-size=1280,900",
                        ],
                    )
                    context = browser.new_context(
                        storage_state=str(_STATE_PATH),
                        user_agent=self.settings.user_agent,
                        locale="en-US",
                        viewport={"width": 1280, "height": 900},
                    )
                    wait_s = 25
                else:
                    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=str(_PROFILE_DIR),
                        headless=False,
                        user_agent=self.settings.user_agent,
                        locale="en-US",
                        viewport={"width": 1280, "height": 900},
                        args=["--disable-blink-features=AutomationControlled"],
                    )
                    wait_s = 180

                page = context.pages[0] if context.pages else context.new_page()
                page.on("request", handle_request)

                log.info("Opening video %s", video_url)
                page.goto(video_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(5000)

                video = self._parse_video(page.content(), aweme_id, video_url)

                # Wait for a usable signed request to borrow params from.
                base_url = self._wait_for_base(page, captured, wait_s)
                comments: List[dict] = []
                comment_total = video.get("comments", 0)
                if base_url:
                    comments = self._collect_comments(
                        page, base_url, aweme_id, max_comments
                    )
                else:
                    log.warning("No signed request captured; cannot load comments.")

                context.close()
                if browser:
                    browser.close()

            if not video.get("video_id"):
                return self.fail(
                    "Không đọc được dữ liệu video (link sai, video riêng tư, "
                    "hoặc phiên đăng nhập đã hết hạn).",
                    data={"video": {"video_url": video_url}},
                )

            return self.ok(
                {
                    "video": video,
                    "comments": comments,
                    "comment_count": len(comments),
                    "comment_total": comment_total,
                }
            )
        except Exception as e:
            log.exception("Video detail scraping failed")
            return self.fail(str(e), data={"video": {"video_url": video_url}})

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _wait_for_base(page, captured: dict, timeout_s: int) -> Optional[str]:
        waited, step = 0, 1000
        while waited < timeout_s * 1000:
            if "item_list" in captured:
                return captured["item_list"]
            if "any" in captured:
                return captured["any"]
            page.wait_for_timeout(step)
            waited += step
        return captured.get("item_list") or captured.get("any")

    def _collect_comments(
        self, page, base_url: str, aweme_id: str, max_comments: int
    ) -> List[dict]:
        query = parse_qs(urlparse(base_url).query)
        base_params = {k: v[0] for k, v in query.items()}
        # Drop signature + endpoint-specific params; keep the shared web params.
        for junk in (
            "X-Bogus", "X-Gnarly", "msToken", "cursor", "count",
            "challengeID", "secUid", "id", "from_page",
            "aweme_id", "item_id", "comment_id",
        ):
            base_params.pop(junk, None)

        params = dict(base_params)
        params["aweme_id"] = aweme_id
        params["count"] = "20"

        collected: dict[str, dict] = {}
        cursor = "0"
        seen: set[str] = set()
        for _ in range(200):
            if cursor in seen:
                break
            seen.add(cursor)

            params["cursor"] = cursor
            path = "/api/comment/list/?" + urlencode(params)
            payload = ProfileVideosScraper._fetch_in_page(page, path)
            if not payload:
                break

            for c in payload.get("comments", []) or []:
                parsed = self._parse_comment(c)
                if parsed:
                    collected[parsed["cid"]] = parsed

            log.info("Comments fetched: %d (cursor=%s)", len(collected), cursor)

            if len(collected) >= max_comments:
                break
            if not payload.get("has_more"):
                break
            cursor = str(payload.get("cursor", ""))
            if not cursor or cursor == "0":
                break
            page.wait_for_timeout(400)

        ordered = sorted(
            collected.values(), key=lambda c: c.get("likes", 0), reverse=True
        )[:max_comments]

        # Pull reply threads for the most-liked comments, bounded so we don't
        # balloon the scrape time on videos with thousands of replies.
        self._attach_replies(page, base_params, aweme_id, ordered)
        return ordered

    def _attach_replies(
        self,
        page,
        base_params: dict,
        aweme_id: str,
        comments: List[dict],
        max_total: int = 1000,
        per_comment: int = 30,
    ) -> None:
        fetched = 0
        for c in comments:
            if fetched >= max_total:
                break
            if c.get("replies", 0) <= 0:
                continue
            replies = self._collect_replies(
                page, base_params, aweme_id, c["cid"],
                cap=min(per_comment, max_total - fetched),
            )
            if replies:
                c["reply_list"] = replies
                fetched += len(replies)

    def _collect_replies(
        self, page, base_params: dict, aweme_id: str, comment_id: str, cap: int
    ) -> List[dict]:
        params = dict(base_params)
        params["item_id"] = aweme_id
        params["comment_id"] = comment_id
        params["count"] = "20"

        out: dict[str, dict] = {}
        cursor = "0"
        seen: set[str] = set()
        for _ in range(20):
            if cursor in seen:
                break
            seen.add(cursor)

            params["cursor"] = cursor
            path = "/api/comment/list/reply/?" + urlencode(params)
            payload = ProfileVideosScraper._fetch_in_page(page, path)
            if not payload:
                break

            for c in payload.get("comments", []) or []:
                parsed = self._parse_comment(c)
                if parsed:
                    out[parsed["cid"]] = parsed

            if len(out) >= cap or not payload.get("has_more"):
                break
            cursor = str(payload.get("cursor", ""))
            if not cursor or cursor == "0":
                break
            page.wait_for_timeout(300)

        ordered = sorted(out.values(), key=lambda c: c.get("likes", 0), reverse=True)
        return ordered[:cap]

    @staticmethod
    def _parse_comment(c: dict) -> Optional[dict]:
        cid = c.get("cid")
        if not cid:
            return None
        user = c.get("user") or {}
        return {
            "cid": str(cid),
            "text": c.get("text", ""),
            "likes": _to_int(c.get("digg_count")),
            "user": user.get("unique_id") or user.get("uniqueId") or "",
            "nickname": user.get("nickname", ""),
            "created_at": _iso(c.get("create_time")),
            "replies": _to_int(c.get("reply_comment_total")),
        }

    @staticmethod
    def _parse_video(html: str, aweme_id: str, video_url: str) -> dict:
        out = {"video_id": "", "video_url": video_url}
        m = _UNIVERSAL_DATA_RE.search(html)
        if not m:
            return out
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            return out

        scope = data.get("__DEFAULT_SCOPE__", {})
        detail = scope.get("webapp.video-detail", {})
        item = (detail.get("itemInfo") or {}).get("itemStruct") or {}
        if not item:
            return out

        stats = item.get("stats") or item.get("statsV2") or {}
        author = item.get("author") or {}
        music = item.get("music") or {}
        vid = item.get("video") or {}

        hashtags = [
            t.get("hashtagName")
            for t in (item.get("textExtra") or [])
            if t.get("hashtagName")
        ]
        if not hashtags:
            hashtags = [
                c.get("title") for c in (item.get("challenges") or []) if c.get("title")
            ]

        return {
            "video_id": str(item.get("id") or aweme_id),
            "video_url": video_url,
            "description": item.get("desc", ""),
            "author": author.get("uniqueId", ""),
            "author_nickname": author.get("nickname", ""),
            "author_verified": bool(author.get("verified", False)),
            "posted_at": _iso(item.get("createTime")),
            "duration": _to_int(vid.get("duration")),
            "music_title": music.get("title", ""),
            "music_author": music.get("authorName", ""),
            "hashtags": hashtags,
            "cover": vid.get("cover") or vid.get("dynamicCover") or "",
            "views": _to_int(stats.get("playCount")),
            "likes": _to_int(stats.get("diggCount")),
            "comments": _to_int(stats.get("commentCount")),
            "shares": _to_int(stats.get("shareCount")),
            "saves": _to_int(stats.get("collectCount")),
        }
