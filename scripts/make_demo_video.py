#!/usr/bin/env python3
"""Record a scripted walkthrough of ApplyCanary and render a narrated demo MP4.

Drives a headless Chromium (Playwright) through the real running app — jobs
feed, typed search, job detail, AI interview studio, memory, profile — clicking
real navigation, then composes the frames with ffmpeg into a single ~3-minute
MP4 with title/outro cards, crossfades, and a female-voice narration track
generated with edge-tts (Microsoft neural voices, free, no key).

Requirements (all optional, install if missing):
    .venv/bin/pip install playwright imageio-ffmpeg Pillow edge-tts
    .venv/bin/playwright install chromium

The app must be running first (python run.py). The video records the account
given by EMAIL (default: stephenjacob815@gmail.com) via a signed session
token, so no password is needed.

Usage:
    .venv/bin/python scripts/make_demo_video.py [--out demo/applycanary-demo.mp4]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
EMAIL = os.environ.get("EMAIL", "stephenjacob815@gmail.com")
VIEW_W, VIEW_H = 1920, 1080
FPS = 30          # output frames per second
CAP_FPS = 10      # capture frames per second (duplicated to FPS)

TTS_VOICE = os.environ.get("TTS_VOICE", "en-US-AriaNeural")  # female, energetic
TTS_RATE = "+8%"

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# Nominal scene lengths in seconds (actual lengths come from captured frame
# counts so narration stays in sync). Cards are the compositor's static
# title/outro.
CARD_DUR = {"title": 10.0, "outro": 10.0}
SCENE_DUR = {
    "s2_dashboard": 26.0,
    "s3_search": 24.0,
    "s4_detail": 30.0,
    "s5a_question": 12.0,
    "s5b_feedback": 28.0,
    "s6_memory": 20.0,
    "s7_profile": 20.0,
}
SCENE_ORDER = [
    "s2_dashboard", "s3_search", "s4_detail", "s5a_question",
    "s5b_feedback", "s6_memory", "s7_profile",
]

# Narration per segment; each clip must stay shorter than its segment.
NARRATION = {
    "title": (
        "Welcome to ApplyCanary — your job search, remembered. "
        "An agentic job-search copilot on CockroachDB memory and AWS."
    ),
    "s2_dashboard": (
        "This is your dashboard. Around the clock, ApplyCanary polls dozens of "
        "job boards, scores every role against your profile, and tailors your "
        "resume automatically. Overnight, it discovered twenty-nine Go "
        "developer roles for you. Every score, every tailored version, every "
        "decision — remembered in CockroachDB."
    ),
    "s3_search": (
        "Search understands you. Type go developer, and it matches Golang "
        "roles, senior Go engineers — the whole family. Match percentage, "
        "skill coverage, location fit: computed instantly, and every query "
        "remembered."
    ),
    "s4_detail": (
        "Open a role and the memory layer goes to work. Here is the full score "
        "breakdown — skills you cover, gaps to close. A tailored resume and "
        "cover letter generated from this exact posting. Interview prep with "
        "company notes and predicted questions. And similar jobs, found with "
        "vector search over embeddings stored in CockroachDB."
    ),
    "s5a_question": (
        "Time for the interview studio. A live AI interviewer with a natural "
        "voice, asking real questions about this role — and listening to your "
        "answers."
    ),
    "s5b_feedback": (
        "Here is the feedback after your answer — structured scoring, what you "
        "did well, what to improve, and a model answer. Every session is saved "
        "to memory, so your next interview is sharper."
    ),
    "s6_memory": (
        "This is the agent's memory. Interview sessions, scores, tailored "
        "resumes, and the embeddings powering semantic search — all stored in "
        "CockroachDB, the distributed SQL database built for exactly this scale."
    ),
    "s7_profile": (
        "Your profile drives everything. Target roles, skills, and your GitHub "
        "activity — the agent reads it all, and keeps hunting for you. Set an "
        "email alert threshold, and it pings you the moment a great match "
        "appears."
    ),
    "outro": (
        "ApplyCanary — open source, MIT licensed. An agent that remembers: "
        "CockroachDB at the core, AWS at the edge."
    ),
}


def _ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _probe_duration(path: Path) -> float:
    out = subprocess.run([_ffmpeg(), "-i", str(path)],
                         capture_output=True, text=True).stderr
    for line in out.splitlines():
        if "Duration:" in line:
            hh, mm, ss = line.split("Duration:")[1].split(",")[0].strip().split(":")
            return int(hh) * 3600 + int(mm) * 60 + float(ss)
    return 0.0


# ---------------------------------------------------------------- cards


def make_card(path: Path, title: str, sub: str, accent: str = "#7aa2f7") -> None:
    """Minimal, tasteful title/outro card at VIEW_W x VIEW_H."""
    img = Image.new("RGB", (VIEW_W, VIEW_H), (13, 17, 23))
    d = ImageDraw.Draw(img)
    d.rectangle([VIEW_W // 2 - 2, 380, VIEW_W // 2 + 2, 620], fill=accent)
    t_font = ImageFont.truetype(FONT_BOLD, 84)
    s_font = ImageFont.truetype(FONT_REG, 34)
    tw = d.textlength(title, font=t_font)
    d.text(((VIEW_W - tw) / 2, 440), title, font=t_font, fill=(235, 240, 248))
    sw = d.textlength(sub, font=s_font)
    d.text(((VIEW_W - sw) / 2, 580), sub, font=s_font, fill=(150, 160, 175))
    img.save(path)


# ---------------------------------------------------------------- scenes


def record_scene(page: Any, name: str, workdir: Path, duration: float,
                 driver: Callable[[Any, int], None]) -> None:
    """Capture `duration` seconds of frames at CAP_FPS, calling `driver(page, tick)`
    on every tick to advance the UI (scroll, click, type…)."""
    import time

    out = workdir / name
    out.mkdir(parents=True, exist_ok=True)
    interval = 1.0 / CAP_FPS
    start = time.monotonic()
    i = 0
    total_ticks = int(duration * CAP_FPS)
    print(f"  scene {name} start URL: {page.url}")
    while time.monotonic() - start < duration:
        driver(page, i)
        # animations=disabled freezes CSS spinners so screenshots don't block
        # waiting for visual stability during loading/evaluating phases.
        page.screenshot(path=str(out / f"f{i:04d}.jpg"), type="jpeg", quality=82,
                        animations="disabled")
        i += 1
        elapsed = time.monotonic() - start
        target = i * interval
        if elapsed < target:
            page.wait_for_timeout(int((target - elapsed) * 1000))
    print(f"  scene {name}: {i}/{total_ticks} frames (end URL: {page.url})")


def _try_click(page: Any, locator_fn: Callable[[], Any], label: str,
               timeout: int = 2000) -> None:
    """Click with diagnostics — the video pipeline should never die on a UI miss."""
    try:
        locator_fn().click(timeout=timeout)
        print(f"    click OK: {label}")
    except Exception as e:  # noqa: BLE001 - best-effort UI driving
        print(f"    click FAILED: {label}: {type(e).__name__} {str(e)[:100]}")


def scroll_bottom_driver(px_per_tick: int = 46) -> Callable[[Any, int], None]:
    def driver(page: Any, tick: int) -> None:
        page.evaluate(f"window.scrollBy(0, {px_per_tick})")
        page.wait_for_timeout(40)
    return driver


def search_driver() -> Callable[[Any, int], None]:
    """Type the query with a typewriter effect, scroll results, click the top hit."""
    query = "go developer"
    state = {"typed": 0, "clicked": False}

    def driver(page: Any, tick: int) -> None:
        box = page.locator("input[type=search]")
        if tick == 0:
            box.click()
        if state["typed"] < len(query) and tick < 55:
            box.press_sequentially(query[state["typed"]], delay=80)
            state["typed"] += 1
        elif 70 <= tick < 150:
            page.evaluate("window.scrollBy(0, 55)")
        elif tick == 170 and not state["clicked"]:
            page.evaluate("window.scrollTo(0, 0)")
        elif tick >= 185 and not state["clicked"]:
            state["clicked"] = True
            _try_click(page, lambda: page.locator("tr.row-link").first, "top search result", 2000)
        page.wait_for_timeout(40)

    return driver


def detail_driver() -> Callable[[Any, int], None]:
    """Scroll the score breakdown / prep / similar jobs, then enter the interview."""
    state = {"clicked": False}

    def driver(page: Any, tick: int) -> None:
        if tick < 210:
            page.evaluate("window.scrollBy(0, 60)")
        elif tick == 240 and not state["clicked"]:
            page.evaluate("window.scrollTo(0, 0)")
        elif tick >= 255 and not state["clicked"]:
            state["clicked"] = True
            _try_click(page, lambda: page.get_by_role("button", name="AI Interview"),
                       "AI Interview", 2000)
        page.wait_for_timeout(40)

    return driver


def interview_start_driver() -> Callable[[Any, int], None]:
    """Switch to typed mode (no mic permission in headless), start, then scroll."""
    state = {"started": False}

    def driver(page: Any, tick: int) -> None:
        if tick == 2 and not state["started"]:
            _try_click(page, lambda: page.get_by_role("button", name="⌨ Typed"),
                       "Typed mode", 3000)
        if tick == 6 and not state["started"]:
            state["started"] = True
            _try_click(page, lambda: page.get_by_role("button", name="Start interview"),
                       "Start interview", 3000)
        if 20 <= tick < 90:
            page.evaluate("window.scrollBy(0, 40)")
        page.wait_for_timeout(40)

    return driver


def feedback_driver() -> Callable[[Any, int], None]:
    """Type an answer, submit, then poll for the feedback card while scrolling."""
    state = {"submitted": False, "waited": 0}

    def driver(page: Any, tick: int) -> None:
        if tick == 2 and not state["submitted"]:
            try:
                page.locator("textarea").fill(
                    "I have spent five years building Go services in production. "
                    "At my last company I owned the payments API: I designed it "
                    "around PostgreSQL and Redis, cut p99 latency by forty "
                    "percent, and led the migration to Kubernetes. What draws me "
                    "here is the scale of the platform and the chance to shape "
                    "the SDK surfaces developers touch."
                )
                print("    typed answer into textarea")
                _try_click(page, lambda: page.get_by_role("button", name="Submit typed answer"),
                           "Submit typed answer", 3000)
            except Exception as e:  # noqa: BLE001
                print(f"    answer fill FAILED: {type(e).__name__} {str(e)[:100]}")
            state["submitted"] = True
        if state["submitted"] and state["waited"] < 80:
            state["waited"] += 1
        if 30 <= tick < 160:
            page.evaluate("window.scrollBy(0, 50)")
        elif tick >= 175:
            page.evaluate("window.scrollBy(0, 40)")
        page.wait_for_timeout(40)

    return driver


def nav_driver(link_name: str) -> Callable[[Any, int], None]:
    state = {"clicked": False}

    def driver(page: Any, tick: int) -> None:
        if tick == 0 and not state["clicked"]:
            state["clicked"] = True
            _try_click(page, lambda: page.get_by_role("link", name=link_name, exact=True),
                       f"nav {link_name}", 3000)
        if tick >= 8:
            page.evaluate("window.scrollBy(0, 55)")
        page.wait_for_timeout(40)

    return driver


# ---------------------------------------------------------------- narration


def synth_narration(workdir: Path) -> None:
    """Generate per-segment narration MP3s with edge-tts (female neural voice)."""
    import edge_tts

    out_dir = workdir / "nar"
    out_dir.mkdir(parents=True, exist_ok=True)

    async def one(name: str, text: str) -> Path:
        path = out_dir / f"{name}.mp3"
        tts = edge_tts.Communicate(text, voice=TTS_VOICE, rate=TTS_RATE)
        await tts.save(str(path))
        return path

    async def all_clips() -> None:
        for name, text in NARRATION.items():
            try:
                path = await one(name, text)
                dur = _probe_duration(path)
                seg = CARD_DUR.get(name, SCENE_DUR.get(name, 0))
                flag = "" if dur < seg else "  <-- LONGER THAN SEGMENT!"
                print(f"  narration {name}: {dur:.1f}s (segment {seg:.0f}s){flag}")
            except Exception as e:  # noqa: BLE001 - audio is best-effort
                print(f"  narration {name}: FAILED ({e})")

    asyncio.run(all_clips())


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

    synth_narration(workdir)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": VIEW_W, "height": VIEW_H})
        page = ctx.new_page()
        ctx.add_cookies([{
            "name": "applycanary_session", "value": token,
            "domain": "127.0.0.1", "path": "/",
        }])

        # s2 — dashboard: slow scroll through the live feed
        page.goto(f"{BASE_URL}/", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1800)
        record_scene(page, "s2_dashboard", workdir, SCENE_DUR["s2_dashboard"],
                     scroll_bottom_driver())

        # s3 — typed search, results, click the top hit
        page.evaluate("window.scrollTo(0, 0)")
        record_scene(page, "s3_search", workdir, SCENE_DUR["s3_search"], search_driver())
        page.wait_for_timeout(2200)  # let the SPA route to the job detail

        # s4 — job detail (arrived via click), scroll, enter interview
        record_scene(page, "s4_detail", workdir, SCENE_DUR["s4_detail"], detail_driver())
        page.wait_for_timeout(2200)

        # s5 — interview studio: start, question, answer, feedback
        record_scene(page, "s5a_question", workdir, SCENE_DUR["s5a_question"],
                     interview_start_driver())
        page.wait_for_timeout(1200)
        record_scene(page, "s5b_feedback", workdir, SCENE_DUR["s5b_feedback"],
                     feedback_driver())
        page.wait_for_timeout(1800)

        # s6 — memory page via real nav click
        page.evaluate("window.scrollTo(0, 0)")
        record_scene(page, "s6_memory", workdir, SCENE_DUR["s6_memory"],
                     nav_driver("Memory"))
        page.wait_for_timeout(1800)

        # s7 — profile page via real nav click
        page.evaluate("window.scrollTo(0, 0)")
        record_scene(page, "s7_profile", workdir, SCENE_DUR["s7_profile"],
                     nav_driver("Profile"))

        browser.close()

    actual = {name: len(list((workdir / name).glob("*.jpg"))) / CAP_FPS
              for name in SCENE_ORDER if (workdir / name).exists()}
    print("  actual durations: " + ", ".join(f"{k}={v:.1f}s" for k, v in actual.items()))
    compose(workdir, out_path, actual)


# ---------------------------------------------------------------- compose


def compose(workdir: Path, out_path: Path, actual: dict[str, float]) -> None:
    """Encode each segment, fade to black between them, concatenate, mux narration."""
    ffmpeg = _ffmpeg()
    encoded: list[Path] = []
    for name in SCENE_ORDER:
        d = workdir / name
        if not d.exists():
            continue
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

    # title + outro cards become videos too
    for card, dur in CARD_DUR.items():
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
    silent = workdir / "silent.mp4"
    subprocess.run([
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", str(silent),
    ], check=True)

    _mux_narration(silent, out_path, workdir, actual)
    print(f"WROTE {out_path}")


def _mux_narration(silent: Path, out_path: Path, workdir: Path,
                   actual: dict[str, float]) -> None:
    """Place each narration clip at its segment's start offset and mix over the video.

    Offsets use the *actual* per-scene lengths (captured frame count / CAP_FPS)
    so audio stays in sync even when capture drifts from the nominal duration.
    """
    ffmpeg = _ffmpeg()
    timeline: list[tuple[str, float]] = []
    cursor = 0.0
    for name in ["title", *SCENE_ORDER, "outro"]:
        timeline.append((name, cursor))
        d = CARD_DUR.get(name)
        cursor += d if d is not None else actual[name]

    clips = [workdir / "nar" / f"{name}.mp3" for name, _ in timeline
             if (workdir / "nar" / f"{name}.mp3").exists()]
    if not clips:
        print("  no narration clips found; writing video without audio")
        subprocess.run([ffmpeg, "-y", "-loglevel", "error",
                        "-i", str(silent), "-c", "copy", str(out_path)], check=True)
        return

    inputs: list[str] = ["-i", str(silent)]
    for c in clips:
        inputs += ["-i", str(c)]
    flt: list[str] = []
    idx = 0
    for name, start in timeline:
        c = workdir / "nar" / f"{name}.mp3"
        if not c.exists():
            continue
        ms = int(start * 1000)
        flt.append(f"[{idx + 1}:a]adelay={ms}|{ms}[a{idx}]")
        idx += 1
    mix_in = "".join(f"[a{i}]" for i in range(idx))
    total = cursor
    flt.append(
        f"{mix_in}amix=inputs={idx}:normalize=0,"
        f"afade=t=in:st=0:d=0.5,afade=t=out:st={max(0.0, total - 1.0)}:d=1.0[aout]"
    )
    final = out_path.with_suffix(".mux.mp4")
    subprocess.run([
        ffmpeg, "-y", "-loglevel", "error", *inputs,
        "-filter_complex", ";".join(flt),
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        str(final),
    ], check=True)
    final.replace(out_path)


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
