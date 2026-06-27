from __future__ import annotations

import json
import re

from src.scrapers.base import BaseScraper
from src.utils.validation import validate_username
from src.utils.logger import get_logger
from src.scrapers.profile.models import ProfileData

log = get_logger(__name__)

# TikTok embeds page data inside this script tag.
_UNIVERSAL_DATA_RE = re.compile(
    r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
    re.DOTALL,
)


def extract_user_info(html: str) -> dict:
    """Pull the userInfo blob out of a TikTok profile page's embedded JSON."""
    match = _UNIVERSAL_DATA_RE.search(html)
    if not match:
        raise ValueError(
            "Could not locate embedded profile data "
            "(page layout changed or request was blocked)."
        )

    data = json.loads(match.group(1))
    scope = data.get("__DEFAULT_SCOPE__", {})
    detail = scope.get("webapp.user-detail", {})
    user_info = detail.get("userInfo")

    if not user_info:
        status = detail.get("statusMsg") or "user not found or private"
        raise ValueError(f"No profile data returned ({status}).")

    return user_info


def parse_profile_from_html(html: str, username: str) -> ProfileData:
    """Build a ProfileData from a profile page's HTML (embedded JSON)."""
    user_info = extract_user_info(html)
    user = user_info.get("user", {})
    stats = user_info.get("stats", {}) or user_info.get("statsV2", {})
    unique_id = user.get("uniqueId") or username

    return ProfileData(
        username=unique_id,
        profile_url=f"https://www.tiktok.com/@{unique_id}",
        nickname=user.get("nickname", ""),
        signature=user.get("signature", ""),
        followers_count=int(stats.get("followerCount", 0)),
        following_count=int(stats.get("followingCount", 0)),
        total_likes=int(stats.get("heartCount", stats.get("heart", 0))),
        video_count=int(stats.get("videoCount", 0)),
        is_verified=bool(user.get("verified", False)),
    )


class ProfileScraper(BaseScraper):
    """
    TikTok profile scraper.

    Fetches the public profile page and extracts the stats embedded in the
    page's __UNIVERSAL_DATA_FOR_REHYDRATION__ JSON blob. TikTok increasingly
    serves a "please wait" anti-bot interstitial to plain HTTP clients, so on
    failure we fall back to the logged-in browser session (same one the video
    scraper uses), which passes TikTok's checks.
    """

    def scrape(self, username: str) -> dict:
        try:
            username = validate_username(username)
            profile_url = f"https://www.tiktok.com/@{username}"

            try:
                html = self.http.get_text(profile_url)
                profile = parse_profile_from_html(html, username)
            except Exception as http_err:
                log.info(
                    "HTTP profile fetch failed (%s); trying browser fallback.",
                    http_err,
                )
                from src.scrapers.video.profile_videos import fetch_html_via_browser

                html = fetch_html_via_browser(profile_url, self.settings)
                if not html:
                    raise http_err
                profile = parse_profile_from_html(html, username)

            return self.ok({"profile": profile.model_dump()})
        except Exception as e:
            log.exception("Profile scraping failed")
            return self.fail(str(e), data={"profile": {"username": username}})
