"""
One-off helper: open a real Chromium window with the persistent browser
profile so you can log into TikTok once. The logged-in session is saved to
`.browser_profile` and reused by the scrapers (far fewer CAPTCHAs afterwards).

Run:  python login_tiktok.py
"""
from __future__ import annotations

import time
from pathlib import Path

from playwright.sync_api import sync_playwright

PROFILE_DIR = Path(__file__).resolve().parents[2] / ".browser_profile"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
WAIT_SECONDS = 420
# Any of these cookies indicates a logged-in TikTok session.
SESSION_COOKIES = {"sessionid", "sessionid_ss", "sid_tt", "uid_tt", "sid_guard"}


def main() -> int:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            user_agent=UA,
            locale="en-US",
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.tiktok.com/login", wait_until="domcontentloaded")

        print(
            f"Waiting up to {WAIT_SECONDS}s for you to log in... "
            "(do NOT close the window — it closes itself once login is detected)",
            flush=True,
        )
        logged_in = False
        waited = 0
        while waited < WAIT_SECONDS:
            try:
                cookies = {c["name"] for c in ctx.cookies()}
            except Exception:
                # Window was closed manually; cookies (if any) are on disk.
                print("Window closed by user.", flush=True)
                break
            if cookies & SESSION_COOKIES:
                logged_in = True
                break
            time.sleep(3)
            waited += 3

        if logged_in:
            print("LOGIN OK — session saved to .browser_profile", flush=True)
        else:
            print("No login detected.", flush=True)

        try:
            ctx.close()
        except Exception:
            pass
    return 0 if logged_in else 1


if __name__ == "__main__":
    raise SystemExit(main())
