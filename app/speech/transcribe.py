"""Amazon Transcribe streaming speech-to-text for interview answers.

The frontend records raw 16 kHz mono PCM (Int16 LE) — the format Transcribe's
streaming API expects — and posts the whole answer as one blob. This module
chunks it into ~100 ms AudioEvent frames, streams them through the
transcribestreaming client, and stitches the final (non-partial) transcript
together.

Audio is capped server-side (90 s) so a stuck mic cannot stream forever and a
malicious payload cannot run up a bill. Without AWS credentials this returns
None and the frontend uses the browser's built-in SpeechRecognition instead.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator

from app.config import get_settings

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHUNK_BYTES = 3200  # 100 ms of 16 kHz mono Int16
MAX_AUDIO_BYTES = SAMPLE_RATE * 2 * 90  # 90 s ceiling


async def transcribe_audio(audio_bytes: bytes, language: str = "en-US") -> str | None:
    """Transcribe raw PCM audio to text, or None when Transcribe is unavailable."""
    settings = get_settings()
    if not settings.transcribe_enabled:
        return None
    if not audio_bytes:
        return ""

    payload = audio_bytes[:MAX_AUDIO_BYTES]

    def _call() -> str | None:
        from app.aws import client  # noqa: PLC0415

        transcribe = client("transcribestreaming")
        if transcribe is None:
            return None
        try:
            stream = transcribe.start_stream_transcription(
                LanguageCode=language,
                MediaSampleRateHertz=SAMPLE_RATE,
                MediaEncoding="pcm",
                AudioStream=_audio_events(payload),
            )
        except Exception:  # noqa: BLE001
            log.warning("transcribe streaming failed", exc_info=True)
            return None

        pieces: list[str] = []
        try:
            for event in stream["TranscriptResultStream"]:
                if "TranscriptEvent" not in event:
                    continue
                results = event["TranscriptEvent"].get("Transcript", {}).get("Results", [])
                for result in results:
                    if result.get("IsPartial"):
                        continue
                    for alt in result.get("Alternatives", []):
                        text = (alt.get("Transcript") or "").strip()
                        if text:
                            pieces.append(text)
        except Exception:  # noqa: BLE001
            log.warning("transcribe stream read failed", exc_info=True)
            return None
        return " ".join(pieces).strip() or None

    return await asyncio.to_thread(_call)


def _audio_events(payload: bytes) -> Iterator[dict]:
    """Yield 100 ms AudioEvent frames from a raw PCM blob."""
    for i in range(0, len(payload), CHUNK_BYTES):
        chunk = payload[i:i + CHUNK_BYTES]
        if chunk:
            yield {"AudioEvent": {"AudioChunk": chunk}}
