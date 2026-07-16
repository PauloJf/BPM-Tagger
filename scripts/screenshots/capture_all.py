"""Capture the full README screenshot set (01-12) with Playwright.

Run `start-backend.ps1` first (serves the built UI on :5088 seeded with
demo_music), then `python scripts/screenshots/capture_all.py`. Images are
written straight into docs/screenshots/.

Config via env (defaults match start-backend.ps1):
  SHOT_BASE          UI base URL           (default http://localhost:5088)
  SHOT_ADMIN_PW      admin UI password     (default screenshot123)
  SHOT_RUN_PW        player/run password   (default runmode123)
  SHOT_OUT           output directory      (default <repo>/docs/screenshots)

Prereqs: pip install playwright && playwright install chromium; a built
frontend (npm --prefix frontend run build) served by the backend.
"""
import os
import time

from playwright.sync_api import sync_playwright

BASE = os.environ.get("SHOT_BASE", "http://localhost:5088")
PASSWORD = os.environ.get("SHOT_ADMIN_PW", "screenshot123")
RUN_PASSWORD = os.environ.get("SHOT_RUN_PW", "runmode123")
OUT = os.environ.get("SHOT_OUT") or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs", "screenshots")
)

DESKTOP = {"width": 1400, "height": 860}
MOBILE = {"width": 390, "height": 844}


def dismiss_ping(page):
    """Dismiss the one-time anonymous-install-count consent dialog if shown."""
    try:
        nt = page.get_by_text("No thanks", exact=True)
        nt.wait_for(timeout=2000)
        nt.click()
    except Exception:
        pass


def login(page, password=PASSWORD):
    page.goto(BASE + "/", wait_until="networkidle")
    page.wait_for_selector("input[type=password]")
    page.fill("input[type=password]", password)
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")
    dismiss_ping(page)
    time.sleep(0.5)


def shoot(page, path, name, wait_selector=None, extra_wait=0.6):
    page.goto(BASE + path, wait_until="networkidle")
    if wait_selector:
        try:
            page.wait_for_selector(wait_selector, timeout=5000)
        except Exception:
            pass
    time.sleep(extra_wait)
    page.screenshot(path=f"{OUT}/{name}")
    print("saved", name)


def main():
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()

        # ---- desktop, logged out: fresh login screen ----
        ctx = browser.new_context(viewport=DESKTOP, color_scheme="dark")
        page = ctx.new_page()
        page.goto(BASE + "/", wait_until="networkidle")
        page.wait_for_selector("input[type=password]")
        time.sleep(0.4)
        page.screenshot(path=f"{OUT}/01-login.png")
        print("saved 01-login.png")

        login(page)
        shoot(page, "/tracks", "02-library.png", "text=Library")
        shoot(page, "/review", "04-review.png", "text=Needs review")

        # track detail — grab first review-queue track link
        page.goto(BASE + "/review", wait_until="networkidle")
        page.wait_for_selector("text=tracks flagged")
        time.sleep(0.8)
        link = page.locator("a[href*='/track?path=']").first
        href = link.get_attribute("href", timeout=10000)
        shoot(page, href, "05-track-detail.png")

        shoot(page, "/stats", "06-stats.png", "text=Statistics")
        shoot(page, "/settings", "07-settings.png", "text=Settings")
        shoot(page, "/about", "08-about.png", "text=About")

        # star a few mid-tempo tracks so Run mode has a populated queue
        page.goto(BASE + "/tracks?bpm=124&tolerance=8", wait_until="networkidle")
        time.sleep(0.6)
        stars = page.locator("button[aria-label='Star']")
        count = min(stars.count(), 6)
        print("star buttons found:", stars.count())
        for i in range(count):
            try:
                stars.nth(i).click()
                time.sleep(0.15)
            except Exception:
                pass
        ctx.close()

        # ---- mobile, admin UI ----
        ctx_m = browser.new_context(viewport=MOBILE, color_scheme="dark", is_mobile=True, has_touch=True)
        page_m = ctx_m.new_page()
        login(page_m)
        shoot(page_m, "/tracks", "09-mobile-library.png", "text=Library")
        ctx_m.close()

        # ---- player mode: the run-only password's login screen (desktop) ----
        ctx_p = browser.new_context(viewport=DESKTOP, color_scheme="dark")
        page_p = ctx_p.new_page()
        page_p.goto(BASE + "/run", wait_until="networkidle")
        page_p.wait_for_selector("input[type=password]", timeout=5000)
        time.sleep(0.4)
        page_p.screenshot(path=f"{OUT}/10-player-login.png")
        print("saved 10-player-login.png")
        ctx_p.close()

        browser.close()

    # Player mode's populated Run cockpit (11 + 12) is built in capture_player.py
    # because it needs a starred, running queue — run that next.
    print("done — now run capture_player.py for 11-player-desktop / 12-player-mobile")


if __name__ == "__main__":
    main()
