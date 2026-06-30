"""Vercel serverless function — "Scrape online" (HTTP-only TikTok scraper).

Self-contained, dependency-free (Python stdlib only). Two stages, both over plain
HTTP with a shared cookie jar (no browser, no signing):

  1. Profile: GET the public profile page, parse the embedded
     __UNIVERSAL_DATA_FOR_REHYDRATION__ JSON → profile stats + secUid.
  2. Videos: replay TikTok's own /api/post/item_list/ endpoint unsigned, reusing
     the cookies (incl. msToken) set by step 1, paginating by cursor until the
     time budget runs out. This is best-effort — TikTok may reject unsigned
     calls; if so we still return the profile (graceful degradation).

  GET /api/scrape_online?username=<handle>&max=<n>   (max=0 → all, time-bounded)

Reality check: HTTP-only works only where TikTok doesn't serve its anti-bot wall
(varies by egress IP). Set PROXY_URL (residential) to improve reliability.
"""

from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode
from datetime import datetime, timezone
import urllib.request
import http.cookiejar
import gzip
import zlib
import json
import os
import re
import time

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
_TIME_BUDGET = 50          # seconds — stay under the function's maxDuration
_MAX_PAGES = 60
_ITEM_API = "https://www.tiktok.com/api/post/item_list/"
_ITEM_PARAMS = {
    "aid": "1988", "app_language": "en", "app_name": "tiktok_web",
    "browser_language": "en-US", "browser_name": "Mozilla",
    "browser_online": "true", "browser_platform": "Win32",
    "browser_version": "5.0 (Windows NT 10.0; Win64; x64)",
    "channel": "tiktok_web", "cookie_enabled": "true", "count": "35",
    "device_platform": "web_pc", "history_len": "3", "is_fullscreen": "false",
    "is_page_visible": "true", "language": "en", "os": "windows",
    "priority_region": "", "referer": "", "region": "US",
    "screen_height": "1080", "screen_width": "1920",
    "tz_name": "America/New_York", "device_id": "7000000000000000000",
}


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


def _fetch_html(opener, url: str) -> str:
    last = ""
    for _ in range(2):
        req = urllib.request.Request(url, headers=_HEADERS)
        with opener.open(req, timeout=_TIMEOUT) as resp:
            last = _decode(resp)
        if "__UNIVERSAL_DATA_FOR_REHYDRATION__" in last:
            return last
    return last


def _parse_video(item: dict) -> dict:
    vid = item.get("id")
    if not vid:
        return None
    stats = item.get("stats") or item.get("statsV2") or {}
    author = (item.get("author") or {}).get("uniqueId") or ""
    hashtags = [t.get("hashtagName") for t in (item.get("textExtra") or [])
                if t.get("hashtagName")]
    posted_at = None
    ct = item.get("createTime")
    if ct:
        try:
            posted_at = (datetime.fromtimestamp(int(ct), tz=timezone.utc)
                         .isoformat().replace("+00:00", "Z"))
        except (ValueError, OSError, TypeError):
            posted_at = None

    def _i(key):
        try:
            return int(stats.get(key, 0) or 0)
        except (ValueError, TypeError):
            return 0

    return {
        "video_id": str(vid),
        "description": item.get("desc", ""),
        "author": author,
        "hashtags": hashtags,
        "likes": _i("diggCount"),
        "comments": _i("commentCount"),
        "shares": _i("shareCount"),
        "views": _i("playCount"),
        "saves": _i("collectCount"),
        "posted_at": posted_at,
        "video_url": ("https://www.tiktok.com/@%s/video/%s" % (author, vid)
                      if author else ""),
    }


def _fetch_videos(opener, sec_uid: str, profile_url: str, max_videos: int) -> list:
    """Paginate the (unsigned) item_list API, reusing the profile-page cookies."""
    headers = dict(_HEADERS)
    headers["Accept"] = "application/json, text/plain, */*"
    headers["Referer"] = profile_url
    headers["Sec-Fetch-Dest"] = "empty"
    headers["Sec-Fetch-Mode"] = "cors"
    headers["Sec-Fetch-Site"] = "same-origin"
    headers["X-Requested-With"] = "XMLHttpRequest"

    videos, seen = [], set()
    cursor = "0"
    deadline = time.time() + _TIME_BUDGET
    for _ in range(_MAX_PAGES):
        if time.time() > deadline:
            break
        params = dict(_ITEM_PARAMS)
        params["secUid"] = sec_uid
        params["cursor"] = cursor
        params["referer"] = profile_url
        url = _ITEM_API + "?" + urlencode(params)
        try:
            req = urllib.request.Request(url, headers=headers)
            with opener.open(req, timeout=_TIMEOUT) as resp:
                payload = json.loads(_decode(resp) or "{}")
        except Exception:
            break
        items = payload.get("itemList") or []
        for it in items:
            v = _parse_video(it)
            if v and v["video_id"] not in seen:
                seen.add(v["video_id"])
                videos.append(v)
        if max_videos and len(videos) >= max_videos:
            break
        if not payload.get("hasMore"):
            break
        nxt = str(payload.get("cursor") or "")
        if not nxt or nxt == cursor:
            break
        cursor = nxt
    videos.sort(key=lambda v: v.get("posted_at") or "", reverse=True)
    return videos[:max_videos] if max_videos else videos


