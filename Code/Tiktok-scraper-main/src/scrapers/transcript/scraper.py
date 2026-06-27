from __future__ import annotations

import json
import re
from typing import List, Optional

from src.scrapers.base import BaseScraper
from src.utils.logger import get_logger
from src.scrapers.video.profile_videos import _STATE_PATH, _PROFILE_DIR
from src.scrapers.video.video_detail import VideoDetailScraper, _UNIVERSAL_DATA_RE

log = get_logger(__name__)

_ID_RE = re.compile(r"/(?:video|photo)/(\d+)")

_LANG_NAMES = {
    "vie": "Tiếng Việt", "eng": "English", "jpn": "日本語", "kor": "한국어",
    "zho": "中文", "cmn": "中文", "yue": "中文 (Quảng Đông)", "fra": "Français",
    "spa": "Español", "deu": "Deutsch", "ita": "Italiano", "tha": "ไทย",
    "ind": "Bahasa Indonesia", "rus": "Русский", "por": "Português",
    "ara": "العربية", "hin": "हिन्दी", "tur": "Türkçe", "nld": "Nederlands",
}


def _lang_name(code: str) -> str:
    if not code:
        return "?"
    base = code.split("-")[0].lower()
    return _LANG_NAMES.get(base, code)


def _ts_to_sec(ts: str) -> float:
    try:
        parts = [float(p) for p in ts.strip().split(":")]
    except ValueError:
        return 0.0
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


# Whisper model is loaded once per process and reused (loading is the slow part).
_WHISPER_MODELS: dict = {}
_WHISPER_SIZE = "small"          # balance of quality vs CPU speed


def _get_whisper_model(size: str = _WHISPER_SIZE):
    if size not in _WHISPER_MODELS:
        from faster_whisper import WhisperModel

        _WHISPER_MODELS[size] = WhisperModel(size, device="cpu", compute_type="int8")
    return _WHISPER_MODELS[size]


def _sec_to_ts(sec) -> str:
    sec = max(0.0, float(sec or 0))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def parse_vtt(content: str) -> List[dict]:
    """Parse WebVTT text into [{start, end, start_s, end_s, text}] cues."""
    segments: List[dict] = []
    blocks = re.split(r"\r?\n\r?\n", (content or "").strip())
    for block in blocks:
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        if lines[0].strip().upper().startswith("WEBVTT"):
            lines = lines[1:]
        ts_idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if ts_idx is None:
            continue
        m = re.match(r"\s*([0-9:.,]+)\s*-->\s*([0-9:.,]+)", lines[ts_idx])
        if not m:
            continue
        start, end = m.group(1).replace(",", "."), m.group(2).replace(",", ".")
        text = " ".join(l.strip() for l in lines[ts_idx + 1:]).strip()
        # Strip simple VTT inline tags if any.
        text = re.sub(r"<[^>]+>", "", text).strip()
        if text:
            segments.append({
                "start": start, "end": end,
                "start_s": round(_ts_to_sec(start), 3),
                "end_s": round(_ts_to_sec(end), 3),
                "text": text,
            })
    return segments


