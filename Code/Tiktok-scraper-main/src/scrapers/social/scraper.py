from __future__ import annotations

import html as _html
import json
import re
from typing import Optional
from urllib.parse import urlparse, parse_qs

from src.scrapers.base import BaseScraper
from src.utils.logger import get_logger

log = get_logger(__name__)


def detect_platform(url: str) -> Optional[str]:
    """Map a URL to one of the supported platforms (or None)."""
    host = urlparse((url or "").strip().lower()).netloc
    if any(h in host for h in ("youtube.com", "youtu.be")):
        return "youtube"
    if "tiktok.com" in host:
        return "tiktok"
    if any(h in host for h in ("facebook.com", "fb.watch", "fb.com")):
        return "facebook"
    if "instagram.com" in host:
        return "instagram"
    return None


def _to_int(v) -> int:
    try:
        if isinstance(v, str):
            v = re.sub(r"[^\d]", "", v) or "0"
        return int(v)
    except (TypeError, ValueError):
        return 0


def _json_after(text: str, marker: str) -> Optional[dict]:
    """Extract the first balanced {...} JSON object that follows ``marker``."""
    i = text.find(marker)
    if i < 0:
        return None
    i = text.find("{", i)
    if i < 0:
        return None
    depth, in_str, esc = 0, False, False
    for j in range(i, len(text)):
        ch = text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[i : j + 1])
                    except json.JSONDecodeError:
                        return None
    return None


def _meta_tags(html_text: str) -> dict:
    """Parse <meta property=... content=...> tags (Open Graph + description)."""
    out: dict[str, str] = {}
    for m in re.finditer(r"<meta\b[^>]*>", html_text, re.I):
        tag = m.group(0)
        key = re.search(r'(?:property|name)\s*=\s*["\']([^"\']+)["\']', tag, re.I)
        val = re.search(r'content\s*=\s*["\'](.*?)["\']', tag, re.I | re.S)
        if key and val:
            out[key.group(1).strip().lower()] = _html.unescape(val.group(1))
    return out


def _parse_human_num(s) -> int:
    """'129K' -> 129000, '2.3M' -> 2300000, '1,234' -> 1234."""
    if s is None:
        return 0
    m = re.search(r"([\d.,]+)\s*([KMB])?", str(s), re.I)
    if not m:
        return 0
    try:
        val = float(m.group(1).replace(",", ""))
    except ValueError:
        return 0
    mult = {"K": 1e3, "M": 1e6, "B": 1e9}.get((m.group(2) or "").upper(), 1)
    return int(val * mult)


def _extract_count(blob: str, labels) -> int:
    """Find 'N <label>' in text, N optionally with K/M/B (e.g. '44K reactions')."""
    for lab in labels:
        m = re.search(r"([\d][\d.,]*\s*[KMB]?)\s+" + lab + r"\b", blob, re.I)
        if m:
            return _parse_human_num(m.group(1))
    return 0


