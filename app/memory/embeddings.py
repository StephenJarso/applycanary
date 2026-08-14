"""Text embedding generation.

Primary path: **Amazon Bedrock Titan** (`amazon.titan-embed-text-v2:0`) — the
same AWS account that serves the LLM calls also produces the vectors, so the
vector index and the operational database stay in one place with no separate
embedding service to operate.

Fallback: a deterministic, dependency-free hashing embedder. It is not as
expressive as a learned model, but it is stable across processes and machines,
which is what vector persistence requires. The app stays fully functional
without AWS credentials — local dev, CI, and the test suite all run on it.

Embeddings are always normalised to unit length so cosine distance is a pure
dot product.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re

from app.config import get_settings

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")

# Simple in-process cache: vectors are immutable per (text, dims) and job
# descriptions get re-embedded often. Keyed by text + dims because the local
# embedder and Titan both normalize, so the result depends only on those two.
_cache: dict[tuple[str, int], list[float]] = {}


def _tokenize(text: str) -> list[str]:
    """Lowercased alphanumeric tokens, including hyphenated terms."""
    return _TOKEN_RE.findall(text.lower())


async def embed_text(text: str, dims: int | None = None) -> list[float]:
    """Embed `text` into a unit vector of `dims` (default from settings)."""
    settings = get_settings()
    target = dims or settings.embedding_dims
    key = (text, target)
    if key in _cache:
        return _cache[key]

    vector: list[float] | None = None
    if settings.aws_enabled:
        vector = await _titan_embedding(text, target)
        if vector is None:
            log.info("bedrock embedding unavailable; using local hashing embedder")

    vector = vector or _hash_embedding(text, target)
    _cache[key] = vector
    return vector


async def _titan_embedding(text: str, dims: int) -> list[float] | None:
    """Bedrock Titan embedding; None on any failure (caller falls back)."""
    try:
        from app.aws import client  # noqa: PLC0415

        bedrock = client("bedrock-runtime")
        if bedrock is None:
            return None
        settings = get_settings()
        body = {
            "inputText": text[:8000],
            "dimensions": min(dims, 2048),
            "normalize": True,
        }
        response = await asyncio.to_thread(
            _invoke_sync, bedrock, settings.bedrock_embedding_model_id, body
        )
        values = response.get("embedding")
        if not values:
            return None
        vec = [float(v) for v in values]
        return _normalize(vec)
    except Exception:  # noqa: BLE001 - any AWS hiccup falls back to local
        log.warning("bedrock embedding failed; using local hashing embedder", exc_info=True)
        return None


def _invoke_sync(bedrock, model_id: str, body: dict) -> dict:  # noqa: ANN001
    import json  # noqa: PLC0415

    response = bedrock.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(body),
    )
    return json.loads(response["body"].read())


def _hash_embedding(text: str, dims: int) -> list[float]:
    """Deterministic hashed bag-of-tokens embedding.

    Tokens (and 2-grams, for a little phrase sensitivity) are hashed into
    buckets with sublinear weighting, then normalised. Two texts that share
    vocabulary land close together; totally unrelated texts are near-orthogonal.
    Deterministic across runs and machines — required for stored vectors.
    """
    vec = [0.0] * dims
    tokens = _tokenize(text)
    if not tokens:
        return vec
    grams: list[str] = list(tokens)
    grams += [f"{a}_{b}" for a, b in zip(tokens, tokens[1:], strict=False)]
    for gram in grams:
        digest = hashlib.sha256(gram.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % dims
        sign = 1.0 if digest[4] & 1 else -1.0
        weight = 1.0 + 4.0 / (1.0 + math.log1p(abs(int.from_bytes(digest[5:8], "big")) % 100))
        vec[bucket] += sign * weight
    return _normalize(vec)


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity for unit vectors — a plain dot product."""
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=False))
