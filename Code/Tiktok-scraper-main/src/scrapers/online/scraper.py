"""Browserless, HTTP-only TikTok profile scraper — the "Scrape online" tool.

This module is intentionally **isolated** from the rest of the system: it never
launches Playwright, never touches the job queue / worker / scheduler / DB, and
has no background threads. It just does one plain HTTP GET and parses the public
profile page. That makes it the one piece of the app that can run on a serverless
or browserless host (Vercel, a tiny VPS, a container with no display).

It reuses only the pure ``parse_profile_from_html`` helper so the parsed shape
stays identical to the browser scraper — without importing any browser code.

Trade-off (by design): TikTok frequently serves an anti-bot interstitial to bare
HTTP clients, especially from datacenter IPs, so this can fail where the headed
browser scraper succeeds. Set HTTP_PROXY / HTTPS_PROXY (ideally residential) to
improve reliability.
"""

from __future__ import annotations

from typing import Optional

import requests

from src.config.settings import Settings
from src.scrapers.profile.scraper import parse_profile_from_html
from src.utils.validation import validate_username
from src.utils.logger import get_logger

log = get_logger(__name__)

# Fuller browser-like headers than the shared HttpClient — TikTok is picky and
# these client hints meaningfully raise the odds of getting real HTML back.
_BROWSER_HEADERS = {
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,image/apng,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


class OnlineProfileScraper:
    """HTTP-only TikTok profile scraper. No Playwright, no shared state."""

    platform = "tiktok"
    scraper_type = "tiktok online scraper"

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or Settings()

    def scrape(self, username: str) -> dict:
        try:
            username = validate_username(username)
        except Exception as e:
            return self._fail(username, f"Username không hợp lệ: {e}")

        url = f"https://www.tiktok.com/@{username}"
        try:
            html = self._fetch(url)
        except requests.RequestException as e:
            return self._fail(
                username,
                f"Không tải được trang qua HTTP ({e}). "
                "Nếu lặp lại, hãy cấu hình proxy (HTTP_PROXY/HTTPS_PROXY).",
            )

        try:
            profile = parse_profile_from_html(html, username)
        except Exception as e:
            return self._fail(
                username,
                "TikTok có thể đã chặn truy cập HTTP (trang chống bot) hoặc tài khoản "
                f"riêng tư/không tồn tại — {e}. "
                "Mẹo: dùng proxy residential, hoặc dùng tool quét bằng trình duyệt.",
            )

        log.info("Online (HTTP) profile scrape OK for @%s", username)
        return {
            "platform": self.platform,
            "scraper_type": self.scraper_type,
            "status": "success",
            "method": "http",
            "profile": profile.model_dump(),
        }

    def _fetch(self, url: str) -> str:
        """GET the profile page. Uses a session and one retry: TikTok's first
        response is often an anti-bot interstitial that sets a cookie, and the
        retry on the same session occasionally returns the real HTML."""
        headers = {"User-Agent": self.settings.user_agent, **_BROWSER_HEADERS}
        session = requests.Session()
        last = ""
        for _ in range(2):
            resp = session.get(
                url,
                headers=headers,
                timeout=self.settings.timeout_s,
                proxies=self.settings.proxies,
                allow_redirects=True,
            )
            resp.raise_for_status()
            last = resp.text
            if "__UNIVERSAL_DATA_FOR_REHYDRATION__" in last:
                return last
        return last

    def _fail(self, username: str, message: str) -> dict:
        return {
            "platform": self.platform,
            "scraper_type": self.scraper_type,
            "status": "error",
            "method": "http",
            "error": {"message": message},
            "profile": {"username": username},
        }
