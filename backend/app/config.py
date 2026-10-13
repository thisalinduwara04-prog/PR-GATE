from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Severity = Literal["high", "medium", "low"]


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables (docker compose env_file)."""

    model_config = SettingsConfigDict(extra="ignore")

    github_token: str = ""
    github_webhook_secret: str = ""
    github_api_url: str = "https://api.github.com"
    github_clone_base: str = "https://github.com"
    watched_repos: str = ""
    claude_code_oauth_token: str = ""
    claude_bin: str = "claude"

    verify_timeout_s: int = 600
    scan_timeout_s: int = 300
    publish_min_severity: Severity = "medium"
    max_body_bytes: int = 5 * 1024 * 1024

    db_path: str = "/data/pr-gate.db"
    workspaces_dir: str = "/workspaces"
    artifacts_dir: str = "/data/artifacts"
    dashboard_url: str = "http://localhost:5173"
    commit_status_enabled: bool = True
    catchup_on_startup: bool = True
    max_concurrent_runs: int = 2
    # B5: when set, scanning and the Claude agent run in their own containers (docker compose sets these).
    scanner_url: str = ""
    agent_url: str = ""

    @property
    def watched_repo_set(self) -> set[str]:
        return {r.strip().lower() for r in self.watched_repos.split(",") if r.strip()}
