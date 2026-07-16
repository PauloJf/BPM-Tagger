"""Capture the player-mode Run screenshots (11-player-desktop, 12-player-mobile).

Separate from capture_all.py because these need a *populated, running* queue:
it logs in as admin to star a handful of mid-tempo tracks, then enters player
mode, picks the Warmup preset, starts a run, and shoots the cockpit.

Run `start-backend.ps1` first, then:
  python scripts/screenshots/capture_player.py

Config via env (defaults match start-backend.ps1):
  SHOT_BASE, SHOT_ADMIN_PW, SHOT_RUN_PW, SHOT_OUT   (see capture_all.py)
"""
import os
import time

from playwright.sync_api import sync_playwright

BASE = os.environ.get("SHOT_BASE", "http://localhost:5088")
ADMIN_PASSWORD = os.environ.get("SHOT_ADMIN_PW", "screenshot123")
RUN_PASSWORD = os.environ.get("SHOT_RUN_PW", "runmode123")
OUT = os.environ.get("SHOT_OUT") or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs", "screenshots")
)

DESKTOP = {"width": 1400, "height": 860}
MOBILE = {"width": 390, "height": 844}


def dismiss_ping(page):
    try:
        nt = page.get_by_text("No thanks", exact=True)
        nt.wait_for(timeout=2000)
        nt.click()
    except Exception:
        pass


def star_demo_tracks(browser):
    """Star up to 6 mid-tempo tracks so the run queue shows stars."""
    ctx = browser.new_context(viewport=DESKTOP, color_scheme="dark")
    page = ctx.new_page()
    page.goto(BASE + "/", wait_until="networkidle")
    page.wait_for_selector("input[type=password]")
    page.fill("input[type=password]", ADMIN_PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")
    time.sleep(0.6)
    dismiss_ping(page)
    page.goto(BASE + "/tracks?bpm=124&tolerance=8", wait_until="networkidle")
    time.sleep(0.6)
    stars = page.locator("button[aria-label='Star']")
    n = min(stars.count(), 6)
    print("star buttons:", stars.count())
    for i in range(n):
        try:
            stars.nth(i).click()
            time.sleep(0.15)
        except Exception:
            pass
    ctx.close()


def enter_player_run(page):
    page.goto(BASE + "/run", wait_until="networkidle")
    page.wait_for_selector("input[type=password]")
    page.fill("input[type=password]", RUN_PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")
    time.sleep(1.0)
    dismiss_ping(page)
    # Warmup preset (~120 bpm) matches the starred demo tracks
    page.get_by_text("Warmup", exact=True).click()
    time.sleep(0.3)
    btn = page.locator("button, [role=button]").filter(has_text="Start run")
    if btn.count() == 0:
        btn = page.locator("[aria-label='Start run']")
    btn.first.click()
    time.sleep(2.5)


def main():
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        star_demo_tracks(browser)

        # Player desktop
        ctx = browser.new_context(viewport=DESKTOP, color_scheme="dark")
        page = ctx.new_page()
        enter_player_run(page)
        page.screenshot(path=f"{OUT}/11-player-desktop.png")
        print("saved 11-player-desktop.png")
        ctx.close()

        # Player mobile
        ctxm = browser.new_context(viewport=MOBILE, color_scheme="dark", is_mobile=True, has_touch=True)
        pagem = ctxm.new_page()
        enter_player_run(pagem)
        pagem.screenshot(path=f"{OUT}/12-player-mobile.png")
        print("saved 12-player-mobile.png")
        ctxm.close()

        browser.close()


if __name__ == "__main__":
    main()
