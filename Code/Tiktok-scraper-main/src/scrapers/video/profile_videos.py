from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


def _day_to_ts(day: str, end_of_day: bool = False) -> Optional[int]:
    """Convert a 'YYYY-MM-DD' string to a UTC unix timestamp.

    ``end_of_day`` pushes it to 23:59:59 so a 'to' date is inclusive.
    Returns None for empty/invalid input.
    """
    if not day:
        return None
    try:
        dt = datetime.strptime(day.strip()[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.timestamp())

from src.scrapers.base import BaseScraper
from src.utils.validation import validate_username
from src.utils.logger import get_logger
from src.scrapers.video.models import VideoData
from src.scrapers.profile.scraper import parse_profile_from_html

log = get_logger(__name__)

# Where the persistent browser profile (cookies / solved-captcha session) lives.
_PROFILE_DIR = Path(__file__).resolve().parents[3] / ".browser_profile"


def _find_state_file() -> Path:
    """Locate tiktok_state.json by walking up from this file.

    extract_cookies.py writes it at the outer project root, so search a few
    parent levels and fall back to the package root.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "tiktok_state.json"
        if candidate.exists():
            return candidate
    return here.parents[3] / "tiktok_state.json"


# Logged-in session exported from a real browser (see extract_cookies.py).
# When present, scraping runs fully headless with no CAPTCHA.
_STATE_PATH = _find_state_file()


def fetch_html_via_browser(url: str, settings, wait_ms: int = 4000) -> Optional[str]:
    """Load a TikTok page in a real (off-screen) browser and return its HTML.

    Used as a fallback for endpoints that TikTok blocks over plain HTTP (it
    serves a "please wait" anti-bot page to bare requests). Requires a saved
    login session (tiktok_state.json); returns None if unavailable.
    """
    if not _STATE_PATH.exists():
        return None
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        from playwright_stealth import Stealth

        cm = Stealth().use_sync(sync_playwright())
    except ImportError:
        cm = sync_playwright()

    with cm as p:
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
            user_agent=settings.user_agent,
            locale="en-US",
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(wait_ms)
            return page.content()
        finally:
            context.close()
            browser.close()


class ProfileVideosScraper(BaseScraper):
    """
    Scrapes the list of videos posted by a TikTok profile.

    TikTok guards the video grid behind a slider CAPTCHA / login wall, so a
    real (non-headless) Chromium window is used with a *persistent* profile.
    On the first run you solve the CAPTCHA (or log in) once in the window that
    opens; the session is saved to ``.browser_profile`` and reused afterwards.

    Videos are collected by intercepting TikTok's own signed ``item_list``
    API responses as the page is scrolled.
    """

    def scrape(
        self,
        username: str,
        max_videos: Optional[int] = None,
        max_scroll_rounds: int = 80,
        headless: bool = False,
        unlock_timeout_s: int = 240,
        date_from: str = "",
        date_to: str = "",
    ) -> dict:
        try:
            username = validate_username(username)
            profile_url = f"https://www.tiktok.com/@{username}"
            videos, profile_info = self._run_collection(
                page_url=profile_url,
                api_match="/api/post/item_list",
                endpoint_path="/api/post/item_list/",
                username=username,
                extract_profile=True,
                max_videos=max_videos,
                max_scroll_rounds=max_scroll_rounds,
                headless=headless,
                unlock_timeout_s=unlock_timeout_s,
                date_from=date_from,
                date_to=date_to,
            )
            return self.ok(
                {
                    "profile": (
                        profile_info.model_dump()
                        if profile_info
                        else {"username": username, "profile_url": profile_url}
                    ),
                    "video_count": len(videos),
                    "videos": [v.model_dump() for v in videos],
                }
            )
        except Exception as e:
            log.exception("Profile video scraping failed")
            return self.fail(str(e), data={"profile": {"username": username}})

    def _run_collection(
        self,
        *,
        page_url: str,
        api_match: str,
        endpoint_path: str,
        username: str = "",
        extract_profile: bool = False,
        count: str = "35",
        max_videos: Optional[int] = None,
        max_scroll_rounds: int = 80,
        headless: bool = False,
        unlock_timeout_s: int = 240,
        date_from: str = "",
        date_to: str = "",
    ):
        """Open a TikTok feed page and collect its videos via the signed
        ``item_list`` API, replayed page-by-page from inside the page.

        Shared by the profile (``/api/post/item_list``) and hashtag
        (``/api/challenge/item_list``) scrapers. Returns
        ``(videos, profile_info)`` where ``profile_info`` is only populated
        when ``extract_profile`` is set. Raises on hard failures (Playwright
        missing, grid never loaded) so callers can report them.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright is not installed. Run: pip install playwright "
                "&& python -m playwright install chromium"
            )

        # Stealth evasions help the browser pass TikTok's bot checks.
        try:
            from playwright_stealth import Stealth

            playwright_cm = Stealth().use_sync(sync_playwright())
        except ImportError:
            playwright_cm = sync_playwright()

        collected: dict[str, VideoData] = {}
        captured: dict[str, str] = {}
        profile_info = None

        def handle_request(request):
            # Capture the first signed item_list request so we can replay the
            # pagination ourselves via in-page fetch (TikTok re-signs it).
            if api_match in request.url and "url" not in captured:
                captured["url"] = request.url

        use_state = _STATE_PATH.exists()

        with playwright_cm as p:
            browser = None
            if use_state:
                # Logged-in run with a REAL (non-headless) browser placed
                # off-screen: passes TikTok's headless checks, the login
                # session avoids the CAPTCHA, and no window is visible.
                log.info("Using saved login session (off-screen window).")
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
                grid_timeout = 45
            else:
                # No session yet: open a real window so the user can solve
                # the CAPTCHA once; the session persists in .browser_profile.
                _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(_PROFILE_DIR),
                    headless=headless,
                    user_agent=self.settings.user_agent,
                    locale="en-US",
                    viewport={"width": 1280, "height": 900},
                    args=["--disable-blink-features=AutomationControlled"],
                )
                grid_timeout = unlock_timeout_s

            # Note: navigator.webdriver masking is handled by playwright-stealth.
            page = context.pages[0] if context.pages else context.new_page()
            page.on("request", handle_request)

            log.info("Opening %s", page_url)
            page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
            # Let the page settle before touching it — scrolling too soon
            # triggers TikTok's behavioural bot checks.
            page.wait_for_timeout(5000)

            # Wait for the page to fire its first (signed) item_list request.
            # In CAPTCHA mode this only happens once the user unlocks the grid.
            if not self._wait_for_request(page, captured, grid_timeout):
                context.close()
                if browser:
                    browser.close()
                raise RuntimeError(
                    "TikTok never loaded the video list. The saved login session "
                    "may have expired — re-run extract_cookies.py to refresh "
                    "tiktok_state.json."
                    if use_state
                    else "Video grid stayed locked (CAPTCHA not solved in time). "
                    "Re-run and solve the slider puzzle in the browser window."
                )

            # Optionally grab the account stats embedded in the profile page,
            # so one browser session yields profile + videos.
            if extract_profile:
                try:
                    profile_info = parse_profile_from_html(page.content(), username)
                except Exception as ex:
                    log.info("Could not parse profile block from page: %s", ex)

            # Paginate the item_list API from inside the page: TikTok's own
            # JS re-signs each fetch, so we just vary the cursor. No scrolling,
            # which keeps us under the behavioural-bot radar.
            # Optional date window. TikTok returns videos newest-first, so once
            # we pass below `from_ts` we can stop paginating entirely — this is
            # what makes a date-range scrape light & fast instead of pulling the
            # whole back catalogue. Pinned videos can appear out of order at the
            # top, so they never trigger the early stop.
            from_ts = _day_to_ts(date_from, end_of_day=False)
            to_ts = _day_to_ts(date_to, end_of_day=True)

            base_params = self._base_params(captured["url"], count)
            cursor = "0"
            seen_cursors: set[str] = set()
            reached_old = False
            for _ in range(max_scroll_rounds):
                if cursor in seen_cursors:
                    break
                seen_cursors.add(cursor)

                path = endpoint_path + "?" + base_params + "&cursor=" + cursor
                payload = self._fetch_in_page(page, path)
                if not payload:
                    break

                for item in payload.get("itemList", []) or []:
                    ts = item.get("createTime")
                    try:
                        ts = int(ts) if ts is not None else None
                    except (TypeError, ValueError):
                        ts = None
                    if ts is not None:
                        if to_ts is not None and ts > to_ts:
                            continue  # newer than the window — skip
                        if from_ts is not None and ts < from_ts:
                            if not item.get("isPinnedItem"):
                                reached_old = True
                            continue  # older than the window — skip
                    video = self._parse_item(item, username)
                    if video:
                        collected[video.video_id] = video

                log.info("Fetched page (cursor=%s) -> %d videos total",
                         cursor, len(collected))

                if reached_old:
                    log.info("Reached videos older than %s — stopping early.", date_from)
                    break
                if max_videos and len(collected) >= max_videos:
                    break
                if not payload.get("hasMore"):
                    break
                cursor = str(payload.get("cursor", ""))
                if not cursor:
                    break
                page.wait_for_timeout(700)

            context.close()
            if browser:
                browser.close()

        videos = sorted(
            collected.values(), key=lambda v: v.posted_at or "", reverse=True
        )
        if max_videos:
            videos = videos[:max_videos]
        return videos, profile_info

    @staticmethod
    def _wait_for_request(page, captured: dict, timeout_s: int) -> bool:
        """Wait until the page fires its first item_list request."""
        deadline = timeout_s * 1000
        waited = 0
        step = 1500
        announced = False
        while waited < deadline:
            if "url" in captured:
                return True
            if not announced and timeout_s > 60:
                log.warning(
                    "Waiting for the video list — solve the CAPTCHA / log in in the "
                    "browser window if shown (up to %ds)...",
                    timeout_s,
                )
                announced = True
            page.wait_for_timeout(step)
            waited += step
        return "url" in captured

    @staticmethod
    def _base_params(url: str, count: str = "35") -> str:
        """Strip the signature params from a captured item_list URL.

        TikTok recomputes X-Bogus / X-Gnarly / msToken on each in-page fetch,
        so we drop them (and cursor/count, which we set per request). ``count``
        is endpoint-specific: post/item_list accepts 35, but challenge (hashtag)
        item_list rejects >30 with statusCode 100002, so callers override it.
        """
        from urllib.parse import urlparse, parse_qs, urlencode

        query = parse_qs(urlparse(url).query)
        params = {k: v[0] for k, v in query.items()}
        for junk in ("X-Bogus", "X-Gnarly", "msToken", "cursor"):
            params.pop(junk, None)
        params["count"] = count
        return urlencode(params)

    @staticmethod
    def _fetch_in_page(page, path: str) -> Optional[dict]:
        """Run a signed item_list fetch inside the page and return parsed JSON."""
        js = (
            "async (p) => { const r = await fetch(p, "
            "{headers: {'Accept': 'application/json'}}); "
            "return await r.text(); }"
        )
        try:
            text = page.evaluate(js, path)
            if not text:
                log.warning("In-page fetch returned empty body.")
                return None
            return json.loads(text)
        except Exception as e:
            log.warning("In-page fetch failed: %s", e)
            return None

    @staticmethod
    def _parse_item(item: dict, username: str) -> Optional[VideoData]:
        video_id = item.get("id")
        if not video_id:
            return None

        stats = item.get("stats", {}) or item.get("statsV2", {}) or {}
        author = (item.get("author") or {}).get("uniqueId") or username
        hashtags: List[str] = [
            t.get("hashtagName")
            for t in (item.get("textExtra") or [])
            if t.get("hashtagName")
        ]

        posted_at = None
        create_time = item.get("createTime")
        if create_time:
            try:
                from datetime import datetime, timezone

                posted_at = (
                    datetime.fromtimestamp(int(create_time), tz=timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
            except (ValueError, OSError):
                posted_at = None

        return VideoData(
            video_id=str(video_id),
            description=item.get("desc", ""),
            author=author,
            hashtags=hashtags,
            likes=int(stats.get("diggCount", 0)),
            comments=int(stats.get("commentCount", 0)),
            shares=int(stats.get("shareCount", 0)),
            views=int(stats.get("playCount", 0)),
            posted_at=posted_at,
            video_url=f"https://www.tiktok.com/@{author}/video/{video_id}",
        )
