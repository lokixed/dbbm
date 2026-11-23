"""rclone plumbing: a private config per invocation, process runs, tokens and error classes."""

import configparser
import os
import re
import shutil
import tempfile
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.services.proc import StderrTail, StepTimeoutError, clean_env, run_process
from app.services.redact import sanitize, scrub

REMOTE = "dbb"
RCLONE_BIN = "rclone"
MAX_STDOUT_BYTES = 64 * 1024 * 1024
COMMON_FLAGS: tuple[str, ...] = (
    "--retries", "3",
    "--low-level-retries", "10",
    "--transfers", "1",
    "--stats", "10s",
    "--stats-one-line",
    "--stats-log-level", "NOTICE",
    "--ask-password=false",
)  # fmt: skip

_LOG_PREFIX_RE = re.compile(r"^\d{4}/\d\d/\d\d \d\d:\d\d:\d\d\s+")
_LEVEL_RE = re.compile(r"^(?:ERROR|CRITICAL|NOTICE|INFO|DEBUG)\s*:\s*")


class ErrorClass(StrEnum):
    AUTH_EXPIRED = "auth_expired"
    QUOTA_EXCEEDED = "quota_exceeded"
    RATE_LIMITED = "rate_limited"
    FOLDER_NOT_FOUND = "folder_not_found"
    NETWORK = "network"
    OTHER = "other"


# Checked in order: an upload-quota 403 also says "rate limit", so quota goes first.
_PATTERNS: tuple[tuple[ErrorClass, re.Pattern[str]], ...] = (
    (
        ErrorClass.AUTH_EXPIRED,
        re.compile(
            r"invalid_grant|token (?:has )?expired|expired or (?:been )?revoked|invalid_client"
            r"|unauthorized_client|couldn't fetch token|cannot fetch token|empty token found"
            r"|\b401\b|Invalid Credentials|authError",
            re.IGNORECASE,
        ),
    ),
    (
        ErrorClass.QUOTA_EXCEEDED,
        re.compile(
            r"storageQuotaExceeded|quotaExceeded|teamDriveFileLimitExceeded|uploadLimitExceeded"
            r"|upload limit error|storage quota has been exceeded"
            r"|userRateLimitExceeded.*upload|upload.*userRateLimitExceeded",
            re.IGNORECASE,
        ),
    ),
    (
        ErrorClass.RATE_LIMITED,
        re.compile(
            r"rateLimitExceeded|userRateLimitExceeded|rate limit|\b429\b|Too Many Requests",
            re.IGNORECASE,
        ),
    ),
    (
        ErrorClass.FOLDER_NOT_FOUND,
        re.compile(
            r"directory not found|dir not found|notFound|Shared drive not found"
            r"|couldn't find root directory ID",
            re.IGNORECASE,
        ),
    ),
    (
        ErrorClass.NETWORK,
        re.compile(
            r"dial tcp|no such host|connection refused|connection reset|i/o timeout"
            r"|TLS handshake|network is unreachable|context deadline exceeded|unexpected EOF"
            r"|temporary failure in name resolution|server misbehaving",
            re.IGNORECASE,
        ),
    ),
)

_FRIENDLY = {
    ErrorClass.AUTH_EXPIRED: "Google Drive authorization expired; reconnect in Destinations.",
    ErrorClass.QUOTA_EXCEEDED: (
        "Google Drive quota exceeded (storage is full or the daily upload limit was reached)."
    ),
    ErrorClass.RATE_LIMITED: "Google Drive rate limit reached; try again later.",
    ErrorClass.FOLDER_NOT_FOUND: "The Drive folder or shared drive was not found.",
    ErrorClass.NETWORK: "Network error while talking to Google Drive.",
    ErrorClass.OTHER: "rclone failed.",
}
NEEDS_REAUTH_MESSAGE = _FRIENDLY[ErrorClass.AUTH_EXPIRED]


