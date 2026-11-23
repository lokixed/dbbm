from pathlib import Path

import pytest

from app.services.paths import PathNotAllowed, check_lexical, resolve_inside, to_relative


@pytest.fixture
def base(tmp_path: Path) -> Path:
    directory = tmp_path / "backups"
    directory.mkdir()
    return directory


@pytest.mark.parametrize("raw", ["../x", "a/../../etc", "../../etc", "/backups/../etc", ".."])
def test_dotdot_rejected(raw: str, base: Path) -> None:
    with pytest.raises(PathNotAllowed):
        to_relative(raw, base)


def test_absolute_outside_rejected(base: Path) -> None:
    with pytest.raises(PathNotAllowed):
        to_relative("/etc", base)
    with pytest.raises(PathNotAllowed):
        to_relative(str(base) + "-sibling/x", base)


def test_absolute_inside_becomes_relative(base: Path) -> None:
    assert to_relative(str(base / "daily"), base) == "daily"
    assert to_relative(str(base), base) == "."
    assert to_relative("daily/db", base) == "daily/db"
    assert to_relative("./daily", base) == "daily"


def test_lexical_rejects_empty_and_control_chars() -> None:
    for raw in ("", "   ", "a\x00b", "a\\b", "x" * 300):
        with pytest.raises(PathNotAllowed):
            check_lexical(raw)


def test_resolves_inside(base: Path) -> None:
    assert resolve_inside(base, "new/dir") == base.resolve() / "new" / "dir"
    assert resolve_inside(base, ".") == base.resolve()


def test_symlink_escape_rejected(base: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (base / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PathNotAllowed):
        resolve_inside(base, "escape")
    with pytest.raises(PathNotAllowed):
        resolve_inside(base, "escape/sub/dir")


def test_symlink_inside_is_allowed(base: Path) -> None:
    (base / "real").mkdir()
    (base / "alias").symlink_to(base / "real", target_is_directory=True)
    assert resolve_inside(base, "alias") == (base / "real").resolve()


def test_symlink_loop_is_rejected_not_crashed(base: Path) -> None:
    (base / "loop").symlink_to(base / "loop")
    with pytest.raises(PathNotAllowed):
        resolve_inside(base, "loop/x")
