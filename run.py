#!/usr/bin/env python3
"""Entrypoint: starts the dashboard and the background scheduler together.

    python run.py                 # dashboard + 24/7 polling
    python run.py --web-only      # dashboard only, no background jobs
    python run.py --port 9000

Security: binds 127.0.0.1 by default. There is no login screen, and the database
holds your resume, contact details and application history. Do not bind a public
interface without putting authentication in front of it.
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", help="override HOST")
    parser.add_argument("--port", type=int, help="override PORT")
    parser.add_argument("--web-only", action="store_true",
                        help="run the dashboard without background polling")
    parser.add_argument("--reload", action="store_true",
                        help="auto-reload on code changes (development)")
    args = parser.parse_args()

    # Set env before importing app.config, whose settings are cached on first use.
    if args.web_only:
        os.environ["ENABLE_SCHEDULER"] = "false"
    if args.host:
        os.environ["HOST"] = args.host
    if args.port:
        os.environ["PORT"] = str(args.port)

    try:
        import uvicorn
    except ImportError:
        print(
            "Dependencies are not installed. Run:\n"
            "  python3 -m venv .venv\n"
            "  .venv/bin/pip install -r requirements.txt\n"
            "  .venv/bin/python run.py",
            file=sys.stderr,
        )
        return 1

    from app.config import get_settings

    settings = get_settings()
    settings.ensure_dirs()

    for warning in settings.startup_warnings():
        print(f"  ! {warning}", file=sys.stderr)

    print(f"\n  ApplyCanary -> http://{settings.host}:{settings.port}\n")

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=args.reload,
        log_level=settings.log_level.lower(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
