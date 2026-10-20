"""Web Push (VAPID) notification sender."""

from __future__ import annotations

import json
import logging

from app.config import get_settings

log = logging.getLogger(__name__)


def push_enabled() -> bool:
    s = get_settings()
    return bool(s.vapid_private_key and s.vapid_public_key and s.vapid_email)


def send_push(
    endpoint: str,
    p256dh: str,
    auth: str,
    title: str,
    body: str,
    url: str = "/",
) -> str:
    """Send a Web Push message. Returns 'ok', 'stale', or 'error'."""
    if not push_enabled():
        log.debug("VAPID not configured, skipping push")
        return "error"
    try:
        from pywebpush import webpush  # lazy import

        s = get_settings()
        payload = json.dumps({"title": title, "body": body, "url": url})
        webpush(
            subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
            data=payload,
            vapid_private_key=s.vapid_private_key,
            vapid_claims={"sub": s.vapid_email},
        )
        return "ok"
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "410" in msg or "404" in msg or "Gone" in msg:
            log.info("Stale push subscription removed: %s", endpoint[:60])
            return "stale"
        log.warning("Push send failed: %s", msg)
        return "error"