class TranscriptScraper(BaseScraper):
    """
    Generate a transcript for a single TikTok video from its own caption tracks.

    Reuses the video-scrape browser session: it opens the video page, reads the
    caption (subtitle) tracks from the embedded JSON, downloads each WebVTT track
    via the browser request context (bypasses CORS / signed CDN), and parses them
    into time-coded segments. ASR tracks are the original spoken language; MT
    tracks are TikTok's machine translations.
    """

    def scrape(self, video_url: str) -> dict:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return self.fail(
                "Playwright is not installed. Run: pip install playwright "
                "&& python -m playwright install chromium"
            )

        if not _ID_RE.search(video_url or ""):
            return self.fail(
                "Link video không hợp lệ. Dán link dạng "
                "https://www.tiktok.com/@user/video/123..."
            )

        try:
            from playwright_stealth import Stealth

            playwright_cm = Stealth().use_sync(sync_playwright())
        except ImportError:
            playwright_cm = sync_playwright()

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

                page = context.pages[0] if context.pages else context.new_page()
                log.info("Opening video for transcript: %s", video_url)
                page.goto(video_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(5000)

                html = page.content()
                item = self._item_struct(html)
                video = VideoDetailScraper._parse_video(html, "", video_url)
                tracks, original_lang, no_caption = self._caption_tracks(item)

                # Luồng 1: phụ đề có sẵn của TikTok.
                transcripts = self._captions_to_transcripts(page, tracks, original_lang)

                # Chỉ tải media cho luồng 2 (Whisper) KHI luồng 1 không có gì.
                media_bytes = None
                if not transcripts:
                    vid = item.get("video") or {}
                    media_url = vid.get("playAddr") or vid.get("downloadAddr")
                    if media_url:
                        media_bytes = self._download_media(page, media_url)

                context.close()
                if browser:
                    browser.close()

            # Luồng 2: tự trích xuất bằng Whisper (ngoài phiên trình duyệt).
            method = "tiktok"
            if not transcripts and media_bytes:
                method = "whisper"
                wt = self._whisper_transcribe(media_bytes)
                if wt:
                    transcripts.append(wt)

            if not transcripts:
                if not video.get("video_id"):
                    return self.fail(
                        "Không đọc được dữ liệu video (link sai, video riêng tư, "
                        "hoặc phiên đăng nhập đã hết hạn).",
                        data={"video": {"video_url": video_url}},
                    )
                reason = no_caption or "Video không có phụ đề tự động."
                return self.fail(
                    "Không tạo được transcript. " + reason
                    + " Tự trích xuất (Whisper) cũng không thành công.",
                    data={"video": video, "transcripts": [], "has_transcript": False},
                )

            if method == "tiktok":
                transcripts.sort(key=lambda t: (not t["is_original"], t["source"] != "ASR"))

            return self.ok({
                "video": video,
                "transcripts": transcripts,
                "original_language": original_lang,
                "has_transcript": True,
                "method": method,
            })
        except Exception as e:
            log.exception("Transcript scraping failed")
            return self.fail(str(e), data={"video": {"video_url": video_url}})

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _item_struct(html: str) -> dict:
        m = _UNIVERSAL_DATA_RE.search(html)
        if not m:
            return {}
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            return {}
        detail = data.get("__DEFAULT_SCOPE__", {}).get("webapp.video-detail", {})
        return (detail.get("itemInfo") or {}).get("itemStruct") or {}

    @staticmethod
    def _caption_tracks(item: dict):
        """Return (tracks, original_language, no_caption_reason)."""
        vid = item.get("video") or {}
        cla = vid.get("claInfo") or {}
        orig_info = cla.get("originalLanguageInfo") or {}
        original_lang = orig_info.get("languageCode") or orig_info.get("language") or ""

        tracks = []
        seen = set()
        for s in (vid.get("subtitleInfos") or []):
            if not isinstance(s, dict):
                continue
            url = s.get("Url")
            lang = s.get("LanguageCodeName") or ""
            src = s.get("Source") or ""
            key = (lang, src)
            if not url or key in seen:
                continue
            seen.add(key)
            tracks.append({"language": lang, "source": src, "url": url})

        # Fallback to claInfo.captionInfos if subtitleInfos was empty.
        if not tracks:
            for c in (cla.get("captionInfos") or []):
                if not isinstance(c, dict):
                    continue
                url = c.get("url")
                lang = c.get("language") or ""
                if not url or lang in seen:
                    continue
                seen.add(lang)
                tracks.append({
                    "language": lang,
                    "source": "ASR" if c.get("isAutoGen") else "MT",
                    "url": url,
                })

        no_caption = cla.get("noCaptionReason") or ""
        return tracks, original_lang, no_caption

    @staticmethod
    def _fetch_vtt(page, url: str) -> Optional[str]:
        """Download a caption track via the browser request context (no CORS)."""
        try:
            resp = page.request.get(url, timeout=30000)
            if resp.status != 200:
                log.warning("Caption fetch HTTP %s", resp.status)
                return None
            return resp.text()
        except Exception as e:
            log.warning("Caption fetch failed: %s", e)
            return None

    def _captions_to_transcripts(self, page, tracks, original_lang) -> List[dict]:
        """Luồng 1: tải & phân tích các track phụ đề có sẵn của TikTok."""
        out: List[dict] = []
        for tr in tracks:
            vtt = self._fetch_vtt(page, tr["url"])
            if not vtt:
                continue
            segments = parse_vtt(vtt)
            if not segments:
                continue
            out.append({
                "language": tr["language"],
                "language_name": _lang_name(tr["language"]),
                "source": tr["source"],                # ASR / MT
                "is_auto": True,
                "is_original": tr["language"] == original_lang or tr["source"] == "ASR",
                "segment_count": len(segments),
                "text": "\n".join(s["text"] for s in segments),
                "segments": segments,
            })
        return out

    @staticmethod
    def _download_media(page, url: str) -> Optional[bytes]:
        """Tải file video (để Whisper trích xuất) qua trình duyệt — có cookie + UA."""
        try:
            resp = page.request.get(
                url, headers={"Referer": "https://www.tiktok.com/"}, timeout=60000
            )
            if resp.status != 200:
                log.warning("Media download HTTP %s", resp.status)
                return None
            return resp.body()
        except Exception as e:
            log.warning("Media download failed: %s", e)
            return None

    @staticmethod
    def _whisper_transcribe(media_bytes: bytes) -> Optional[dict]:
        """Luồng 2: tự trích xuất transcript từ audio bằng faster-whisper."""
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            log.warning("faster-whisper chưa được cài; bỏ qua luồng tự trích xuất.")
            return None

        import os
        import tempfile

        tmp = os.path.join(tempfile.gettempdir(), f"ttk_whisper_{os.getpid()}.mp4")
        try:
            with open(tmp, "wb") as f:
                f.write(media_bytes)

            model = _get_whisper_model()
            seg_iter, info = model.transcribe(tmp, beam_size=1, vad_filter=True)

            segments: List[dict] = []
            for s in seg_iter:
                text = (s.text or "").strip()
                if not text:
                    continue
                segments.append({
                    "start": _sec_to_ts(s.start), "end": _sec_to_ts(s.end),
                    "start_s": round(float(s.start or 0), 3),
                    "end_s": round(float(s.end or 0), 3),
                    "text": text,
                })
            if not segments:
                return None

            lang = info.language or ""
            return {
                "language": lang,
                "language_name": _lang_name(lang),
                "source": "Whisper",
                "is_auto": True,
                "is_original": True,
                "segment_count": len(segments),
                "text": "\n".join(s["text"] for s in segments),
                "segments": segments,
            }
        except Exception as e:
            log.exception("Whisper transcription failed: %s", e)
            return None
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
