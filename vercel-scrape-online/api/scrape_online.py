"""Vercel serverless function — "Scrape online" (HTTP-only TikTok profile).

Self-contained, dependency-free (Python stdlib only). One HTTP GET of the public
profile page, parse the embedded __UNIVERSAL_DATA_FOR_REHYDRATION__ JSON, return
normalised profile stats. No browser, no queue, no DB.

  GET /api/scrape_online?username=<handle>

Scope note: this returns the PROFILE only. TikTok's video-list API
(/api/post/item_list) requires a browser-signed request (X-Bogus/_signature) and
returns an empty body to unsigned HTTP calls, and the profile page does not embed
the video grid — so the full video list is not obtainable over plain HTTP. Use
the browser-based "Tài khoản" tool (offline) for the complete video analysis.

Reality check: HTTP-only works only where TikTok doesn't serve its anti-bot wall
(varies by egress IP). Set PROXY_URL (residential) to improve reliability.
"""

from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import urllib.request
import http.cookiejar
import gzip
import zlib
import json
import os
import re

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_HEADERS = {
    "User-Agent": _UA,
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,image/apng,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}
_DATA_RE = re.compile(
    r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
    re.DOTALL,
)
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,24}$")
_TIMEOUT = 12


def _decode(resp) -> str:
    raw = resp.read()
    enc = (resp.headers.get("Content-Encoding") or "").lower()
    try:
        if "gzip" in enc:
            raw = gzip.decompress(raw)
        elif "deflate" in enc:
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    except Exception:
        pass
    return raw.decode("utf-8", "replace")


def _opener():
    handlers = [urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())]
    proxy = os.environ.get("PROXY_URL")
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def _fetch(url: str) -> str:
    opener = _opener()
    last = ""
    for _ in range(2):
        req = urllib.request.Request(url, headers=_HEADERS)
        with opener.open(req, timeout=_TIMEOUT) as resp:
            last = _decode(resp)
        if "__UNIVERSAL_DATA_FOR_REHYDRATION__" in last:
            return last
    return last


def _err(username: str, message: str) -> dict:
    return {
        "platform": "tiktok", "scraper_type": "tiktok online scraper",
        "status": "error", "method": "http",
        "error": {"message": message}, "profile": {"username": username},
    }


def scrape_profile(username: str) -> dict:
    username = (username or "").lstrip("@").strip()
    if not _USERNAME_RE.match(username):
        return _err(username, "Username không hợp lệ (chỉ chữ, số, dấu chấm/gạch dưới).")

    url = "https://www.tiktok.com/@" + username
    try:
        html = _fetch(url)
    except Exception as e:
        return _err(username, "Không tải được trang qua HTTP (%s). "
                              "Cấu hình PROXY_URL (proxy residential)." % e)

    m = _DATA_RE.search(html)
    if not m:
        return _err(username,
                    "TikTok đã chặn truy cập HTTP (trang chống bot) — thường gặp khi "
                    "gọi từ IP datacenter. Hãy đặt PROXY_URL (residential).")
    try:
        data = json.loads(m.group(1))
        info = data["__DEFAULT_SCOPE__"]["webapp.user-detail"]["userInfo"]
    except Exception:
        return _err(username, "Tài khoản riêng tư/không tồn tại, hoặc layout đã đổi.")

    user = info.get("user", {}) or {}
    stats = info.get("stats", {}) or info.get("statsV2", {}) or {}
    uid = user.get("uniqueId") or username
    profile = {
        "username": uid,
        "profile_url": "https://www.tiktok.com/@" + uid,
        "nickname": user.get("nickname", ""),
        "signature": user.get("signature", ""),
        "avatar_url": (user.get("avatarLarger") or user.get("avatarMedium")
                       or user.get("avatarThumb") or ""),
        "followers_count": int(stats.get("followerCount", 0) or 0),
        "following_count": int(stats.get("followingCount", 0) or 0),
        "total_likes": int(stats.get("heartCount", stats.get("heart", 0)) or 0),
        "video_count": int(stats.get("videoCount", 0) or 0),
        "is_verified": bool(user.get("verified", False)),
    }
    return {
        "platform": "tiktok", "scraper_type": "tiktok online scraper",
        "status": "success", "method": "http", "profile": profile,
    }


class handler(BaseHTTPRequestHandler):
    """Vercel Python entry point."""

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        qs = parse_qs(urlparse(self.path).query)
        username = (qs.get("username") or [""])[0]
        self._send(200, scrape_profile(username))

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()
