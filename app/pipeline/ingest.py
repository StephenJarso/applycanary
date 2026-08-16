"""Ingest orchestration.

Runs connectors concurrently with per-source error isolation: one dead board must
never stall or abort a poll cycle. Every run is recorded as a SourceRun so a
silently-broken connector shows up on the dashboard instead of just going quiet.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

from app.db import session_scope
from app.models import SourceRun, utcnow
from app.pipeline.dedup import resolve
from app.sources import all_sources
from app.sources.base import DEFAULT_TIMEOUT, USER_AGENT, BaseSource

log = logging.getLogger(__name__)

# A slow board must not hold the cycle open indefinitely.
PER_SOURCE_TIMEOUT = 90.0
MAX_CONCURRENT_SOURCES = 6

CONFIG_PATH = Path("companies.yaml")

# Circuit breaker: a source that failed its last run is skipped for this long,
# so a dead board token (404) or a temporarily down site is not hit every
# cycle. A wrong token 404s forever; hammering it 12x/hour only slows the
# cycle and pollutes the logs.
SOURCE_BACKOFF_SECONDS = 1800.0  # 30 minutes
# Monotonic clock of the last failure per source label. Module-level so it
# survives across poll cycles within the process.
_source_backoff: dict[str, float] = {}


@dataclass(slots=True)
class IngestSummary:
    sources_run: int = 0
    sources_failed: int = 0
    found: int = 0
    new: int = 0
    duplicates: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def load_config(path: Path = CONFIG_PATH) -> dict:
    if not path.exists():
        log.warning("%s not found; no curated companies configured", path)
        return {"companies": [], "aggregators": []}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        log.error("%s is not valid YAML: %s", path, exc)
        return {"companies": [], "aggregators": []}
    return {
        "companies": data.get("companies") or [],
        "aggregators": data.get("aggregators") or [],
    }


def build_curated(config: dict, client: httpx.AsyncClient) -> list[BaseSource]:
    """Instantiate one connector per curated company entry."""
    registry = all_sources()
    built: list[BaseSource] = []
    for entry in config.get("companies", []):
        if not isinstance(entry, dict):
            continue
        platform = str(entry.get("platform") or "").strip()
        token = str(entry.get("token") or "").strip()
        if not platform or not token:
            log.warning("skipping malformed companies.yaml entry: %r", entry)
            continue
        cls = registry.get(platform)
        if cls is None:
            log.warning("unknown platform %r in companies.yaml (have: %s)",
                        platform, ", ".join(sorted(registry)))
            continue
        try:
            built.append(cls(board_token=token,
                             company_name=str(entry.get("name") or ""),
                             client=client))
        except TypeError as exc:
            log.warning("cannot build %s/%s: %s", platform, token, exc)
    return built


def build_aggregators(config: dict, client: httpx.AsyncClient) -> list[BaseSource]:
    registry = all_sources()
    built: list[BaseSource] = []
    for entry in config.get("aggregators", []):
        if not isinstance(entry, dict) or entry.get("enabled") is False:
            continue
        name = str(entry.get("source") or "").strip()
        cls = registry.get(name)
        if cls is None:
            log.warning("unknown aggregator %r", name)
            continue
        kwargs = {k: v for k, v in entry.items() if k not in ("source", "enabled")}
        try:
            built.append(cls(client=client, **kwargs))
        except TypeError as exc:
            log.warning("cannot build aggregator %s: %s", name, exc)
    return built


async def run_sources(sources: list[BaseSource]) -> IngestSummary:
    """Fetch from every source concurrently, then persist sequentially.

    Persistence is deliberately serialised: dedup reads and writes the same
    tables, and concurrent writers on SQLite would contend for the write lock
    while producing interleaved, harder-to-reason-about dedup decisions.
    """
    summary = IngestSummary()
    if not sources:
        return summary

    sem = asyncio.Semaphore(MAX_CONCURRENT_SOURCES)

    async def fetch_one(src: BaseSource) -> tuple[BaseSource, list, str, int]:
        started = time.monotonic()
        async with sem:
            try:
                async with asyncio.timeout(PER_SOURCE_TIMEOUT):
                    jobs = await src.fetch()
                return src, jobs, "", int((time.monotonic() - started) * 1000)
            except TimeoutError:
                return src, [], f"timed out after {PER_SOURCE_TIMEOUT:.0f}s", \
                    int((time.monotonic() - started) * 1000)
            except Exception as exc:  # noqa: BLE001 - isolate every source
                return src, [], f"{type(exc).__name__}: {exc}", \
                    int((time.monotonic() - started) * 1000)

    # Skip sources currently sitting out their backoff. The label is computed
    # once and reused so the cooldown key and the telemetry row agree.
    active: list[tuple[BaseSource, str]] = []
    now = time.monotonic()
    for src in sources:
        label = _label(src)
        if now < _source_backoff.get(label, 0.0):
            log.info("source %s: in backoff, skipping this cycle", label)
            summary.sources_failed += 1
            with session_scope() as session:
                session.add(SourceRun(
                    source=label, started_at=utcnow(), duration_ms=0,
                    found=0, new_jobs=0, duplicates=0, ok=False,
                    error="in backoff after previous failure; skipped this cycle",
                ))
            continue
        active.append((src, label))

    results = await asyncio.gather(*(fetch_one(s) for s, _ in active))

    for (_src, label), (_, raw_jobs, error, elapsed_ms) in zip(active, results, strict=True):
        summary.sources_run += 1
        new_count = dup_count = 0

        if error:
            summary.sources_failed += 1
            summary.errors.append(f"{label}: {error}")
            log.error("source %s failed: %s", label, error)
            _source_backoff[label] = time.monotonic() + SOURCE_BACKOFF_SECONDS
        else:
            # A clean run lifts the breaker.
            _source_backoff.pop(label, None)
            summary.found += len(raw_jobs)
            try:
                new_count, dup_count = _persist(raw_jobs)
                summary.new += new_count
                summary.duplicates += dup_count
            except Exception as exc:  # noqa: BLE001
                error = f"persist failed: {type(exc).__name__}: {exc}"
                summary.sources_failed += 1
                summary.errors.append(f"{label}: {error}")
                log.exception("persisting %s failed", label)
                _source_backoff[label] = time.monotonic() + SOURCE_BACKOFF_SECONDS

        with session_scope() as session:
            session.add(SourceRun(
                source=label, started_at=utcnow(), duration_ms=elapsed_ms,
                found=len(raw_jobs), new_jobs=new_count, duplicates=dup_count,
                ok=not error, error=error,
            ))

        log.info("source %s: found=%d new=%d dup=%d in %dms%s",
                 label, len(raw_jobs), new_count, dup_count, elapsed_ms,
                 f" ERROR: {error}" if error else "")

    return summary


def _persist(raw_jobs: list) -> tuple[int, int]:
    new_count = dup_count = 0
    with session_scope() as session:
        for raw in raw_jobs:
            if not raw.title:
                continue
            if not raw.company:
                raw.company = str(raw.source or "Unknown").title()
            result = resolve(session, raw.to_job())
            if result.is_new:
                new_count += 1
            else:
                dup_count += 1
            # Flush per job so the next resolve() sees it and duplicates within
            # a single batch are caught, not just across batches.
            session.flush()
    return new_count, dup_count


def _label(src: BaseSource) -> str:
    token = getattr(src, "board_token", "")
    return f"{src.name}/{token}" if token else src.name


async def _run(sources_factory) -> IngestSummary:  # noqa: ANN001
    async with httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    ) as client:
        sources = sources_factory(load_config(), client)
        for src in sources:
            src._client = client  # noqa: SLF001 - share one pooled client
            src._owns_client = False  # noqa: SLF001
        return await run_sources(sources)


async def poll_curated() -> IngestSummary:
    """Curated company boards. Polled often; this is the fast-apply edge."""
    return await _run(build_curated)


async def poll_broad() -> IngestSummary:
    """Aggregators and regional boards. Noisier, polled less often."""
    return await _run(build_aggregators)
