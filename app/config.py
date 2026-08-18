"""Central configuration, loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Hard default for the session-signing key. Every deploy must override it with a
# random value, and startup refuses a non-loopback bind that still carries it —
# anyone who can read the public repo could forge a session token otherwise.
DEFAULT_SECRET_KEY = "applycanary-secret-key-change-in-production"


@lru_cache
def in_container() -> bool:
    """Best-effort detection of Docker/Podman/Kubernetes.

    Used only to phrase a warning accurately, never to change behaviour, so a
    wrong answer is harmless. /.dockerenv covers Docker; the cgroup scan covers
    Podman and Kubernetes, where that file is absent.
    """
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return any(tag in cgroup for tag in ("docker", "containerd", "kubepods", "libpod"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- llm ---
    # Provider chain (tried in order). First available is used.
    # 1. xAI Grok - needs XAI_API_KEY (OpenAI-compatible, generous trial credits)
    # 2. Gemini (primary) - needs GEMINI_API_KEY
    # 3. OpenRouter (free models) - needs OPENROUTER_API_KEY
    # 4. Groq (free tier) - needs GROQ_API_KEY
    # 5. Ollama (local) - needs OLLAMA_HOST (default http://localhost:11434)
    # 6. Anthropic - needs ANTHROPIC_API_KEY
    # 7. Amazon Bedrock - needs AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY
    xai_api_key: str = ""
    xai_triage_model: str = "grok-4.6"
    xai_tailor_model: str = "grok-4.6"
    anthropic_api_key: str = ""
    model_triage: str = "claude-haiku-4-5-20251001"
    model_tailor: str = "claude-sonnet-5"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_tailor_model: str = "gemini-3.5-flash-lite"
    # 0 = disable chain-of-thought on Gemini models that support thinking
    # (e.g. gemini-3.5-flash). Non-thinking models like flash-lite reject the
    # field, so the client falls back to sending it without thinkingConfig.
    gemini_thinking_budget: int = 0

    openrouter_api_key: str = ""
    openrouter_triage_model: str = "openai/gpt-4o-mini"
    openrouter_tailor_model: str = "openai/gpt-4o-mini"

    groq_api_key: str = ""
    groq_triage_model: str = "llama-3.1-8b-instant"
    groq_tailor_model: str = "llama-3.1-8b-instant"

    ollama_host: str = "http://localhost:11434"
    ollama_triage_model: str = "llama3.1:8b"
    ollama_tailor_model: str = "llama3.1:8b"

    # --- safety gates ---
    enable_auto_submit: bool = False
    daily_apply_cap: int = 20
    auto_submit_min_score: int = 80
    # Set false to run the dashboard alone, with no background polling.
    enable_scheduler: bool = True

    # --- poll cadence (minutes) ---
    # Curated company boards are polled tightly: that is where the
    # apply-early advantage comes from. Aggregators are noisier and slower
    # to update, so they are polled less often.
    poll_curated_minutes: int = 5
    poll_broad_minutes: int = 30
    score_interval_minutes: int = 2

    # --- github ---
    github_username: str = ""
    github_token: str = ""

    # --- email ---
    # Preferred: Resend (resend_api_key + email_from). Falls back to SMTP
    # when RESEND_API_KEY is empty. EMAIL_FROM defaults to Resend's sandbox
    # domain; switch to a verified domain (or set a custom FROM in Railway)
    # for production.
    resend_api_key: str = ""
    email_from: str = "ApplyCanary@resend.dev"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    digest_to: str = ""
    alert_min_score: int = 90

    # --- amazon web services (Bedrock, Polly, Transcribe, S3) ---
    # All optional. Bedrock adds a Claude + Titan-embedding provider to the LLM
    # chain; Polly/Transcribe power the voice interview; S3 stores interview
    # audio. Without them the app degrades gracefully (browser speech, local
    # storage, hashing embeddings) so the demo still runs.
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_session_token: str = ""
    aws_region: str = "us-east-1"
    bedrock_model_id: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    bedrock_embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    # Titan v2 emits 1024 dims by default; the vector columns must match.
    embedding_dims: int = 1024
    polly_voice_id: str = "Joanna"
    polly_engine: str = "neural"
    s3_bucket: str = ""
    s3_prefix: str = "applycanary"

    # --- cockroachdb operations (ccloud CLI / MCP server) ---
    # The managed MCP server (https://cockroachlabs.cloud/mcp) lets an agent
    # inspect and operate the cluster read-only by default with full audit
    # logging. The token comes from the Cloud Console; ccloud service-account
    # keys come from `ccloud iam service-account create`.
    cockroach_mcp_url: str = "https://cockroachlabs.cloud/api/v2/mcp"
    cockroach_mcp_token: str = ""
    ccloud_api_key: str = ""
    ccloud_api_secret: str = ""

    # --- aggregators ---
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    # --- auth ---
    # Legacy single-user credentials. Retained only so the migration can seed the
    # first account; authentication itself now goes through the User table.
    auth_enabled: bool = False
    auth_username: str = "admin"
    auth_password: str = ""
    secret_key: str = DEFAULT_SECRET_KEY
    # Registration requires an invite code, because the operator's API keys pay
    # for every user's scoring and tailoring.
    allow_registration: bool = True
    # Hackathon open-signup: while set, this exact code is accepted at
    # registration without consuming an InviteCode row, so every new user can
    # sign up with the same prefilled referral code. Clear it after the
    # hackathon to restore strict single-use invite gating.
    default_invite_code: str = ""

    # --- server ---
    host: str = "127.0.0.1"
    port: int = 8000
    database_url: str = "sqlite:///./data/applycanary.db"
    data_dir: Path = Field(default=Path("./data"))
    log_level: str = "INFO"
    # Frontend base URL for redirects.
    # In production: "/" (same origin, React frontend served by FastAPI).
    # In development: "http://localhost:5173" (Vite dev server).
    frontend_base_url: str = "/"
    # Marks the session cookie Secure. Defaults on for any non-loopback bind,
    # since that means the app is reachable off-box and the cookie must not
    # travel in clear text.
    cookie_secure: bool | None = None

    # ------------------------------------------------------------------
    @property
    def is_public_bind(self) -> bool:
        """True when the server listens on something other than loopback."""
        return self.host not in ("127.0.0.1", "localhost", "::1")

    @property
    def session_cookie_secure(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.is_public_bind

    @property
    def is_auth_required(self) -> bool:
        return self.auth_enabled or bool(self.auth_password)

    @property
    def llm_enabled(self) -> bool:
        """True when at least one real LLM provider is configured.

        Mirrors the provider chain in app.llm.client._build_provider_order,
        minus Ollama: a defaulted OLLAMA_HOST points at nothing until the
        operator actually runs a local server, so it must not count here.
        """
        return bool(
            self.xai_api_key
            or self.gemini_api_key
            or self.openrouter_api_key
            or self.groq_api_key
            or self.anthropic_api_key
            or self.aws_access_key_id
        )

    @property
    def aws_enabled(self) -> bool:
        return bool(self.aws_access_key_id and self.aws_secret_access_key)

    @property
    def polly_enabled(self) -> bool:
        return self.aws_enabled

    @property
    def transcribe_enabled(self) -> bool:
        return self.aws_enabled

    @property
    def s3_enabled(self) -> bool:
        return self.aws_enabled and bool(self.s3_bucket)

    @property
    def email_enabled(self) -> bool:
        """True when a send backend is configured.

        Resend is preferred; SMTP is the legacy fallback. A recipient is not
        checked here — per-user routing resolves profile.email → user.email →
        profile.digest_to → settings.digest_to at send time.
        """
        return bool(self.resend_api_key or (self.smtp_host and self.smtp_user))

    @property
    def resume_dir(self) -> Path:
        return self.data_dir / "resumes"

    @property
    def artifact_dir(self) -> Path:
        """Generated per-job resumes and cover letters."""
        return self.data_dir / "artifacts"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def db_path(self) -> str:
        """Human-readable location of the database, for startup logging."""
        return self.database_url.replace("sqlite:///", "")

    @property
    def package_dir(self) -> Path:
        return Path(__file__).resolve().parent

    @property
    def templates_dir(self) -> Path:
        return self.package_dir / "web" / "templates"

    @property
    def static_dir(self) -> Path:
        return self.package_dir / "web" / "static"

    @property
    def frontend_dist(self) -> Path:
        """Built React bundle. Absent until `npm run build` has been run."""
        return Path("./frontend/dist")

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.resume_dir, self.artifact_dir, self.cache_dir):
            d.mkdir(parents=True, exist_ok=True)

    def startup_errors(self) -> list[str]:
        """Misconfigurations that must stop the boot.

        Only the signing key qualifies today. Serving other people's data while
        signing sessions with a key published in the repo means any reader can
        mint a cookie for any account, so this fails closed instead of warning.
        """
        errors: list[str] = []
        if self.is_public_bind and self.secret_key == DEFAULT_SECRET_KEY:
            errors.append(
                "SECRET_KEY is still the built-in default while listening on "
                f"{self.host}. Session cookies would be forgeable by anyone who "
                "has read the source. Set SECRET_KEY to a random value "
                '(e.g. `python -c "import secrets; print(secrets.token_urlsafe(48))"`).'
            )
        return errors

    def startup_warnings(self) -> list[str]:
        """Non-fatal misconfigurations worth surfacing at boot and on the dashboard."""
        warnings: list[str] = []
        if not self.llm_enabled:
            warnings.append(
                "No LLM API key configured (XAI_API_KEY, GEMINI_API_KEY, "
                "OPENROUTER_API_KEY, GROQ_API_KEY or ANTHROPIC_API_KEY): "
                "tier-2 scoring, CV tailoring and interview prep are disabled. "
                "Local filtering still runs."
            )
        if not self.email_enabled:
            warnings.append(
                "No email backend configured (set RESEND_API_KEY or SMTP_HOST): "
                "digests will be logged, not sent."
            )
        if not self.github_username:
            warnings.append(
                "GITHUB_USERNAME is unset: CV tailoring has no GitHub evidence to draw on."
            )
        if self.enable_auto_submit:
            warnings.append(
                f"ENABLE_AUTO_SUBMIT is TRUE: live applications will be sent, "
                f"capped at {self.daily_apply_cap}/24h above score "
                f"{self.auto_submit_min_score}."
            )
        if self.host not in ("127.0.0.1", "localhost", "::1"):
            if in_container():
                # A container must bind 0.0.0.0 to be reachable at all, so the
                # bind address says nothing about exposure here.  When a proper
                # SECRET_KEY is in place the session is signed and the warning
                # is noise for operators (Railway/Vercel always terminate TLS).
                if self.secret_key == DEFAULT_SECRET_KEY:
                    warnings.append(
                        f"Running in a container bound to {self.host}. "
                        "The dashboard is behind login; ensure the public "
                        "ingress terminates HTTPS."
                    )
            else:
                warnings.append(
                    f"HOST is {self.host}, not loopback. The dashboard is behind "
                    "login but exposes your resume and application history; keep "
                    "it on a trusted network."
                )
        return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()
