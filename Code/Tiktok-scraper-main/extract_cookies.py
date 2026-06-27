"""
Extract TikTok cookies from your real desktop browser (Chrome/Edge) and save
them as a Playwright storage_state file the scrapers can reuse headlessly.

Chrome/Edge v130+ encrypt cookies with app-bound encryption, so this must be
run as administrator. A wrapper launches it elevated; results and a short log
are written next to the project so the (elevated) console output isn't lost.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Project root = two levels up from this file (…/Tiktok scraper by TTV).
ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = ROOT / "tiktok_state.json"
LOG_PATH = ROOT / "_extract_cookies.log"

SESSION_COOKIES = {"sessionid", "sessionid_ss", "sid_tt", "uid_tt", "sid_guard"}


def _same_site(value) -> str:
    v = str(value).lower() if value is not None else ""
    if "strict" in v:
        return "Strict"
    if "none" in v:
        return "None"
    return "Lax"


def _to_pw_cookie(c: dict) -> dict:
    expires = c.get("expires")
    try:
        expires = float(expires) if expires else -1
    except (TypeError, ValueError):
        expires = -1
    return {
        "name": c.get("name", ""),
        "value": c.get("value", ""),
        "domain": c.get("domain", ".tiktok.com"),
        "path": c.get("path", "/"),
        "expires": expires,
        "httpOnly": bool(c.get("http_only", c.get("httpOnly", False))),
        "secure": bool(c.get("secure", True)),
        "sameSite": _same_site(c.get("same_site", c.get("sameSite"))),
    }


def main() -> int:
    log_lines = []

    def log(msg):
        log_lines.append(str(msg))
        print(msg, flush=True)

    try:
        import rookiepy
    except Exception as e:
        log(f"rookiepy not installed: {e}")
        LOG_PATH.write_text("\n".join(log_lines), encoding="utf-8")
        return 1

    chosen = None
    cookies = []
    for browser in ("chrome", "edge"):
        try:
            raw = getattr(rookiepy, browser)(["tiktok.com"])
        except Exception as e:
            log(f"{browser}: ERROR {type(e).__name__}: {str(e)[:120]}")
            continue
        names = {c["name"] for c in raw}
        has_session = bool(SESSION_COOKIES & names)
        log(f"{browser}: {len(raw)} tiktok cookies, logged_in={has_session}")
        if has_session and not chosen:
            chosen = browser
            cookies = raw

    if not chosen:
        log("No logged-in TikTok session found in Chrome or Edge.")
        log("=> Log into tiktok.com in Chrome/Edge first, then re-run.")
        LOG_PATH.write_text("\n".join(log_lines), encoding="utf-8")
        return 2

    state = {"cookies": [_to_pw_cookie(c) for c in cookies], "origins": []}
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    log(f"OK: saved {len(state['cookies'])} cookies from {chosen} -> {STATE_PATH.name}")
    LOG_PATH.write_text("\n".join(log_lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
