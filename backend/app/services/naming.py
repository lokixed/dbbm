import re
from datetime import UTC, datetime

_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
MAX_COMPONENT = 100
MANIFEST_SUFFIX = ".manifest.json"


def safe_component(name: str) -> str:
    """A file-name-safe version of a job or database name (never empty, never starts with a dot)."""
    cleaned = _UNSAFE_RE.sub("_", name).lstrip(".")[:MAX_COMPONENT]
    return cleaned or "unnamed"


def database_label(database_id: str) -> str:
    """Artifact label for a Firestore database; "(default)" becomes "default"."""
    return safe_component(database_id.strip("()"))


def format_timestamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def artifact_key(
    job_name: str, database: str, moment: datetime, extension: str, *, suffix: int = 1
) -> str:
    """<job>/<YYYY>/<MM>/<job>_<database>_<timestamp>.<ext>; `suffix` > 1 breaks name clashes."""
    job = safe_component(job_name)
    db = safe_component(database) + (f"-{suffix}" if suffix > 1 else "")
    utc = moment.astimezone(UTC)
    return f"{job}/{utc:%Y}/{utc:%m}/{job}_{db}_{format_timestamp(moment)}.{extension}"


def manifest_key(key: str) -> str:
    return key + MANIFEST_SUFFIX
