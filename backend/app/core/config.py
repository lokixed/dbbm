from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, PrivateAttr, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_KEY_LENGTH = 32
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    data_dir: Path = Path("/data")
    backup_dir: Path = Path("/backups")
    app_secret_key: SecretStr | None = None
    app_secret_key_file: Path | None = None
    session_ttl_hours: int = Field(12, ge=1)
    log_level: str = "INFO"
    enable_docs: bool = False
    cookie_secure: bool = True
    login_max_attempts: int = Field(5, ge=1)
    login_lockout_seconds: int = Field(900, ge=1)
    max_concurrent_runs: int = Field(2, ge=1, le=32)
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"
    # Initial value of the public_base_url setting (the setting wins once saved).
    public_base_url: str = ""
    allow_private_notification_targets: bool = False

    _master_key: bytes = PrivateAttr(default=b"")

    @model_validator(mode="after")
    def _load_master_key(self) -> Self:
        inline = self.app_secret_key.get_secret_value() if self.app_secret_key else ""
        if inline and self.app_secret_key_file:
            raise ValueError("Set only one of APP_SECRET_KEY and APP_SECRET_KEY_FILE, not both.")
        if self.app_secret_key_file:
            try:
                key = self.app_secret_key_file.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ValueError(
                    f"Cannot read APP_SECRET_KEY_FILE ({self.app_secret_key_file}): {exc.strerror}"
                ) from exc
        else:
            key = inline.strip()
        if not key:
            raise ValueError(
                "No master key configured. Set APP_SECRET_KEY or APP_SECRET_KEY_FILE "
                f"(at least {MIN_KEY_LENGTH} characters, e.g. `openssl rand -base64 32`)."
            )
        if len(key) < MIN_KEY_LENGTH:
            raise ValueError(f"The master key must be at least {MIN_KEY_LENGTH} characters long.")
        self._master_key = key.encode("utf-8")
        return self

    @model_validator(mode="after")
    def _normalise_log_level(self) -> Self:
        self.log_level = self.log_level.upper()
        return self

    @property
    def master_key(self) -> bytes:
        return self._master_key

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def work_dir(self) -> Path:
        # Not /tmp: the container mounts /tmp as RAM-backed tmpfs.
        return self.data_dir / "work"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def scheduler_lock_path(self) -> Path:
        return self.data_dir / "scheduler.lock"

    @property
    def session_ttl_seconds(self) -> int:
        return self.session_ttl_hours * 3600


@lru_cache
def get_settings() -> Settings:
    return Settings()
