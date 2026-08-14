"""Artifact storage: interview audio recordings.

Primary path: **Amazon S3** — recordings land in `s3://<bucket>/<prefix>/`
with per-user object keys. Fallback: local disk under the data dir. The return
value is an opaque storage key; `audio_key` and `audio_url` are the only ways
the rest of the app touches objects, so swapping storage backends later (or
adding lifecycle rules) touches one module.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.config import get_settings

log = logging.getLogger(__name__)


async def save_audio(
    *,
    user_id: int,
    session_id: int,
    question_index: int,
    audio_bytes: bytes,
) -> str:
    """Persist one recorded answer; returns its storage key."""
    key = f"interviews/user_{user_id}/session_{session_id}/q{question_index}.pcm"
    settings = get_settings()
    if settings.s3_enabled:
        await asyncio.to_thread(_upload_s3, key, audio_bytes)
        return f"s3://{settings.s3_bucket}/{key}"
    path = settings.data_dir / "audio" / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(audio_bytes)
    return str(path)


def _upload_s3(key: str, audio_bytes: bytes) -> None:
    from app.aws import client  # noqa: PLC0415

    settings = get_settings()
    s3 = client("s3")
    if s3 is None:
        # Degrade to local disk rather than losing the recording.
        path = settings.data_dir / "audio" / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio_bytes)
        log.warning("s3 unavailable; wrote audio to local disk")
        return
    s3.put_object(
        Bucket=settings.s3_bucket,
        Key=f"{settings.s3_prefix}/{key}",
        Body=audio_bytes,
        ContentType="audio/L16",
    )


def audio_url(key: str) -> str:
    """HTTP URL for a stored recording, if one is derivable."""
    if key.startswith("s3://"):
        bucket, _, rest = key[5:].partition("/")
        return f"https://{bucket}.s3.amazonaws.com/{rest}"
    path = Path(key)
    return f"/audio/{path.name}" if path.exists() else ""
