import re
from pathlib import Path, PurePosixPath

MAX_PATH_LENGTH = 255
_FORBIDDEN_RE = re.compile(r"[\x00-\x1f\x7f\\]")


class PathNotAllowed(ValueError):  # noqa: N818  (reads better as a domain error)
    """The path is not allowed. The message is safe to show to users."""


def check_lexical(raw: str) -> PurePosixPath:
    """Reject anything unsafe that can be seen without touching the filesystem."""
    if not raw.strip() or len(raw) > MAX_PATH_LENGTH:
        raise PathNotAllowed(f"Path must be 1-{MAX_PATH_LENGTH} characters.")
    if _FORBIDDEN_RE.search(raw):
        raise PathNotAllowed("Path contains forbidden characters.")
    path = PurePosixPath(raw)
    if ".." in path.parts:
        raise PathNotAllowed("'..' is not allowed in paths.")
    return path


def to_relative(raw: str, base: Path) -> str:
    """Normalise a user path to one relative to `base`; absolute paths must already be inside it."""
    path = check_lexical(raw)
    if path.is_absolute():
        for root in (PurePosixPath(base), PurePosixPath(base.resolve())):
            if path.is_relative_to(root):
                path = path.relative_to(root)
                break
        else:
            raise PathNotAllowed("Absolute paths must be inside the backup directory.")
    return "/".join(path.parts) or "."


def resolve_inside(base: Path, relative: str) -> Path:
    """Resolve symlinks and verify the result is still inside `base`."""
    try:
        real_base = base.resolve()
        target = (real_base / relative).resolve()
    except (OSError, RuntimeError):
        raise PathNotAllowed("Path cannot be resolved.") from None
    if not target.is_relative_to(real_base):
        raise PathNotAllowed("Path resolves outside the backup directory.")
    return target
