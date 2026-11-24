"""scripts/release.py: next version from Conventional Commits, file updates, changelog rotation."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_script"] = module  # dataclasses look the module up by name
    spec.loader.exec_module(module)
    return module


if not SCRIPT.exists():  # the in-image integration run only copies backend/
    pytest.skip("scripts/release.py is not available here", allow_module_level=True)

release = load_script()
Commit = release.Commit


def commits(*subjects: str, body: str = "") -> list[object]:
    return [Commit(s, body) for s in subjects]


# ---------- latest tag ----------


def test_latest_stable_orders_numerically_and_ignores_prereleases() -> None:
    tags = ["v13.9.0", "v13.10.0", "v2.0.0", "v14.0.0-rc.1", "v1.2", "release-5", "v13.10.0-beta"]
    assert release.latest_stable(tags) == "13.10.0"


def test_latest_stable_without_a_stable_tag_is_none() -> None:
    assert release.latest_stable(["v1.0.0-rc.1", "foo"]) is None
    assert release.latest_stable([]) is None


# ---------- bump rules ----------


@pytest.mark.parametrize(
    ("subjects", "body", "expected"),
    [
        (["fix: a"], "", "patch"),
        (["chore: a", "docs: b"], "", "patch"),
        (["Update the readme"], "", "patch"),  # not conventional: still a patch
        (["fix: a", "feat: b"], "", "minor"),
        (["feat(ui): b"], "", "minor"),
        (["feat!: b"], "", "major"),
        (["refactor(api)!: b", "feat: c"], "", "major"),
        (["fix: a"], "details\n\nBREAKING CHANGE: the API changed", "major"),
        (["fix: a"], "BREAKING-CHANGE: the API changed", "major"),
        (["chore(release): v1.2.3", "fix: a"], "", "patch"),
        (["chore(release): v1.2.3!: x"], "", "patch"),  # release commits never decide a bump
    ],
)
def test_bump_kind(subjects: list[str], body: str, expected: str) -> None:
    assert release.bump_kind(commits(*subjects, body=body)) == expected


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("major", "2.0.0"), ("minor", "1.5.0"), ("patch", "1.4.4")],
)
def test_bump_arithmetic(kind: str, expected: str) -> None:
    assert release.bump("1.4.3", kind) == expected


# ---------- next version, and the refusals ----------


def test_next_version_from_commits() -> None:
    assert release.next_version("1.4.3", commits("feat: x"), ["v1.4.3"]) == "1.5.0"
    assert release.next_version("1.4.3", commits("fix: x"), ["v1.4.3"]) == "1.4.4"
    assert release.next_version("1.4.3", commits("feat!: x"), ["v1.4.3"]) == "2.0.0"


def test_a_forced_bump_overrides_the_commit_log() -> None:
    assert release.next_version("1.4.3", commits("fix: x"), forced="major") == "2.0.0"
    assert release.next_version("1.4.3", commits("feat: x"), forced="patch") == "1.4.4"


def test_an_explicit_version_wins() -> None:
    assert release.next_version("1.4.3", commits("fix: x"), explicit="3.0.0") == "3.0.0"
    assert release.next_version(None, [], explicit="1.0.0") == "1.0.0"


@pytest.mark.parametrize("bad", ["v1.2.3", "1.2", "1.2.3-rc.1", "1.2.3.4", "one.two.three"])
def test_a_malformed_explicit_version_is_refused(bad: str) -> None:
    with pytest.raises(release.ReleaseError, match="not a plain X.Y.Z"):
        release.next_version("1.0.0", commits("fix: x"), explicit=bad)


def test_an_existing_tag_is_refused() -> None:
    with pytest.raises(release.ReleaseError, match="already exists"):
        release.next_version("1.0.0", [], ["v1.0.0", "v2.0.0"], explicit="2.0.0")


def test_a_version_that_is_not_greater_is_refused() -> None:
    with pytest.raises(release.ReleaseError, match="not greater"):
        release.next_version("2.10.0", [], ["v2.10.0"], explicit="2.1.0")  # 2.1.0 < 2.10.0
    with pytest.raises(release.ReleaseError, match="not greater"):
        release.next_version("2.10.0", [], [], explicit="2.10.0")  # equal is not greater either


def test_no_stable_tag_means_nothing_to_release_unless_a_version_is_given() -> None:
    with pytest.raises(release.NothingToRelease, match="No stable"):
        release.next_version(None, commits("feat: x"), ["v1.0.0-rc.1"])


def test_no_new_commits_means_nothing_to_release() -> None:
    with pytest.raises(release.NothingToRelease, match="No commits"):
        release.next_version("1.0.0", [], ["v1.0.0"])
    with pytest.raises(release.NothingToRelease, match="No commits"):
        release.next_version("1.0.0", commits("chore(release): v1.0.0"), ["v1.0.0"])


# ---------- changelog ----------

CHANGELOG = """# Changelog

Intro.

## [Unreleased]
{unreleased}
## [1.0.0] - 2026-01-01

