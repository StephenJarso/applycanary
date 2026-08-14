"""Amazon Polly text-to-speech for the spoken interviewer.

The coach's questions are synthesized with a neural voice and returned as MP3.
Results are cached in memory by (voice, engine, text) so replaying a question —
or a judge replaying the demo — costs one API call, not one per play.

Without AWS credentials this returns None and the frontend falls back to the
browser's built-in speechSynthesis, so the voice interview still works offline.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import get_settings

log = logging.getLogger(__name__)

_cache: dict[tuple[str, str, str], bytes] = {}


async def synthesize(text: str) -> bytes | None:
    """Synthesize `text` to MP3 bytes, or None when Polly is unavailable."""
    settings = get_settings()
    if not settings.polly_enabled:
        return None
    key = (settings.polly_voice_id, settings.polly_engine, text)
    if key in _cache:
        return _cache[key]

    def _call() -> bytes | None:
        from app.aws import client  # noqa: PLC0415

        polly = client("polly")
        if polly is None:
            return None
        try:
            response = polly.synthesize_speech(
                Engine=settings.polly_engine,
                VoiceId=settings.polly_voice_id,
                OutputFormat="mp3",
                Text=text[:3000],
            )
        except Exception:  # noqa: BLE001
            log.warning("polly synthesis failed", exc_info=True)
            return None
        stream = response.get("AudioStream")
        if stream is None:
            return None
        return stream.read()

    audio = await asyncio.to_thread(_call)
    if audio:
        _cache[key] = audio
    return audio