def _err(username: str, message: str) -> dict:
    return {
        "platform": "tiktok", "scraper_type": "tiktok online scraper",
        "status": "error", "method": "http",
        "error": {"message": message}, "profile": {"username": username},
        "videos": [], "video_count": 0,
    }


def scrape_profile(username: str, max_videos: int = 0) -> dict:
    username = (username or "").lstrip("@").strip()
    if not _USERNAME_RE.match(username):
        return _err(username, "Username không hợp lệ (chỉ chữ, số, dấu chấm/gạch dưới).")

    url = "https://www.tiktok.com/@" + username
    opener = _opener()
    try:
        html = _fetch_html(opener, url)
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
    sec_uid = user.get("secUid") or ""
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

    videos = []
    if sec_uid:
        try:
            videos = _fetch_videos(opener, sec_uid, url, max_videos)
        except Exception:
            videos = []

    return {
        "platform": "tiktok", "scraper_type": "tiktok online scraper",
        "status": "success", "method": "http",
        "profile": profile, "videos": videos, "video_count": len(videos),
    }


def _diagnose(username: str) -> dict:
    """Temporary diagnostic: what does the profile page embed, and what does the
    item_list endpoint actually return? Used to decide the video strategy."""
    username = (username or "").lstrip("@").strip()
    url = "https://www.tiktok.com/@" + username
    out = {"username": username}
    opener = _opener()
    try:
        html = _fetch_html(opener, url)
    except Exception as e:
        return {"error": "fetch_html: %s" % e}
    out["html_len"] = len(html)
    m = _DATA_RE.search(html)
    out["has_embedded"] = bool(m)
    sec_uid = ""
    if m:
        try:
            data = json.loads(m.group(1))
            scope = data.get("__DEFAULT_SCOPE__", {})
            out["scope_keys"] = list(scope.keys())
            ud = scope.get("webapp.user-detail", {})
            out["user_detail_keys"] = list(ud.keys())
            sec_uid = ((ud.get("userInfo") or {}).get("user") or {}).get("secUid", "")
            out["has_secUid"] = bool(sec_uid)
            # any embedded items anywhere?
            blob = m.group(1)
            for k in ("itemList", "ItemList", "ItemModule", "post"):
                out["embeds_%s" % k] = (('"%s"' % k) in blob)

            # recursively hunt for an itemList array with real video items
            found = {"max_len": 0, "sample_keys": None, "paths": []}

            def walk(node, path):
                if isinstance(node, dict):
                    for kk, vv in node.items():
                        if kk in ("itemList", "items") and isinstance(vv, list) and vv:
                            found["paths"].append(path + "." + kk + "[%d]" % len(vv))
                            if len(vv) > found["max_len"]:
                                found["max_len"] = len(vv)
                                it0 = vv[0] if isinstance(vv[0], dict) else {}
                                found["sample_keys"] = list(it0.keys())[:12]
                        walk(vv, path + "." + kk)
                elif isinstance(node, list):
                    for i, vv in enumerate(node[:50]):
                        walk(vv, path + "[%d]" % i)

            walk(data, "")
            out["embedded_items_max_len"] = found["max_len"]
            out["embedded_items_paths"] = found["paths"][:5]
            out["embedded_item_keys"] = found["sample_keys"]
            # also check the full HTML for other SSR script blobs
            out["html_has_SIGI"] = ("SIGI_STATE" in html)
            out["html_has_NEXT"] = ("__NEXT_DATA__" in html)
        except Exception as e:
            out["parse_err"] = str(e)
    # probe item_list raw
    if sec_uid:
        h = dict(_HEADERS)
        h.update({"Accept": "application/json, text/plain, */*", "Referer": url,
                  "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors",
                  "Sec-Fetch-Site": "same-origin", "X-Requested-With": "XMLHttpRequest"})
        p = dict(_ITEM_PARAMS); p["secUid"] = sec_uid; p["cursor"] = "0"; p["referer"] = url
        try:
            req = urllib.request.Request(_ITEM_API + "?" + urlencode(p), headers=h)
            with opener.open(req, timeout=_TIMEOUT) as resp:
                body = _decode(resp)
                out["itemlist_http"] = resp.status
                out["itemlist_len"] = len(body)
                out["itemlist_head"] = body[:200]
                try:
                    j = json.loads(body or "{}")
                    out["itemlist_statusCode"] = j.get("statusCode")
                    out["itemlist_count"] = len(j.get("itemList") or [])
                    out["itemlist_hasMore"] = j.get("hasMore")
                except Exception as e:
                    out["itemlist_json_err"] = str(e)
        except Exception as e:
            out["itemlist_err"] = "%s: %s" % (type(e).__name__, e)
    return out


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
        if (qs.get("debug") or [""])[0] == "1":
            self._send(200, _diagnose(username))
            return
        try:
            max_videos = int((qs.get("max") or ["0"])[0])
        except ValueError:
            max_videos = 0
        self._send(200, scrape_profile(username, max_videos))

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()
