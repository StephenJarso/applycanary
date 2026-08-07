"""Central configuration, loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- llm ---
    anthropic_api_key: str = ""
    model_triage: str = "claude-haiku-4-5-20251001"
    model_tailor: str = "claude-sonnet-5"

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
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    digest_to: str = ""
    alert_min_score: int = 90

    # --- aggregators ---
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""

    # --- server ---
    host: str = "127.0.0.1"
    port: int = 8000
    database_url: str = "sqlite:///./data/applycanary.db"
    data_dir: Path = Field(default=Path("./data"))
    log_level: str = "INFO"

    # ------------------------------------------------------------------
    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.digest_to)

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

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.resume_dir, self.artifact_dir, self.cache_dir):
            d.mkdir(parents=True, exist_ok=True)

    def startup_warnings(self) -> list[str]:
        """Non-fatal misconfigurations worth surfacing at boot and on the dashboard."""
        warnings: list[str] = []
        if not self.llm_enabled:
            warnings.append(
                "ANTHROPIC_API_KEY is unset: tier-2 scoring, CV tailoring and "
                "interview prep are disabled. Local filtering still runs."
            )
        if not self.email_enabled:
            warnings.append("SMTP is not configured: digests will be logged, not sent.")
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
            warnings.append(
                f"HOST is {self.host}, not loopback. The dashboard has no "
                "authentication and exposes your resume and application history."
            )
        return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()
