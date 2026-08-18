"""FastAPI application factory.

The scheduler starts inside the app lifespan so a single `python run.py` runs both
the dashboard and the 24/7 polling loop.

Binding: the app defaults to 127.0.0.1. This service holds a resume, an API key
and (optionally) ATS credentials, and has no authentication layer — see the note
in run.py before exposing it.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import init_db

log = logging.getLogger(__name__)


def configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # These are chatty at DEBUG and drown out our own lines.
    for noisy in ("httpx", "httpcore", "apscheduler.executors.default"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201, ARG001
    configure_logging()
    settings = get_settings()

    if problems := settings.startup_errors():
        for problem in problems:
            log.error("refusing to start: %s", problem)
        raise RuntimeError("; ".join(problems))

    init_db()
    log.info("database ready at %s", settings.db_path)

    if not settings.llm_enabled:
        log.warning(
            "no LLM API key configured (XAI_API_KEY, GEMINI_API_KEY, "
            "OPENROUTER_API_KEY, GROQ_API_KEY or ANTHROPIC_API_KEY) — "
            "scoring falls back to keyword-only "
            "and tailoring/interview prep are disabled"
        )
    if not settings.email_enabled:
        log.warning(
            "no email backend configured (set RESEND_API_KEY or SMTP_HOST) — "
            "digests will be logged, not emailed"
        )
    if settings.enable_auto_submit:
        log.warning(
            "AUTO-SUBMIT IS ON: applications scoring >= %s may be sent without review",
            settings.auto_submit_min_score,
        )
    else:
        log.info("auto-submit is off — applications wait in the review queue")

    scheduler = None
    if settings.enable_scheduler:
        from app import scheduler as sched

        scheduler = sched.start()
    else:
        log.info("scheduler disabled (ENABLE_SCHEDULER=false)")

    try:
        yield
    finally:
        if scheduler is not None:
            from app import scheduler as sched

            sched.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="ApplyCanary",
        description="Self-hosted job discovery, ATS resume tailoring and application tracking.",
        version="0.1.0",
        lifespan=lifespan,
    )
    request_windows: dict[tuple[str, str], deque[float]] = defaultdict(deque)
    limits = {"auth": (10, 60.0), "api-write": (30, 60.0), "api-read": (120, 60.0)}

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):  # noqa: ANN001, ANN202
        from fastapi.responses import JSONResponse, RedirectResponse
        from sqlmodel import Session

        from app.auth import resolve_current_user
        from app.db import engine

        path = request.url.path
        bucket = "auth" if path.startswith("/api/auth/") else ("api-write" if path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"} else "api-read" if path.startswith("/api/") else "")
        if bucket:
            now = time.monotonic()
            window = request_windows[(request.client.host if request.client else "unknown", bucket)]
            maximum, period = limits[bucket]
            while window and now - window[0] >= period:
                window.popleft()
            if len(window) >= maximum:
                retry_after = max(1, int(period - (now - window[0])))
                return JSONResponse(status_code=429, content={"detail": "Too many requests; please try again later."}, headers={"Retry-After": str(retry_after)})
        if (
            path == "/health"
            or path in ("/login", "/register", "/api/auth/login", "/api/auth/register", "/api/auth/signup-info")
            or path.startswith("/assets/")
            or path == "/guest"
            or path.startswith("/guest/")
            or path.startswith("/api/public/")
            or path == "/favicon.ico"
        ):
            return await call_next(request)

        # Resolve the caller once here and hand it to the handlers, rather than
        # each of them re-deriving identity from the cookie. `request.state` is
        # set before routing, so the `current_user` dependency and the SPA
        # catch-alls (which take no Request) both read the same answer.
        with Session(engine) as session:
            user = resolve_current_user(request, session)
            request.state.user_id = user.id if user is not None else None

        if user is None:
            if path.startswith("/api/"):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Authentication required"},
                    headers={"WWW-Authenticate": 'Basic realm="ApplyCanary"'},
                )
            return RedirectResponse(url="/login", status_code=303)

        return await call_next(request)

    @app.get("/health", include_in_schema=False)
    def health() -> dict:
        from app import scheduler as sched
        scheduler = sched.get_scheduler()
        return {
            "ok": True,
            "scheduler_running": bool(scheduler and scheduler.running),
        }

    from app.api import auth, interview, router

    # API routes are registered before the SPA catch-all.
    app.include_router(auth.router)
    app.include_router(router.router)
    app.include_router(interview.router)
    _mount_spa(app, settings)

    return app


def _mount_spa(app: FastAPI, settings: object) -> None:
    """Serve the built React application from the site root."""
    from fastapi.responses import FileResponse, PlainTextResponse

    dist = settings.frontend_dist  # type: ignore[attr-defined]
    index = dist / "index.html"
    if not index.exists():
        @app.get("/{path:path}", include_in_schema=False)
        def frontend_not_built(path: str = "") -> PlainTextResponse:  # noqa: ARG001
            return PlainTextResponse("Frontend is not built. Run: cd frontend && npm run build", status_code=503)
        return

    assets = dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str = "") -> FileResponse:  # noqa: ARG001
        return FileResponse(index)

    log.info("React dashboard mounted at /")


app = create_app()