def _find_first_key(obj, key):
    """Depth-first search for the first value stored under ``key``."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find_first_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_first_key(v, key)
            if r is not None:
                return r
    return None


def _find_comment_token(data) -> Optional[str]:
    """Locate the comments-section continuation token inside ytInitialData."""
    found: list = []

    def rec(o):
        if found:
            return
        if isinstance(o, dict):
            if o.get("sectionIdentifier") == "comment-item-section":
                cc = _find_first_key(o, "continuationCommand")
                if isinstance(cc, dict) and cc.get("token"):
                    found.append(cc["token"])
                    return
            for v in o.values():
                rec(v)
        elif isinstance(o, list):
            for v in o:
                rec(v)

    rec(data)
    return found[0] if found else None


def _next_comment_token(j) -> Optional[str]:
    """The 'show more comments' continuation token from a /next response.

    Targets the top-level continuationItems (not reply continuations nested
    inside comment threads), taking the last continuationItemRenderer.
    """
    for ep in j.get("onResponseReceivedEndpoints") or []:
        cont = ep.get("reloadContinuationItemsCommand") or ep.get("appendContinuationItemsAction") or {}
        for it in reversed(cont.get("continuationItems") or []):
            cir = it.get("continuationItemRenderer")
            if cir:
                tok = _find_first_key(cir, "token")
                if tok:
                    return tok
    return None


def _blank_media(platform: str, url: str) -> dict:
    return {
        "platform": platform,
        "url": url,
        "title": "",
        "description": "",
        "author": "",
        "author_url": "",
        "thumbnail": "",
        "duration": 0,
        "posted_at": None,
        "views": 0,
        "likes": 0,
        "comments": 0,
        "shares": 0,
        "extra": {},
    }


class SocialScraper(BaseScraper):
    """Auto-detecting multi-platform link scraper.

    Supports YouTube, TikTok, Facebook and Instagram. TikTok delegates to the
    existing ``VideoDetailScraper`` (full stats + comments + replies). The other
    platforms extract public metadata best-effort and never touch TikTok code.
    """

    def scrape(self, url: str) -> dict:
        url = (url or "").strip()
        platform = detect_platform(url)
        if not platform:
            return self.fail(
                "Không nhận diện được nền tảng. Hỗ trợ: YouTube, TikTok, "
                "Facebook, Instagram."
            )
        try:
            if platform == "tiktok":
                return self._scrape_tiktok(url)
            if platform == "youtube":
                return self._scrape_youtube(url)
            return self._scrape_generic(url, platform)
        except Exception as e:
            log.exception("Social scrape failed (%s)", platform)
            return self.fail(str(e), data={"media": _blank_media(platform, url)})

    # ----------------------------------------------------------------- tiktok
    def _scrape_tiktok(self, url: str) -> dict:
        from src.scrapers.video.video_detail import VideoDetailScraper

        res = VideoDetailScraper(self.settings).scrape(video_url=url)
        if res.get("status") != "success":
            return res
        v = res.get("video") or {}
        handle = v.get("author", "")
        media = _blank_media("tiktok", v.get("video_url") or url)
        media.update(
            {
                "title": v.get("description", ""),
                "description": v.get("description", ""),
                "author": v.get("author_nickname") or handle,
                "author_url": ("https://www.tiktok.com/@" + handle) if handle else "",
                "thumbnail": v.get("cover", ""),
                "duration": _to_int(v.get("duration")),
                "posted_at": v.get("posted_at"),
                "views": _to_int(v.get("views")),
                "likes": _to_int(v.get("likes")),
                "comments": _to_int(v.get("comments")),
                "shares": _to_int(v.get("shares")),
                "extra": {
                    "saves": _to_int(v.get("saves")),
                    "hashtags": v.get("hashtags") or [],
                    "music_title": v.get("music_title", ""),
                    "author_handle": handle,
                    "verified": bool(v.get("author_verified")),
                },
            }
        )
        return self.ok(
            {
                "media": media,
                "comments": res.get("comments") or [],
                "comment_total": res.get("comment_total") or 0,
            }
        )

    # ---------------------------------------------------------------- youtube
    def _scrape_youtube(self, url: str) -> dict:
        watch = self._youtube_watch_url(url)
        media = _blank_media("youtube", watch)
        html_text = ""
        try:
            html_text = self.http.get_text(watch)
        except Exception as e:
            log.warning("YouTube fetch failed: %s", e)

        ydata = _json_after(html_text, "ytInitialData") if html_text else None
        pr = _json_after(html_text, "ytInitialPlayerResponse") if html_text else None
        if pr:
            vd = pr.get("videoDetails") or {}
            mf = (pr.get("microformat") or {}).get("playerMicroformatRenderer") or {}
            thumbs = (vd.get("thumbnail") or {}).get("thumbnails") or []
            media.update(
                {
                    "title": vd.get("title", ""),
                    "description": vd.get("shortDescription", ""),
                    "author": vd.get("author", "") or mf.get("ownerChannelName", ""),
                    "author_url": mf.get("ownerProfileUrl", ""),
                    "thumbnail": thumbs[-1]["url"] if thumbs else "",
                    "duration": _to_int(vd.get("lengthSeconds")),
                    "views": _to_int(vd.get("viewCount")),
                    "posted_at": self._iso_date(mf.get("publishDate") or mf.get("uploadDate")),
                    "extra": {
                        "keywords": (vd.get("keywords") or [])[:15],
                        "channel_id": vd.get("channelId", ""),
                        "category": mf.get("category", ""),
                    },
                }
            )
        else:
            meta = _meta_tags(html_text)
            media["title"] = meta.get("og:title", "")
            media["description"] = meta.get("og:description", "")
            media["thumbnail"] = meta.get("og:image", "")

        media["likes"] = self._yt_count(
            html_text, (r'"likeCount":"(\d+)"', r'"accessibilityText":"([\d.,]+) likes?"')
        )

        # subscriber count (best-effort, lives in ytInitialData)
        if ydata:
            sub = _find_first_key(ydata, "subscriberCountText")
            if isinstance(sub, dict):
                txt = sub.get("simpleText") or _find_first_key(sub, "text") or ""
                media["extra"]["subscribers"] = _parse_human_num(txt)

        # real comments + count via the innertube /next API (best-effort)
        comments, ccount = self._youtube_comments(html_text, ydata)
        media["comments"] = ccount or self._yt_count(
            html_text, (r'"commentCount":\{"simpleText":"([\d.,]+)"', r'"commentCount":"(\d+)"')
        ) or len(comments)

        if not media.get("title"):
            return self.fail("Không lấy được dữ liệu video YouTube.", data={"media": media})
        return self.ok(
            {"media": media, "comments": comments, "comment_total": media["comments"]}
        )

    def _youtube_comments(self, html_text: str, ydata, cap: int = 200, max_pages: int = 25):
        """Fetch comments via YouTube's innertube /next endpoint, following the
        continuation token to page through up to ``cap`` comments.

        Fully best-effort: any failure returns whatever was collected so far and
        never breaks the metadata result.
        """
        try:
            if not html_text or not ydata:
                return [], 0
            km = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html_text)
            vm = re.search(r'"INNERTUBE_CONTEXT_CLIENT_VERSION":"([^"]+)"', html_text) or \
                re.search(r'"clientVersion":"([\d.]+)"', html_text)
            if not km or not vm:
                return [], 0
            token = _find_comment_token(ydata)
            if not token:
                return [], 0
            api_url = "https://www.youtube.com/youtubei/v1/next?key=" + km.group(1)
            ctx = {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": vm.group(1),
                    "hl": "en",
                    "gl": "US",
                }
            }
        except Exception as e:
            log.warning("YouTube comments setup failed: %s", e)
            return [], 0

        comments: list = []
        seen: set = set()
        count = 0
        pages = 0
        empty_streak = 0
        while token and len(comments) < cap and pages < max_pages:
            pages += 1
            try:
                resp = self.http.session.post(
                    api_url,
                    json={"context": ctx, "continuation": token},
                    timeout=self.settings.timeout_s,
                    headers={"Content-Type": "application/json"},
                )
                resp.raise_for_status()
                j = resp.json()
            except Exception as e:
                log.warning("YouTube comments page %d failed: %s", pages, e)
                break

            if not count:
                hdr = _find_first_key(j, "commentsHeaderRenderer")
                if isinstance(hdr, dict):
                    runs = (hdr.get("countText") or {}).get("runs") or []
                    if runs:
                        count = _parse_human_num(runs[0].get("text", ""))

            added = 0
            mutations = (
                ((j.get("frameworkUpdates") or {}).get("entityBatchUpdate") or {}).get("mutations")
            ) or []
            for mu in mutations:
                payload = (mu.get("payload") or {}).get("commentEntityPayload")
                if not payload:
                    continue
                props = payload.get("properties") or {}
                cid = props.get("commentId", "")
                if cid and cid in seen:
                    continue
                text = (props.get("content") or {}).get("content") or ""
                if not text:
                    continue
                author = payload.get("author") or {}
                toolbar = payload.get("toolbar") or {}
                seen.add(cid)
                comments.append(
                    {
                        "cid": cid,
                        "text": text,
                        "user": (author.get("displayName", "") or "").lstrip("@"),
                        "nickname": author.get("displayName", ""),
                        "likes": _parse_human_num(
                            toolbar.get("likeCountNotliked") or toolbar.get("likeCountLiked") or "0"
                        ),
                        "replies": _parse_human_num(toolbar.get("replyCount") or "0"),
                        "created_at": "",
                        "reply_list": [],
                    }
                )
                added += 1
                if len(comments) >= cap:
                    break

            empty_streak = empty_streak + 1 if added == 0 else 0
            if empty_streak >= 2:
                break
            token = _next_comment_token(j)

        return comments, (count or len(comments))

    @staticmethod
    def _youtube_watch_url(url: str) -> str:
        p = urlparse(url)
        if "youtu.be" in p.netloc.lower():
            vid = p.path.strip("/").split("/")[0]
            return "https://www.youtube.com/watch?v=" + vid
        if "/shorts/" in p.path:
            vid = p.path.split("/shorts/")[1].split("/")[0]
            return "https://www.youtube.com/watch?v=" + vid
        q = parse_qs(p.query)
        if "v" in q:
            return "https://www.youtube.com/watch?v=" + q["v"][0]
        return url

    @staticmethod
    def _yt_count(html_text: str, patterns) -> int:
        if not html_text:
            return 0
        for pat in patterns:
            m = re.search(pat, html_text)
            if m:
                return _to_int(m.group(1))
        return 0

    # ----------------------------------------------------- facebook / instagram
    def _scrape_generic(self, url: str, platform: str) -> dict:
        media = _blank_media(platform, url)
        html_text = ""
        try:
            html_text = self.http.get_text(url)
        except Exception as e:
            log.warning("%s http fetch failed: %s", platform, e)

        meta = _meta_tags(html_text) if html_text else {}
        # Public OG tags are sometimes only present after a real browser render.
        if not meta.get("og:title") and not meta.get("og:description"):
            try:
                from src.scrapers.video.profile_videos import fetch_html_via_browser

                rendered = fetch_html_via_browser(url, self.settings, wait_ms=4000)
                if rendered:
                    meta = _meta_tags(rendered)
            except Exception as e:
                log.warning("%s browser fetch failed: %s", platform, e)

        media["title"] = meta.get("og:title", "")
        media["description"] = meta.get("og:description", "") or meta.get("description", "")
        media["thumbnail"] = meta.get("og:image", "")
        media["author"] = meta.get("og:site_name", "") or platform.capitalize()
        if meta.get("og:video") or meta.get("og:video:url"):
            media["extra"]["video_url"] = meta.get("og:video") or meta.get("og:video:url")

        # Pull the real account name + clean caption out of the cluttered OG
        # title ("Name on Instagram: ..." / "N reactions | caption | Author").
        og_title = meta.get("og:title", "")
        try:
            if platform == "instagram":
                am = re.match(r"\s*(.+?)\s+on Instagram\b", og_title, re.I)
                if am and am.group(1).strip():
                    media["author"] = am.group(1).strip()
                qm = re.search(r'on Instagram[^:]*:\s*["“](.+)["”]\s*$', og_title, re.I | re.S)
                if qm:
                    media["title"] = qm.group(1).strip()
            elif platform == "facebook":
                parts = [p.strip() for p in og_title.split("|") if p.strip()]
                if len(parts) >= 2:
                    media["author"] = parts[-1]
                    body = [
                        p for p in parts[:-1]
                        if not re.search(r"\b(reactions?|views?|comments?|likes?|shares?)\b", p, re.I)
                    ]
                    if body:
                        media["title"] = " | ".join(body).strip()
        except Exception:
            pass

        # Counts live in OG text, e.g. Instagram "129K likes, 291 comments" or
        # Facebook "2M views · 44K reactions". Handle the K/M/B suffixes.
        blob = " ".join([meta.get("og:title", ""), meta.get("og:description", "")])
        media["views"] = _extract_count(blob, ("views", "plays"))
        media["likes"] = _extract_count(blob, ("likes", "reactions"))
        media["comments"] = _extract_count(blob, ("comments",))
        media["shares"] = _extract_count(blob, ("shares",))

        if not (media["title"] or media["description"] or media["thumbnail"]):
            return self.fail(
                f"Không lấy được dữ liệu {platform} (bài viết riêng tư hoặc cần "
                "đăng nhập). Chỉ hỗ trợ nội dung công khai.",
                data={"media": media},
            )
        return self.ok({"media": media, "comments": [], "comment_total": media["comments"]})

    @staticmethod
    def _iso_date(s: Optional[str]) -> Optional[str]:
        if not s:
            return None
        if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            return s + "T00:00:00Z"
        return s
