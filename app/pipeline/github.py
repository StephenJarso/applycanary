"""GitHub evidence collection.

Purpose is narrow and important: give the CV tailor a body of *verifiable* work to
draw on. Without this, asking a model to "add missing keywords" is an invitation
to fabricate. With it, a skill can be added only when a real repository shows it.

Only public repos are read, forks are excluded by default (a fork is not evidence
the user wrote anything), and archived repos are kept but marked.

Works unauthenticated at 60 requests/hour, which is enough for a periodic sync.
A token raises that to 5000/hour.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field

import httpx

from app.config import get_settings
from app.pipeline.keywords import extract_skills
from app.sources.base import DEFAULT_TIMEOUT, USER_AGENT

log = logging.getLogger(__name__)

API = "https://api.github.com"
MAX_REPOS = 30
README_CONCURRENCY = 4
README_CHARS = 1200


@dataclass(slots=True)
class RepoEvidence:
    name: str
    full_name: str = ""
    description: str = ""
    language: str = ""
    languages: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    stars: int = 0
    readme_excerpt: str = ""
    url: str = ""
    is_private: bool = False
    is_archived: bool = False
    pushed_at: str = ""

    def as_prompt_block(self) -> str:
        privacy = " (private project)" if self.is_private else ""
        lines = [f"Repository: {self.name}{privacy}"]
        if self.languages:
            lines.append(f"  Languages: {', '.join(self.languages)}")
        elif self.language:
            lines.append(f"  Language: {self.language}")
        if self.topics:
            lines.append(f"  Topics: {', '.join(self.topics)}")
        if self.description:
            lines.append(f"  Description: {self.description}")
        if self.stars > 2:
            lines.append(f"  Stars: {self.stars}")
        if self.is_archived:
            lines.append("  (archived)")
        if self.readme_excerpt:
            lines.append(f"  README: {self.readme_excerpt}")
        return "\n".join(lines)


@dataclass(slots=True)
class GithubEvidence:
    username: str = ""
    repos: list[RepoEvidence] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.repos)

    def as_prompt_text(self, limit: int = 15) -> str:
        """Flatten to the text block handed to the tailoring prompt."""
        if not self.repos:
            return ""
        blocks = [r.as_prompt_block() for r in self.repos[:limit]]
        private_count = sum(1 for r in self.repos if r.is_private)
        meta_note = f" (including {private_count} private repositories)" if private_count else ""
        header = (
            f"GitHub repositories and verified projects for {self.username}{meta_note} "
            f"(real verifiable evidence of code, architecture, and projects to ground tailoring):\n\n"
        )
        return header + "\n\n".join(blocks)

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "skills": self.skills,
            "repos": [asdict(r) for r in self.repos],
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> GithubEvidence:
        if not isinstance(data, dict):
            return cls()
        repos = [
            RepoEvidence(**{k: v for k, v in r.items() if k in RepoEvidence.__slots__})
            for r in data.get("repos", [])
            if isinstance(r, dict)
        ]
        return cls(
            username=str(data.get("username") or ""),
            repos=repos,
            skills=[str(s) for s in data.get("skills", [])],
            error=str(data.get("error") or ""),
        )


async def scan(
    username: str = "", *, token: str = "", include_forks: bool = False
) -> GithubEvidence:
    settings = get_settings()
    username = username or settings.github_username
    if not username:
        return GithubEvidence(error="no GitHub username configured")

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    auth_token = token or settings.github_token
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    async with httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT, headers=headers, follow_redirects=True
    ) as client:
        try:
            repos = await _list_repos(client, username, include_forks, has_token=bool(auth_token))
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code == 404:
                return GithubEvidence(username=username,
                                      error=f"GitHub user {username!r} not found")
            if code == 401:
                return GithubEvidence(
                    username=username,
                    error="GitHub rejected the access token; reconnect GitHub",
                )
            if code in (403, 429):
                return GithubEvidence(
                    username=username,
                    error="GitHub rate limit reached; connect a read-only token to raise it",
                )
            return GithubEvidence(username=username, error=f"GitHub HTTP {code}")
        except Exception as exc:  # noqa: BLE001
            return GithubEvidence(username=username,
                                  error=f"{type(exc).__name__}: {exc}")

        await _enrich(client, username, repos)

    skills: set[str] = set()
    for repo in repos:
        skills |= extract_skills(
            f"{repo.name} {repo.description} {' '.join(repo.languages)} "
            f"{' '.join(repo.topics)} {repo.readme_excerpt}"
        )

    log.info("github: %d repos, %d distinct skills for %s",
             len(repos), len(skills), username)
    return GithubEvidence(username=username, repos=repos, skills=sorted(skills))


async def _list_repos(
    client: httpx.AsyncClient,
    username: str,
    include_forks: bool,
    *,
    has_token: bool = False,
) -> list[RepoEvidence]:
    payload: list | None = None

    # When an authenticated token is provided, query /user/repos to list
    # BOTH public and private repositories of the user with full visibility.
    if has_token:
        try:
            resp = await client.get(
                f"{API}/user/repos",
                params={"visibility": "all", "affiliation": "owner", "sort": "pushed", "per_page": 100},
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and data:
                    payload = data
        except Exception as exc:  # noqa: BLE001
            log.debug("github: /user/repos failed (%s), falling back to /users/{username}/repos", exc)

    if payload is None:
        resp = await client.get(
            f"{API}/users/{username}/repos",
            params={"sort": "pushed", "per_page": 100, "type": "owner"},
        )
        resp.raise_for_status()
        payload = resp.json()

    if not isinstance(payload, list):
        raise ValueError("unexpected /repos payload")

    repos: list[RepoEvidence] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        # A fork is not evidence the user wrote the code.
        if item.get("fork") and not include_forks:
            continue
        repos.append(RepoEvidence(
            name=str(item.get("name") or ""),
            full_name=str(item.get("full_name") or f"{username}/{item.get('name')}"),
            description=str(item.get("description") or "")[:300],
            language=str(item.get("language") or ""),
            topics=[str(t) for t in (item.get("topics") or []) if t][:10],
            stars=int(item.get("stargazers_count") or 0),
            url=str(item.get("html_url") or ""),
            is_private=bool(item.get("private")),
            is_archived=bool(item.get("archived")),
            pushed_at=str(item.get("pushed_at") or ""),
        ))

    # Most-recently-pushed first, then stars: recent work is the better evidence.
    repos.sort(key=lambda r: (r.pushed_at, r.stars), reverse=True)
    return repos[:MAX_REPOS]


async def _enrich(
    client: httpx.AsyncClient, username: str, repos: list[RepoEvidence]
) -> None:
    """Add language breakdown and README excerpt, bounded and failure-tolerant."""
    sem = asyncio.Semaphore(README_CONCURRENCY)

    async def one(repo: RepoEvidence) -> None:
        async with sem:
            repo_path = repo.full_name or f"{username}/{repo.name}"
            try:
                langs = await client.get(f"{API}/repos/{repo_path}/languages")
                if langs.status_code == 200 and isinstance(data := langs.json(), dict):
                    # Ordered by bytes of code, so the primary stack comes first.
                    repo.languages = [
                        k for k, _ in sorted(data.items(), key=lambda kv: kv[1], reverse=True)
                    ][:6]
            except Exception as exc:  # noqa: BLE001
                log.debug("github: languages failed for %s: %s", repo.name, exc)

            try:
                readme = await client.get(
                    f"{API}/repos/{repo_path}/readme",
                    headers={"Accept": "application/vnd.github.raw+json"},
                )
                if readme.status_code == 200:
                    repo.readme_excerpt = _clean_readme(readme.text)
            except Exception as exc:  # noqa: BLE001
                log.debug("github: readme failed for %s: %s", repo.name, exc)

    await asyncio.gather(*(one(r) for r in repos))


def _clean_readme(text: str) -> str:
    """Strip badges and markdown noise, keep the prose that describes the project."""
    keep: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!", "|", "```", "---", "===")):
            continue
        if stripped.startswith("[!") or "shields.io" in stripped or "badge" in stripped.lower():
            continue
        keep.append(stripped)
        if sum(len(k) for k in keep) > README_CHARS:
            break
    return " ".join(keep)[:README_CHARS]
