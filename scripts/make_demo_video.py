#!/usr/bin/env python3
"""Record a scripted walkthrough of ApplyCanary and render a demo MP4.

Drives a headless Chromium (Playwright) through the real running app — jobs
feed, search, job detail, AI interview studio, memory, profile — capturing
frames as it scrolls and interacts, then composes them with ffmpeg into a
single MP4 with title/outro cards and crossfades.

Requirements (all optional, install if missing):
    .venv/bin/pip install playwright imageio-ffmpeg Pillow
    .venv/bin/playwright install chromium

The app must be running first (python run.py). The video records the account
given by EMAIL (default: stephenjacob815@gmail.com) via a signed session
token, so no password is needed.

Usage:
    .venv/bin/python scripts/make_demo_video.py [--out demo/applycanary-demo.mp4]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
EMAIL = os.environ.get("EMAIL", "stephenjacob815@gmail.com")
VIEW_W, VIEW_H = 1920, 1080
FPS = 30          # output frames per second
CAP_FPS = 10      # capture frames per second (duplicated to FPS)

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


# ---------------------------------------------------------------- cards


def make_card(path: Path, title: str, sub: str, accent: str = "#7aa2f7") -> None:
    """Minimal, tasteful title/outro card at VIEW_W x VIEW_H."""
    img = Image.new("RGB", (VIEW_W, VIEW_H), (13, 17, 23))
    d = ImageDraw.Draw(img)
    # subtle vertical accent line
    d.rectangle([VIEW_W // 2 - 2, 380, VIEW_W // 2 + 2, 620], fill=accent)
    t_font = ImageFont.truetype(FONT_BOLD, 84)
    s_font = ImageFont.truetype(FONT_REG, 34)
    tw = d.textlength(title, font=t_font)
    d.text(((VIEW_W - tw) / 2, 440), title, font=t_font, fill=(235, 240, 248))
    sw = d.textlength(sub, font=s_font)
    d.text(((VIEW_W - sw) / 2, 580), sub, font=s_font, fill=(150, 160, 175))
    img.save(path)


# ---------------------------------------------------------------- scenes


def record_scene(page: Any, name: str, workdir: Path, duration: float, driver: Any) -> None:
    """Capture `duration` seconds of frames at CAP_FPS while `driver(page)`
    advances the UI (scroll, etc.) on every tick."""
    import time

    out = workdir / name
    out.mkdir(parents=True, exist_ok=True)
    interval = 1.0 / CAP_FPS
    start = time.monotonic()
    i = 0
    while time.monotonic() - start < duration:
        driver(page)
        page.screenshot(path=str(out / f"f{i:04d}.jpg"), type="jpeg", quality=82)
        i += 1
        # pace to CAP_FPS
        elapsed = time.monotonic() - start
        target = i * interval
        if elapsed < target:
            page.wait_for_timeout(int((target - elapsed) * 1000))
    print(f"  scene {name}: {i} frames")


def scroll_to_bottom_slow(page: Any, px_per_tick: int = 46) -> Any:
    def driver(page: Any) -> None:
        page.evaluate(f"window.scrollBy(0, {px_per_tick})")
        page.wait_for_timeout(40)
    return driver


# ---------------------------------------------------------------- capture


def run(workdir: Path, out_path: Path, email: str) -> None:
    from playwright.sync_api import sync_playwright
    from sqlmodel import select

    from app.auth import create_session_token
    from app.db import session_scope
    from app.models import User

    with session_scope() as session:
        user = session.exec(select(User).where(User.email == email)).first()
        if user is None:
            raise SystemExit(f"no user with email {email!r}")
        token = create_session_token(user)
        user_id = user.id
    print(f"recording as {email} (user {user_id})")

    cards = workdir / "cards"
    cards.mkdir(parents=True, exist_ok=True)
    make_card(cards / "title.jpg",
              "ApplyCanary", "Your job search, remembered — CockroachDB memory · AWS · agentic")
    make_card(cards / "outro.jpg",
              "ApplyCanary", "Open source · MIT · built for the CockroachDB × AWS hackathon",
              accent="#a9b1d6")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": VIEW_W, "height": VIEW_H})
        page = ctx.new_page()
        # Set the session cookie before the first navigation so the SPA boots
        # authenticated. The title/outro cards are static (compositor inserts
        # them); every recorded scene below is a live page.
        ctx.add_cookies([{
            "name": "applycanary_session", "value": token,
            "domain": "127.0.0.1", "path": "/",
        }])

        # --- scene 2: jobs dashboard, slow scroll through the feed
        page.goto(f"{BASE_URL}/", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1800)
        record_scene(page, "s2_dashboard", workdir, 14.0, scroll_to_bottom_slow(page))

        # --- scene 3: search for the user's actual role
        page.evaluate("window.scrollTo(0, 0)")
        box = page.locator("input[type=search]")
        box.click()
        box.fill("go developer")
        page.wait_for_timeout(2500)
        record_scene(page, "s3_search", workdir, 12.0, scroll_to_bottom_slow(page))

        # --- scene 4: job detail with the score breakdown
        import httpx

        with httpx.Client(base_url=BASE_URL, timeout=30) as c:
            c.cookies.set("applycanary_session", token)
            r = c.get("/api/jobs", params={"q": "golang", "limit": 50})
            jobs = r.json()["jobs"] if r.status_code == 200 else []
        job_id = jobs[0]["id"] if jobs else 1
        page.goto(f"{BASE_URL}/job/{job_id}", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)
        record_scene(page, "s4_detail", workdir, 16.0, scroll_to_bottom_slow(page))

        # --- scene 5: AI interview studio — ask, answer, get feedback
        page.goto(f"{BASE_URL}/job/{job_id}/interview", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1500)
        page.get_by_role("button", name="Start interview").click()
        page.wait_for_timeout(2500)
        record_scene(page, "s5a_question", workdir, 8.0, scroll_to_bottom_slow(page))
        page.evaluate("window.scrollTo(0, 0)")
        page.locator("textarea").fill(
            "I have spent five years building Go services in production. At my "
            "last company I owned the payments API: I designed it around "
            "PostgreSQL and Redis, cut p99 latency by 40 percent, and led the "
            "migration to Kubernetes. What draws me here is the scale of the "
            "platform and the chance to shape the SDK surfaces developers touch."
        )
        page.get_by_role("button", name="Submit typed answer").click()
        # Evaluation may take a while (LLM path); wait for the feedback card.
        try:
            page.locator("text=Feedback").first.wait_for(timeout=30000)
        except Exception:  # noqa: BLE001 - capture whatever is on screen
            pass
        page.wait_for_timeout(2500)
        record_scene(page, "s5b_feedback", workdir, 14.0, scroll_to_bottom_slow(page))

        # --- scene 6: memory page
        page.goto(f"{BASE_URL}/memory", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1800)
        record_scene(page, "s6_memory", workdir, 10.0, scroll_to_bottom_slow(page))

        # --- scene 7: profile — details, GitHub evidence, role discovery
        page.goto(f"{BASE_URL}/profile", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1800)
        record_scene(page, "s7_profile", workdir, 12.0, scroll_to_bottom_slow(page))

        browser.close()

    # ------------------------------------------------------------ compose
    compose(workdir, out_path)


def compose(workdir: Path, out_path: Path) -> None:
    """Encode each scene, fade to black between them, concatenate."""
    ffmpeg = _ffmpeg()
    scene_order = ["s2_dashboard", "s3_search", "s4_detail",
                   "s5a_question", "s5b_feedback", "s6_memory", "s7_profile"]
    encoded: list[Path] = []
    for name in scene_order:
        d = workdir / name
        if not d.exists():
            continue
        # frames are JPG named f0000.jpg...
        mp4 = workdir / f"{name}.mp4"
        subprocess.run([
            ffmpeg, "-y", "-loglevel", "error",
            "-framerate", str(CAP_FPS), "-i", str(d / "f%04d.jpg"),
            "-vf", (
                f"fade=t=in:st=0:d=0.5,"
                f"fade=t=out:st={max(0.0, _scene_len(d) - 0.5)}:d=0.5,"
                f"fps={FPS}"
            ),
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", str(mp4),
        ], check=True)
        encoded.append(mp4)
        print(f"  encoded {mp4.name}")

    # title + outro cards need to become videos too
    for card, dur in (("title", 6.0), ("outro", 7.0)):
        mp4 = workdir / f"card_{card}.mp4"
        subprocess.run([
            ffmpeg, "-y", "-loglevel", "error",
            "-loop", "1", "-framerate", str(FPS), "-i", str(workdir / "cards" / f"{card}.jpg"),
            "-t", str(dur),
            "-vf", f"fade=t=in:st=0:d=0.6,fade=t=out:st={dur - 0.6}:d=0.6",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", str(mp4),
        ], check=True)
        encoded.insert(0, mp4) if card == "title" else encoded.append(mp4)
        print(f"  encoded card_{card}.mp4")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    list_file = workdir / "concat.txt"
    list_file.write_text("\n".join(f"file '{mp4}'" for mp4 in encoded) + "\n")
    subprocess.run([
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", str(out_path),
    ], check=True)
    print(f"WROTE {out_path}")


def _scene_len(d: Path) -> float:
    n = len(list(d.glob("*.jpg")))
    return n / CAP_FPS


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "demo" / "applycanary-demo.mp4"))
    ap.add_argument("--email", default=EMAIL)
    args = ap.parse_args()

    with tempfile.TemporaryDirectory(prefix="applycanary-video-") as tmp:
        run(Path(tmp), Path(args.out), args.email)
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