class RcloneError(Exception):
    """An rclone command failed. The message is sanitized and safe to store and show."""

    def __init__(self, error_class: ErrorClass, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.code = code


@dataclass(frozen=True)
class RcloneResult:
    code: int
    stdout: str
    stderr: str


# ---------- config files ----------


def drive_sections(
    *,
    scope: str,
    token: str,
    client_id: str | None = None,
    client_secret: str | None = None,
    shared_drive_id: str | None = None,
) -> dict[str, str]:
    sections = {"type": "drive", "scope": scope}
    if client_id and client_secret:
        sections["client_id"] = client_id
        sections["client_secret"] = client_secret
    if shared_drive_id:
        sections["team_drive"] = shared_drive_id
    sections["token"] = token
    return sections


def render_config(sections: Mapping[str, str]) -> str:
    lines = [f"[{REMOTE}]"]
    for key, value in sections.items():
        if any(c in key + value for c in "\r\n"):
            raise ValueError("rclone config values must be single-line.")
        lines.append(f"{key} = {value}")
    return "\n".join(lines) + "\n"


def read_token(config_path: Path) -> str | None:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(config_path.read_text(encoding="utf-8"))
    except (OSError, configparser.Error, UnicodeDecodeError):
        return None
    if not parser.has_option(REMOTE, "token"):
        return None
    return parser.get(REMOTE, "token").strip() or None


def _write_private(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)


# ---------- errors ----------


def _strip_prefixes(line: str) -> str:
    line = _LOG_PREFIX_RE.sub("", line.strip())
    return _LEVEL_RE.sub("", line)


def _detail(stderr: str) -> str:
    lines = [ln for ln in stderr.splitlines() if ln.strip()]
    errors = [ln for ln in lines if re.search(r"\b(?:ERROR|CRITICAL|Failed)\b", ln)]
    picked = errors[-1] if errors else (lines[-1] if lines else "")
    return _strip_prefixes(picked)


def classify_error(stderr: str, code: int | None, secrets: Sequence[str] = ()) -> RcloneError:
    error_class = ErrorClass.OTHER
    for candidate, pattern in _PATTERNS:
        if pattern.search(stderr):
            error_class = candidate
            break
    if error_class is ErrorClass.OTHER and code == 3:
        error_class = ErrorClass.FOLDER_NOT_FOUND
    detail = sanitize(_detail(stderr), secrets)
    friendly = _FRIENDLY[error_class]
    if error_class is ErrorClass.OTHER:
        friendly = f"rclone failed (exit code {code})."
    message = f"{friendly} {detail}".strip() if detail else friendly
    return RcloneError(error_class, sanitize(message, secrets), code)


# ---------- running ----------


class RcloneSession:
    """One private config file; run any number of rclone commands against it."""

    def __init__(self, config_path: Path, env: dict[str, str], secrets: Sequence[str]) -> None:
        self.config_path = config_path
        self.env = env
        self._secrets = list(secrets)

    def read_token(self) -> str | None:
        return read_token(self.config_path)

    async def run(
        self,
        args: Sequence[str],
        *,
        timeout: int,
        on_line: Callable[[str], None] | None = None,
        check: bool = True,
    ) -> RcloneResult:
        out = bytearray()

        async def sink(chunk: bytes) -> None:
            out.extend(chunk)
            if len(out) > MAX_STDOUT_BYTES:
                raise RcloneError(ErrorClass.OTHER, "rclone produced too much output.")

        def line(text: str) -> None:
            if on_line is not None and text.strip():
                on_line(scrub(_strip_prefixes(text), self._secrets))

        tail = StderrTail()
        try:
            code = await run_process(
                [RCLONE_BIN, *args, *COMMON_FLAGS],
                env=self.env,
                timeout=timeout,
                tail=tail,
                stdout_sink=sink,
                stderr_line=line,
            )
        except StepTimeoutError as exc:
            raise RcloneError(
                ErrorClass.NETWORK, f"rclone timed out after {exc.seconds} seconds."
            ) from None
        except FileNotFoundError:
            raise RcloneError(ErrorClass.OTHER, "rclone is not installed.") from None
        stderr = tail.text()
        if check and code != 0:
            raise classify_error(stderr, code, self._secrets)
        return RcloneResult(code, out.decode("utf-8", errors="replace"), stderr)


@asynccontextmanager
async def rclone_session(
    sections: Mapping[str, str], *, cache_dir: Path | None, secrets: Sequence[str]
) -> AsyncIterator[RcloneSession]:
    """A 0700 temp dir (under the tmpfs /tmp) with a 0600 config; always removed afterwards."""
    home = Path(tempfile.mkdtemp(prefix="dbbackup-rclone-"))
    try:
        config_path = home / "rclone.conf"
        _write_private(config_path, render_config(sections))
        cache = cache_dir if cache_dir is not None else home / "cache"
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        env = clean_env(
            {
                "RCLONE_CONFIG": str(config_path),
                "RCLONE_CACHE_DIR": str(cache),
                "XDG_CACHE_HOME": str(cache),
                "XDG_CONFIG_HOME": str(home),
                "TMPDIR": str(home),
            },
            home=home,
        )
        yield RcloneSession(config_path, env, secrets)
    finally:
        shutil.rmtree(home, ignore_errors=True)
