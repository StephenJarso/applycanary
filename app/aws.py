"""Thin, lazy boto3 client access with graceful degradation.

Every client is created on demand and cached. When AWS credentials are absent
the helpers return None and callers fall back to local/browser behaviour — the
app must stay fully functional without any AWS account (local dev, CI, tests).

All boto3 calls are synchronous; async callers wrap them with
`asyncio.to_thread` so the event loop is never blocked.
"""

from __future__ import annotations

import functools
import logging

from app.config import get_settings

log = logging.getLogger(__name__)


@functools.cache
def _session() -> object | None:
    """A boto3 Session bound to the configured credentials, or None."""
    settings = get_settings()
    if not settings.aws_enabled:
        return None
    try:
        import boto3  # noqa: PLC0415
    except ImportError:
        log.warning("boto3 is not installed; AWS features are disabled")
        return None
    return boto3.Session(
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        aws_session_token=settings.aws_session_token or None,
        region_name=settings.aws_region,
    )


def client(service: str) -> object | None:
    """A cached boto3 client for `service`, or None without credentials."""
    session = _session()
    if session is None:
        return None
    return session.client(service)
