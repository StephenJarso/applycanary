"""Connector health: which sources have quietly stopped producing.

The dashboard already shows a failing run, but a board that 404s every cycle
looks the same as one that is merely between hiring rounds unless somebody
reads the status page. This module answers the question that matters — has this
source produced anything in the last day? — and the scheduler emails the answer.

Two failures are worth telling apart:

* **failing** — the last run errored and has not succeeded for ``hours``. The
  token is wrong, the site moved, the API changed.
* **zero-yield** — every run succeeds but returns no postings, for a full
  ``hours``. Markup changed silently, or the board moved to a new URL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.models import SourceRun, utcnow

DEFAULT_HOURS = 24.0
# Enough history to span a day of five-minute polls for a handful of boards.
WINDOW = 2000


@dataclass(slots=True)
class StaleSource:
    source: str
    reason: str
    last_ok_at: datetime | None
    last_run_at: datetime
    runs: int


def stale_sources(
    session: Session,
    *,
    now: datetime | None = None,
    hours: float = DEFAULT_HOURS,
    window: int = WINDOW,
) -> list[StaleSource]:
    """Sources that have produced nothing for ``hours``, newest problem first."""
    now = now or utcnow()
    cutoff = timedelta(hours=hours)

    rows = session.exec(
        select(SourceRun).order_by(SourceRun.started_at.desc()).limit(window)
    ).all()

    grouped: dict[str, list[SourceRun]] = {}
    for run in rows:
        grouped.setdefault(run.source, []).append(run)

    stale: list[StaleSource] = []
    for source, runs in grouped.items():
        latest = runs[0]
        if latest.ok and latest.found > 0:
            continue

        last_ok = next((run for run in runs if run.ok), None)

        if not latest.ok:
            # Failing: only worth an alert once the source has been down for the
            # whole window, not because one poll hit a timeout.
            if last_ok is not None and now - last_ok.started_at < cutoff:
                continue
            reason = (
                f"failing since {last_ok.started_at:%Y-%m-%d %H:%M} UTC"
                if last_ok is not None
                else "has never completed a successful run"
            )
            stale.append(
                StaleSource(
                    source=source,
                    reason=reason,
                    last_ok_at=last_ok.started_at if last_ok is not None else None,
                    last_run_at=latest.started_at,
                    runs=len(runs),
                )
            )
            continue

        # Zero-yield: every run since the streak began succeeded with nothing.
        streak = 0
        for run in runs:
            if not run.ok or run.found > 0:
                break
            streak += 1
        oldest = runs[streak - 1].started_at
        if now - oldest < cutoff:
            continue
        stale.append(
            StaleSource(
                source=source,
                reason=f"no postings in its last {streak} runs",
                last_ok_at=oldest,
                last_run_at=latest.started_at,
                runs=len(runs),
            )
        )

    stale.sort(key=lambda item: item.last_ok_at or datetime.min)
    return stale


def alert_body(stale: list[StaleSource]) -> tuple[str, str, str]:
    """Subject, HTML and plain text for the daily health email."""
    subject = f"ApplyCanary: {len(stale)} source(s) have stopped producing"
    lines = [
        f"{item.source}: {item.reason} (last run {item.last_run_at:%Y-%m-%d %H:%M} UTC)"
        for item in stale
    ]
    text = (
        "These job sources have produced nothing for at least a day.\n\n"
        + "\n".join(lines)
        + "\n\nCheck the Sources page, or pause the board in Admin > Boards.\n"
    )
    html = (
        "<p>These job sources have produced nothing for at least a day:</p><ul>"
        + "".join(f"<li><strong>{item.source}</strong>: {item.reason}</li>" for item in stale)
        + "</ul><p>Check the Sources page, or pause the board in "
        "Admin &gt; Boards &amp; Programmes.</p>"
    )
    return subject, html, text