### Added
- First release.
"""


def test_rotate_keeps_hand_written_notes() -> None:
    text = CHANGELOG.format(unreleased="\n### Added\n- Hand written.\n")
    rotated = release.rotate_changelog(text, "1.1.0", "2026-02-03", commits("feat: ignored"))
    assert "## [Unreleased]\n\n## [1.1.0] - 2026-02-03\n\n### Added\n- Hand written.\n" in rotated
    assert "ignored" not in rotated and rotated.index("[1.1.0]") < rotated.index("[1.0.0]")


def test_rotate_writes_notes_from_commits_when_unreleased_is_empty() -> None:
    text = CHANGELOG.format(unreleased="\n")
    log = commits(
        "feat(ui): add a badge",
        "fix: stop the crash",
        "docs: explain it",
        "chore(release): v1.0.0",
        "Merge branch 'x'",
    )
    rotated = release.rotate_changelog(text, "1.1.0", "2026-02-03", log)
    section = release.version_section(rotated, "1.1.0")
    assert section == (
        "### Added\n- add a badge\n\n### Fixed\n- stop the crash\n\n### Changed\n- explain it"
    )


def test_rotate_with_nothing_to_say_still_makes_a_section() -> None:
    rotated = release.rotate_changelog(CHANGELOG.format(unreleased="\n"), "1.0.1", "2026-02-03", [])
    assert release.version_section(rotated, "1.0.1") == "- Maintenance release."


def test_rotate_needs_an_unreleased_section() -> None:
    with pytest.raises(release.ReleaseError, match="Unreleased"):
        release.rotate_changelog("# Changelog\n\n## [1.0.0]\n", "1.0.1", "2026-02-03", [])


def test_version_section_is_a_literal_match() -> None:
    text = "## [1.0.0] - x\n- a\n\n## [1.0.0-rc.1] - y\n- b\n"
    assert release.version_section(text, "1.0.0") == "- a"
    with pytest.raises(release.ReleaseError, match="no section"):
        release.version_section(text, "1.0")


# ---------- files that carry the version ----------


def make_tree(root: Path, newline: str) -> None:
    files = {
        "backend/app/version.py": '"""doc"""\n\n__version__ = "1.0.0-rc.1"\n',
        "backend/pyproject.toml": '[project]\nname = "dbbm"\nversion = "1.0.0rc1"\n',
        "frontend/package.json": (
            '{\n  "name": "x",\n  "version": "1.0.0-rc.1",\n  "private": true\n}\n'
        ),
        "CHANGELOG.md": CHANGELOG.format(unreleased="\n- Something new.\n"),
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.replace("\n", newline).encode())


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_apply_updates_every_file_and_keeps_line_endings(tmp_path: Path, newline: str) -> None:
    make_tree(tmp_path, newline)
    release.apply_release(tmp_path, "1.2.0", [], "2026-02-03")
    assert set(release.read_versions(tmp_path).values()) == {"1.2.0"}
    for name in ("backend/app/version.py", "frontend/package.json", "CHANGELOG.md"):
        raw = (tmp_path / name).read_bytes()
        assert (b"\r\n" in raw) == (newline == "\r\n")
        assert raw.count(b"\n") == raw.count(newline.encode())  # no mixed line endings
    changelog = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert release.version_section(changelog, "1.2.0") == "- Something new."


def test_apply_fails_when_a_version_line_is_missing(tmp_path: Path) -> None:
    make_tree(tmp_path, "\n")
    (tmp_path / "backend/app/version.py").write_text("__version__: str = '1.0.0'\n")
    with pytest.raises(release.ReleaseError, match="version.py"):
        release.apply_release(tmp_path, "1.2.0", [], "2026-02-03")


def test_apply_refuses_a_malformed_version(tmp_path: Path) -> None:
    make_tree(tmp_path, "\n")
    with pytest.raises(release.ReleaseError, match="not a plain"):
        release.apply_release(tmp_path, "v1.2.0", [], "2026-02-03")


# ---------- git ----------


def run_git(repo: Path, *args: str) -> None:
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "PATH": os.environ["PATH"],
        "HOME": str(repo),
    }
    subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)  # noqa: S603, S607


def test_commits_since_the_latest_stable_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_git(tmp_path, "init", "-q", "-b", "main")
    for index, subject in enumerate(["feat: one", "fix: two"]):
        (tmp_path / "f").write_text(str(index))
        run_git(tmp_path, "add", "f")
        run_git(tmp_path, "commit", "-q", "-m", subject)
    run_git(tmp_path, "tag", "v1.0.0-rc.1")
    (tmp_path / "f").write_text("stable")
    run_git(tmp_path, "commit", "-q", "-am", "chore(release): v1.0.0", "-m", "body")
    run_git(tmp_path, "tag", "v1.0.0")
    (tmp_path / "f").write_text("next")
    run_git(tmp_path, "commit", "-q", "-am", "feat: three", "-m", "BREAKING CHANGE: gone")

    monkeypatch.setattr(release, "ROOT", tmp_path)
    tags = release.list_tags()
    assert sorted(tags) == ["v1.0.0", "v1.0.0-rc.1"]
    assert release.range_base(tags) == "v1.0.0"
    found = release.commits_since("v1.0.0")
    assert [c.subject for c in found] == ["feat: three"]
    assert "BREAKING CHANGE" in found[0].body
    assert release.next_version(release.latest_stable(tags), found, tags) == "2.0.0"
    assert len(release.commits_since(None)) == 4  # no base: the whole history


def test_the_range_starts_at_a_prerelease_tag_when_there_is_no_stable_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "f").write_text("a")
    run_git(tmp_path, "add", "f")
    run_git(tmp_path, "commit", "-q", "-m", "feat: one")
    run_git(tmp_path, "tag", "v1.0.0-rc.1")
    (tmp_path / "f").write_text("b")
    run_git(tmp_path, "commit", "-q", "-am", "fix: two")
    monkeypatch.setattr(release, "ROOT", tmp_path)
    assert release.range_base(release.list_tags()) == "v1.0.0-rc.1"
    assert [c.subject for c in release.commits_since("v1.0.0-rc.1")] == ["fix: two"]
