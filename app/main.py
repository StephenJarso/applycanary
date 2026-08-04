"""FastAPI application factory.

The scheduler starts inside the app lifespan so a single `python run.py` runs both
the dashboard and the 24/7 polling loop.

Binding: the app defaults to 127.0.0.1. This service holds a resume, an API key
and (optionally) ATS credentials, and has no authentication layer — see the note
in run.py before exposing it.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
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

    init_db()
    log.info("database ready at %s", settings.db_path)

    if not settings.llm_enabled:
        log.warning(
            "ANTHROPIC_API_KEY is not set — scoring falls back to keyword-only "
            "and tailoring/interview prep are disabled"
        )
    if not settings.email_enabled:
        log.warning("SMTP is not configured — digests will be logged, not emailed")
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
        title="Job Matcher",
        description="Self-hosted job discovery, ATS resume tailoring and application tracking.",
        version="0.1.0",
        lifespan=lifespan,
    )

    from app.web import routes

    app.include_router(routes.router)

    static_dir = settings.static_dir
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


app = create_app()
