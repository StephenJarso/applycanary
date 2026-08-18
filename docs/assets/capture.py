"""Regenerate the ApplyCanary screenshots in this folder.

Usage (from the repo root, with the local server running on :8000):

    .venv/bin/python docs/assets/capture.py

Produces: cover.png, architecture.png, dashboard-jobs.png, job-detail.png,
interview-studio.png, memory.png, guest-jobs.png

The app screenshots log in with the account below — edit EMAIL/PASSWORD if you
want to capture a different profile.
"""

import asyncio
import os
import sys

from playwright.async_api import async_playwright

BASE = os.environ.get("APP_BASE", "http://127.0.0.1:8000")
OUT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(OUT, "src")

# Local demo account (edit to taste)
EMAIL = os.environ.get("CAPTURE_EMAIL", "stephenjacob815@gmail.com")
PASSWORD = os.environ.get("CAPTURE_PASSWORD", "Jason@2019")

# The Go-developer role used for the demo screenshots
DEMO_JOB_ID = os.environ.get("DEMO_JOB_ID", "47")


async def shot(page, path, full_page=False, wait_ms=0):
    if wait_ms:
        await page.wait_for_timeout(wait_ms)
    await page.screenshot(path=os.path.join(OUT, path), full_page=full_page)
    print(f"  ✓ {path}")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()

        # --- Static artwork (cover + architecture) ---
        art = await browser.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=2)
        page = await art.new_page()
        await page.goto(f"file://{SRC}/cover.html")
        await shot(page, "cover.png", wait_ms=1200)

        arch = await browser.new_context(viewport={"width": 1600, "height": 1040}, device_scale_factor=2)
        page = await arch.new_page()
        await page.goto(f"file://{SRC}/architecture.html")
        await shot(page, "architecture.png", wait_ms=1200)
        # Same source, vector-crisp PDF for the submission (print_background so
        # the dark theme renders; no margins so the diagram fills the page).
        await page.pdf(
            path=os.path.join(OUT, "architecture.pdf"),
            width="1600px",
            height="1040px",
            print_background=True,
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
        )
        print("  ✓ architecture.pdf")

        # A4-landscape variant for submission forms expecting standard paper.
        # The 1600×1040 design is scaled to fit; the letterbox matches the
        # diagram's dark background so it disappears on the page.
        a4 = await arch.new_page()
        await a4.goto(f"file://{SRC}/architecture.html")
        await a4.wait_for_timeout(800)
        scale = min(1123 / 1600, 794 / 1040)  # A4 landscape @96dpi
        await a4.evaluate(
            f"document.documentElement.style.background='#0d1226';"
            f"document.body.style.transform='scale({scale:.4f})';"
            f"document.body.style.transformOrigin='top left';"
            f"document.body.style.width='{1600 * scale:.0f}px';"
            f"document.body.style.height='{1040 * scale:.0f}px';"
            f"document.body.style.overflow='visible';"
        )
        await a4.pdf(
            path=os.path.join(OUT, "architecture-a4.pdf"),
            format="A4",
            landscape=True,
            print_background=True,
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
        )
        print("  ✓ architecture-a4.pdf")
        await art.close()
        await arch.close()

        # --- Authenticated app screenshots ---
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        page = await ctx.new_page()

        print("Logging in…")
        await page.goto(f"{BASE}/login")
        await page.fill('input[type="text"]', EMAIL)
        await page.fill('input[type="password"]', PASSWORD)
        await page.click('button:has-text("Sign in")')
        try:
            await page.wait_for_url(f"{BASE}/**", timeout=15000)
        except Exception:
            pass
        await page.wait_for_timeout(3000)
        if "/login" in page.url:
            banner = await page.locator(".banner-bad").inner_text() if await page.locator(".banner-bad").count() else "unknown"
            print(f"Login failed: {banner}", file=sys.stderr)
            sys.exit(1)
        print("Logged in — capturing…")

        # Jobs dashboard
        await page.goto(f"{BASE}/", wait_until="domcontentloaded", timeout=60000)
        await shot(page, "dashboard-jobs.png", wait_ms=4500)

        # Job detail (Golang role) with match card + similar roles
        await page.goto(f"{BASE}/job/{DEMO_JOB_ID}", wait_until="domcontentloaded", timeout=60000)
        await shot(page, "job-detail.png", full_page=True, wait_ms=4500)

        # AI Interview Studio
        await page.goto(f"{BASE}/job/{DEMO_JOB_ID}/interview", wait_until="domcontentloaded", timeout=60000)
        await shot(page, "interview-studio.png", wait_ms=5000)

        # Memory page
        await page.goto(f"{BASE}/memory", wait_until="domcontentloaded", timeout=60000)
        await shot(page, "memory.png", full_page=True, wait_ms=4000)

        await ctx.close()

        # --- Guest mode (no login) ---
        guest = await browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        page = await guest.new_page()
        await page.goto(f"{BASE}/guest")
        await shot(page, "guest-jobs.png", wait_ms=4500)
        await guest.close()

        await browser.close()
    print("Done — see docs/assets/")


if __name__ == "__main__":
    asyncio.run(main())
