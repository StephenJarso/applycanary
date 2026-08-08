"""Text normalisation shared by dedup and scoring.

Kept deliberately conservative. Over-normalising titles collapses genuinely
distinct requisitions (`Engineer II` vs `Engineer III`) into one row, which
silently hides a real opening — a worse failure than leaving a duplicate.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Legal-entity suffixes only. Words like "Labs", "Group" or "Technologies" are
# left alone because they distinguish real, separate companies.
_LEGAL_SUFFIXES = {
    "inc", "llc", "ltd", "limited", "corp", "corporation", "co", "plc",
    "gmbh", "ag", "bv", "nv", "sa", "sas", "srl", "spa", "oy", "ab",
    "aps", "pty", "pte", "kk", "kg", "ug", "oyj", "asa", "dba",
}

# Pure noise in posting titles: EU gender markers and re-posting decorations.
_TITLE_NOISE = re.compile(
    r"""
    \((?:\s*[mwfdxhn](?:\s*[/|]\s*[mwfdxhn])+\s*)\)   # (m/f/d), (m/w/d), (f/m/x)
    | \b[mwfdxhn](?:\s*[/|]\s*[mwfdxhn]){1,3}\b        # bare m/f/d
    | \((?:\s*(?:remote|hybrid|onsite|on-site|wfh)\s*)\)
    | \b(?:urgent|hiring\s+now|immediate\s+start|apply\s+now)\b
    | [☀-➿\U0001f300-\U0001faff]             # emoji
    """,
    re.IGNORECASE | re.VERBOSE,
)

_CITY_ALIASES = {
    "sf": "san francisco", "sfo": "san francisco", "nyc": "new york",
    "ny": "new york", "la": "los angeles", "blr": "bengaluru",
    "bangalore": "bengaluru", "bombay": "mumbai", "ldn": "london",
    "nbo": "nairobi", "ber": "berlin", "ams": "amsterdam",
}

_REMOTE_HINT = re.compile(
    r"\b(remote|anywhere|distributed|work\s*from\s*home|wfh|telecommute)\b",
    re.IGNORECASE,
)

# Tracking params that differ per aggregator for the identical posting.
_TRACKING_PARAMS = re.compile(
    r"^(utm_|ref$|referrer$|source$|src$|gh_src$|gh_jid_src$|lever-source|"
    r"fbclid$|gclid$|mc_cid$|mc_eid$|trk$|trackingId$|position$|pageNum$)",
    re.IGNORECASE,
)


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def norm_company(company: str) -> str:
    """`Acme Corp., Inc.` -> `acme`."""
    s = _collapse(company).lower()
    s = re.sub(r"[^\w\s&+-]", " ", s)
    tokens = [t for t in _collapse(s).split(" ") if t]
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens) or _collapse(company).lower()


def norm_title(title: str) -> str:
    """Strip noise but preserve seniority words and level numerals."""
    s = _TITLE_NOISE.sub(" ", _collapse(title).lower())
    # Drop a trailing location tail: "Backend Engineer - Nairobi, Kenya".
    s = re.sub(r"\s*[-–—|,]\s*(?:remote|hybrid|onsite|on-site)\b.*$", "", s)
    s = re.sub(r"[^\w\s/+#.-]", " ", s)
    return _collapse(s)


def is_remote(location: str, *, description: str = "", flag: bool | None = None) -> bool:
    if flag is not None:
        return flag
    if _REMOTE_HINT.search(location or ""):
        return True
    # Only trust the description's opening, where the work model is stated.
    return bool(_REMOTE_HINT.search((description or "")[:400]))


def location_bucket(location: str, *, remote: bool = False) -> str:
    """Coarse location key for the dedup fingerprint.

    Remote roles collapse to a single `remote` bucket. That intentionally merges
    `Remote - US` with `Remote - EU` for the same company and title: in practice
    these are far more often one posting duplicated across boards than two
    distinct requisitions, and the merged sighting is preserved as a JobAlias, so
    nothing is lost outright.
    """
    if remote or _REMOTE_HINT.search(location or ""):
        return "remote"
    s = _collapse(location).lower()
    if not s:
        return "unspecified"
    s = re.sub(r"[^\w\s,-]", " ", s)
    head = _collapse(re.split(r"[,/|]", s)[0])
    head = re.sub(r"\b(metro(politan)?|area|region|greater|downtown)\b", "", head)
    head = _collapse(head)
    return _CITY_ALIASES.get(head, head) or "unspecified"


def canonical_url(url: str) -> str:
    """Strip tracking params and normalise, so the same posting relabelled by an
    aggregator resolves to one key.

    Input without a host is returned unchanged. `urlsplit` does not raise on
    junk — it just yields an empty netloc — and rebuilding that produces a
    plausible-looking `https:///...` string. Since this value is dedup layer 2,
    such a string could collide across unrelated postings and merge distinct
    jobs, so anything that is not a real absolute URL is left alone.
    """
    if not url:
        return ""
    cleaned = url.strip()
    try:
        parts = urlsplit(cleaned)
    except ValueError:
        return cleaned
    if not parts.netloc:
        return cleaned
    scheme = "https" if parts.scheme in ("http", "https", "") else parts.scheme
    host = parts.netloc.lower().removeprefix("www.")
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
            if not _TRACKING_PARAMS.match(k)]
    kept.sort()
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, host, path, urlencode(kept), ""))


def fingerprint(company: str, title: str, location: str, *, remote: bool = False) -> str:
    """Layer-1 dedup key."""
    key = "\x1f".join((
        norm_company(company),
        norm_title(title),
        location_bucket(location, remote=remote),
    ))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def text_hash(text: str) -> str:
    """Stable hash of description prose, ignoring whitespace and case."""
    return hashlib.sha256(_collapse((text or "").lower()).encode("utf-8")).hexdigest()
