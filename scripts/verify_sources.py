#!/usr/bin/env python3
"""Smoke-test every job source before anything depends on it.

Uses only the standard library so it runs before `pip install`, which makes it a
genuine first step rather than something gated on a working environment.

    python3 scripts/verify_sources.py
    python3 scripts/verify_sources.py --platform greenhouse --token stripe
    python3 scripts/verify_sources.py --json

Exit status is 1 if any probe fails, so it works in CI.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any

TIMEOUT = 20
UA = "applycanary/0.1 (personal job search agent)"

# (label, url, how to reach the job list, fields we rely on)
PROBES: list[dict[str, Any]] = [
    {
        "label": "greenhouse",
        "url": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
        "default_token": "stripe",
        "list_path": ["jobs"],
        "expect_fields": ["id", "title", "absolute_url", "location", "content"],
    },
    {
        "label": "lever",
        "url": "https://api.lever.co/v0/postings/{token}?mode=json",
        "default_token": "spotify",
        "list_path": [],  # bare array
        "expect_fields": ["id", "text", "hostedUrl", "categories"],
    },
    {
        "label": "ashby",
        "url": "https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true",
        "default_token": "ramp",
        "list_path": ["jobs"],
        "expect_fields": ["id", "title", "jobUrl", "location"],
    },
    {
        "label": "smartrecruiters",
        "url": "https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=10",
        "default_token": "Visa",
        "list_path": ["content"],
        "expect_fields": ["id", "name", "ref", "location"],
    },
    {
        "label": "workable",
        "url": "https://apply.workable.com/api/v1/widget/accounts/{token}?details=true",
        "default_token": "zapier",
        "list_path": ["jobs"],
        "expect_fields": ["id", "title", "url"],
    },
    {
        "label": "remoteok",
        "url": "https://remoteok.com/api",
        "default_token": "",
        "list_path": [],
        "expect_fields": ["id", "position", "company"],
    },
    {
        "label": "hn_hiring",
        "url": "https://hacker-news.firebaseio.com/v0/user/whoishiring.json",
        "default_token": "",
        "list_path": [],
        "expect_fields": [],
    },
]


@dataclass
class Result:
    label: str
    url: str
    ok: bool
    status: int | None = None
    elapsed_ms: int = 0
    count: int | None = None
    missing_fields: list[str] = field(default_factory=list)
    sample_keys: list[str] = field(default_factory=list)
    error: str = ""


def fetch(url: str) -> tuple[int, Any]:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
        return resp.status, json.loads(resp.read().decode("utf-8", errors="replace"))


def dig(payload: Any, path: list[str]) -> Any:
    for key in path:
        if not isinstance(payload, dict):
            return None
        payload = payload.get(key)
    return payload


def probe(spec: dict[str, Any], token_override: str | None = None) -> Result:
    token = token_override or spec.get("default_token") or ""
    url = spec["url"].replace("{token}", token)
    started = time.monotonic()
    try:
        status, payload = fetch(url)
    except urllib.error.HTTPError as exc:
        return Result(spec["label"], url, False, exc.code,
                      int((time.monotonic() - started) * 1000),
                      error=f"HTTP {exc.code} {exc.reason}")
    except Exception as exc:  # noqa: BLE001
        return Result(spec["label"], url, False, None,
                      int((time.monotonic() - started) * 1000),
                      error=f"{type(exc).__name__}: {exc}")

    elapsed = int((time.monotonic() - started) * 1000)
    items = dig(payload, spec["list_path"]) if spec["list_path"] else payload

    if not isinstance(items, list):
        # HN's endpoint returns an object; that is expected, not a failure.
        if spec["label"] == "hn_hiring" and payload:
            return Result(spec["label"], url, True, status, elapsed, count=None,
                          sample_keys=sorted(payload)[:8] if isinstance(payload, dict) else [])
        return Result(spec["label"], url, False, status, elapsed,
                      error=f"expected a list at {spec['list_path'] or 'root'}, "
                            f"got {type(items).__name__}")

    # Sample the first element that actually looks like a posting. RemoteOK
    # puts a legal notice at index 0, so naively sampling items[0] reports
    # field drift on a connector that is working fine.
    sample = next(
        (i for i in items
         if isinstance(i, dict) and not i.get("legal")
         and any(f in i for f in spec["expect_fields"])),
        next((i for i in items if isinstance(i, dict)), {}),
    )
    # An empty board is not drift: there is nothing to check the shape against.
    missing = [f for f in spec["expect_fields"] if f not in sample] if sample else []
    return Result(
        spec["label"], url, True, status, elapsed,
        count=len(items),
        missing_fields=missing,
        sample_keys=sorted(sample)[:14],
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--platform", help="probe only this platform")
    ap.add_argument("--token", help="board token to use with --platform")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    specs = PROBES
    if args.platform:
        specs = [p for p in PROBES if p["label"] == args.platform]
        if not specs:
            print(f"unknown platform {args.platform!r}; "
                  f"choose from {', '.join(p['label'] for p in PROBES)}", file=sys.stderr)
            return 2

    results = [probe(spec, args.token) for spec in specs]

    if args.as_json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        width = max(len(r.label) for r in results)
        for r in results:
            mark = "LIVE" if r.ok else "DEAD"
            head = f"[{mark}] {r.label.ljust(width)}  {r.elapsed_ms:>5}ms"
            if r.ok:
                count = "n/a" if r.count is None else str(r.count)
                print(f"{head}  items={count}")
                if r.missing_fields:
                    print(f"         ! missing expected fields: {', '.join(r.missing_fields)}")
                if r.sample_keys:
                    print(f"         keys: {', '.join(r.sample_keys)}")
            else:
                print(f"{head}  {r.error}")
        live = sum(1 for r in results if r.ok)
        drift = sum(1 for r in results if r.ok and r.missing_fields)
        print(f"\n{live}/{len(results)} live" + (f", {drift} with field drift" if drift else ""))
        if drift:
            print("Field drift means a connector's parser needs updating.")

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
