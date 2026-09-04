"""Settings for ConsentMove.

All configuration flows through pydantic-settings so the same code runs
locally, in tests, and in the container. The OAuth client secret never
defaults to a real value — the deploy pipeline MUST inject it; a missing
secret means the app refuses to start an OAuth flow, not that it silently
falls back to an application-wide grant (which is the whole thing this
project exists to prevent).
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Persistence
    database_url: str = "consentmove.db"

    # HTTP
    cors_origins: str = "*"
    log_level: str = "INFO"

    # OAuth — delegated only. There is intentionally no `app_*` setting:
    # this project must never be configured to request an application-wide
    # grant. If a future feature needs one, it has to be added here in code
    # review, not smuggled in via env.
    oauth_client_id: str = ""
    oauth_client_secret: str = ""
    oauth_redirect_uri: str = "http://localhost:8000/oauth/callback"
    oauth_scopes: str = (
        "openid profile email offline_access "
        "Files.Read Files.ReadWrite"
    )

    # Job behaviour
    job_poll_interval_seconds: float = 1.0
    audit_export_path: str = "audit.json"


@lru_cache
def settings() -> Settings:
    return Settings()
