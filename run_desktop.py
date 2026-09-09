#!/usr/bin/env python3
"""Desktop entrypoint: starts the backend and opens a native window.

    python run_desktop.py              # opens ApplyCanary in a desktop window
    python run_desktop.py --dev        # dev mode: connects to running server

On macOS the native webview is WKWebView (Safari engine). On Linux it is
WebKitGTK. On Windows it is the Edge Chromium webview. No browser install
needed — pywebview uses whatever the OS ships.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time


def _wait_for_server(host: str, port: int, timeout: float = 30.0) -> bool:
    """Block until the FastAPI server is accepting connections."""
    import socket

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1", help="bind address")
    parser.add_argument("--port", type=int, default=0,
                        help="port (0 = random free port)")
    parser.add_argument("--dev", action="store_true",
                        help="skip starting the server; connect to an existing one")
    parser.add_argument("--url", default="",
                        help="full URL to open (overrides --host/--port)")
    args = parser.parse_args()

    # Ensure scheduler is enabled (the whole point of the desktop app is
    # always-on job discovery).
    os.environ.setdefault("ENABLE_SCHEDULER", "true")
    os.environ.setdefault("HOST", args.host)

    server_thread: threading.Thread | None = None
    port = args.port
    url = args.url

    if not args.dev and not url:
        # Pick a random free port when the user asked for 0.
        if port == 0:
            import socket
            with socket.socket() as s:
                s.bind((args.host, 0))
                port = s.getsockname()[1]
            os.environ["PORT"] = str(port)

        from app.config import get_settings
        settings = get_settings()
        settings.ensure_dirs()

        for warning in settings.startup_warnings():
            print(f"  ! {warning}", file=sys.stderr)

        def _run_server() -> None:
            import uvicorn

            from app.main import app as target
            uvicorn.run(
                target,
                host=args.host,
                port=port or settings.port,
                log_level=settings.log_level.lower(),
            )

        server_thread = threading.Thread(target=_run_server, daemon=True)
        server_thread.start()

        if not _wait_for_server(args.host, port or settings.port):
            print("ERROR: backend server failed to start within 30s",
                  file=sys.stderr)
            return 1

        url = f"http://{args.host}:{port or settings.port}"

    if not url:
        url = f"http://{args.host}:{port}"

    # --- pywebview: native window -----------------------------------------
    try:
        import webview
    except ImportError:
        print(
            "pywebview is not installed. Install it:\n"
            "  pip install pywebview\n\n"
            f"Or open {url} in your browser.",
            file=sys.stderr,
        )
        # Fall back to opening in the default browser.
        import webbrowser
        webbrowser.open(url)
        # Keep the server alive if we started one.
        if server_thread:
            server_thread.join()
        return 0

    webview.create_window(
        title="ApplyCanary — AI Career Agent",
        url=url,
        width=1280,
        height=860,
        min_size=(900, 600),
        text_select=True,
    )
    webview.start(debug=args.dev)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
