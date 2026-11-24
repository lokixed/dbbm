#!/usr/bin/env python3
"""Release helper for the CI release job. Standard library only.

    release.py next [--version X.Y.Z] [--bump auto|major|minor|patch]
    release.py apply X.Y.Z
    release.py notes X.Y.Z

`next` works out the next version from the Conventional Commits made since the latest stable tag
and prints it. It refuses instead of guessing: a malformed or non-increasing version, a tag that
already exists, or an unclear starting point stop with an error. Exit code 10 means "nothing to
release" (no stable tag yet and no explicit version, or no new commits); any other non-zero exit is
a refusal. `apply` writes the version into the files that carry it and rotates the changelog, then
reads every file back to check. `notes` prints one version's changelog section.
"""

import argparse
import ast
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

NOTHING_TO_RELEASE = 10

STABLE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
BREAKING_SUBJECT = re.compile(r"^[a-z]+(\([^)]*\))?!:")
BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE", re.MULTILINE)
FEATURE_SUBJECT = re.compile(r"^feat(\([^)]*\))?:")
FIX_SUBJECT = re.compile(r"^fix(\([^)]*\))?:")
TYPE_PREFIX = re.compile(r"^[a-z]+(\([^)]*\))?!?:\s*")
RELEASE_SUBJECT = re.compile(r"^chore\(release\)")

ROOT = Path(__file__).resolve().parent.parent
VERSION_PY = Path("backend/app/version.py")
PYPROJECT = Path("backend/pyproject.toml")
PACKAGE_JSON = Path("frontend/package.json")
CHANGELOG = Path("CHANGELOG.md")


class ReleaseError(Exception):
    """A refusal: the release must not go ahead."""


class NothingToRelease(Exception):
    """Not an error: there is nothing to publish right now."""


@dataclass(frozen=True)
class Commit:
    subject: str
    body: str = ""


# ---------- version arithmetic ----------


def parse_version(text: str) -> tuple[int, int, int]:
    if not VERSION.match(text):
        raise ReleaseError(f"'{text}' is not a plain X.Y.Z version (no v prefix, no suffix).")
    major, minor, patch = (int(part) for part in text.split("."))
    return major, minor, patch


def latest_stable(tags: Iterable[str]) -> str | None:
    """The highest vX.Y.Z tag as X.Y.Z. Pre-release tags such as v1.0.0-rc.1 are ignored."""
    found = [m.groups() for tag in tags if (m := STABLE_TAG.match(tag))]
    if not found:
        return None
    major, minor, patch = max((int(a), int(b), int(c)) for a, b, c in found)
    return f"{major}.{minor}.{patch}"


def bump_kind(commits: Sequence[Commit]) -> str:
    """major for a breaking change, minor for a feature, otherwise patch."""
    commits = [c for c in commits if not RELEASE_SUBJECT.match(c.subject)]
    if any(BREAKING_SUBJECT.match(c.subject) or BREAKING_FOOTER.search(c.body) for c in commits):
        return "major"
    if any(FEATURE_SUBJECT.match(c.subject) for c in commits):
        return "minor"
    return "patch"


def bump(version: str, kind: str) -> str:
    major, minor, patch = parse_version(version)
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ReleaseError(f"Unknown bump '{kind}'.")


def next_version(
    latest: str | None,
    commits: Sequence[Commit],
    tags: Iterable[str] = (),
    explicit: str = "",
    forced: str = "auto",
) -> str:
    """The version to release; raises ReleaseError to refuse, NothingToRelease to skip."""
    existing = set(tags)
    if explicit:
        candidate = explicit
        parse_version(candidate)
    else:
        if latest is None:
            raise NothingToRelease(
                "No stable vX.Y.Z tag exists yet. Run the workflow by hand with an explicit "
                "version to start releasing."
            )
        if not [c for c in commits if not RELEASE_SUBJECT.match(c.subject)]:
            raise NothingToRelease(f"No commits since v{latest}.")
        kind = forced if forced != "auto" else bump_kind(commits)
        candidate = bump(latest, kind)
    if f"v{candidate}" in existing:
        raise ReleaseError(f"Tag v{candidate} already exists.")
    if latest is not None and parse_version(candidate) <= parse_version(latest):
        raise ReleaseError(f"{candidate} is not greater than the latest release {latest}.")
    return candidate


# ---------- git ----------


def git(*args: str) -> str:
    done = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def list_tags() -> list[str]:
    return [t for t in git("tag", "-l", "v*").splitlines() if t]


def range_base(tags: Sequence[str]) -> str | None:
    """Where the commit range starts: the latest stable tag, else the newest v* tag, else None."""
    stable = latest_stable(tags)
    if stable:
        return f"v{stable}"
    newest = git("tag", "-l", "v*", "--sort=-creatordate").splitlines()
    return newest[0] if newest else None


def commits_since(base: str | None) -> list[Commit]:
    span = f"{base}..HEAD" if base else "HEAD"
    raw = git("log", span, "--format=%s%x1f%b%x1e")
    commits = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if record:
            subject, _, body = record.partition("\x1f")
            commits.append(Commit(subject.strip(), body))
    return commits


# ---------- changelog ----------


def _unreleased_span(lines: list[str]) -> tuple[int, int]:
    """Line range of the body under '## [Unreleased]' (heading excluded); inserts nothing."""
    for index, line in enumerate(lines):
        if line.startswith("## [Unreleased]"):
            end = next(
                (i for i in range(index + 1, len(lines)) if lines[i].startswith("## [")), len(lines)
            )
            return index, end
    raise ReleaseError("CHANGELOG.md has no '## [Unreleased]' section.")


def synthesize_notes(commits: Sequence[Commit]) -> str:
    groups: dict[str, list[str]] = {"Added": [], "Fixed": [], "Changed": []}
    for commit in commits:
        if RELEASE_SUBJECT.match(commit.subject) or commit.subject.startswith("Merge "):
            continue
        text = TYPE_PREFIX.sub("", commit.subject).strip()
        if not text:
            continue
        if FEATURE_SUBJECT.match(commit.subject):
            groups["Added"].append(text)
        elif FIX_SUBJECT.match(commit.subject):
            groups["Fixed"].append(text)
        else:
            groups["Changed"].append(text)
    parts = [
        f"### {name}\n" + "\n".join(f"- {item}" for item in items)
        for name, items in groups.items()
        if items
    ]
    return "\n\n".join(parts) if parts else "- Maintenance release."


def rotate_changelog(text: str, version: str, today: str, commits: Sequence[Commit]) -> str:
    """Move the Unreleased notes under a dated version heading and leave an empty Unreleased."""
    lines = text.split("\n")
    start, end = _unreleased_span(lines)
    body = "\n".join(lines[start + 1 : end]).strip()
    if not body:
        body = synthesize_notes(commits)
    rotated = ["## [Unreleased]", "", f"## [{version}] - {today}", "", *body.split("\n"), ""]
    return "\n".join([*lines[:start], *rotated, *lines[end:]])


def version_section(text: str, version: str) -> str:
    """The body of '## [version]' (literal match, not a regex)."""
    lines = text.split("\n")
    heading = f"## [{version}]"
    start = next((i for i, line in enumerate(lines) if line.startswith(heading)), None)
    if start is None:
        raise ReleaseError(f"CHANGELOG.md has no section for {version}.")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## [")), len(lines))
    return "\n".join(lines[start + 1 : end]).strip()


# ---------- files that carry the version ----------


def read_raw(path: Path) -> str:
    """Read a file without translating line endings, so a rewrite keeps CRLF or LF as it was."""
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def write_raw(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def replace_once(text: str, pattern: str, replacement: str, name: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ReleaseError(f"Could not find the version line in {name}.")
    return updated


def write_versions(root: Path, version: str) -> None:
    edits = [
        (VERSION_PY, r'^__version__ = ".*"(?=\r?$)', f'__version__ = "{version}"'),
        (PYPROJECT, r'^version = ".*"(?=\r?$)', f'version = "{version}"'),
        (PACKAGE_JSON, r'^(\s*)"version": ".*",(?=\r?$)', rf'\1"version": "{version}",'),
    ]
    for path, pattern, replacement in edits:
        target = root / path
        write_raw(target, replace_once(read_raw(target), pattern, replacement, str(path)))


def read_versions(root: Path) -> dict[str, str]:
    """Read each file back with a parser, not with the regex that wrote it."""
    tree = ast.parse((root / VERSION_PY).read_text(encoding="utf-8"))
    py = next(
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets)
    )
    project = tomllib.loads((root / PYPROJECT).read_text(encoding="utf-8"))["project"]["version"]
    package = json.loads((root / PACKAGE_JSON).read_text(encoding="utf-8"))["version"]
    return {str(VERSION_PY): str(py), str(PYPROJECT): str(project), str(PACKAGE_JSON): str(package)}


def apply_release(root: Path, version: str, commits: Sequence[Commit], today: str) -> None:
    parse_version(version)
    write_versions(root, version)
    changelog = root / CHANGELOG
    raw = read_raw(changelog)
    newline = "\r\n" if "\r\n" in raw else "\n"
    rotated = rotate_changelog(raw.replace("\r\n", "\n"), version, today, commits)
    write_raw(changelog, rotated.replace("\n", newline))
    wrong = {name: found for name, found in read_versions(root).items() if found != version}
    if wrong:
        raise ReleaseError(f"Version files do not read back as {version}: {wrong}")
    version_section(read_raw(changelog).replace("\r\n", "\n"), version)


# ---------- command line ----------


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    nxt = commands.add_parser("next", help="print the next version")
    nxt.add_argument("--version", default="", help="release exactly this version (X.Y.Z)")
    nxt.add_argument("--bump", default="auto", choices=["auto", "major", "minor", "patch"])
    applying = commands.add_parser(
        "apply", help="write the version into the files and the changelog"
    )
    applying.add_argument("version")
    notes = commands.add_parser("notes", help="print one version's changelog section")
    notes.add_argument("version")
    args = parser.parse_args(argv)
    try:
        if args.command == "next":
            tags = list_tags()
            commits = commits_since(range_base(tags))
            print(
                next_version(
                    latest_stable(tags), commits, tags, args.version.strip(), args.bump or "auto"
                )
            )
        elif args.command == "apply":
            commits = commits_since(range_base(list_tags()))
            apply_release(ROOT, args.version, commits, datetime.now(UTC).strftime("%Y-%m-%d"))
        else:
            print(version_section(read_raw(ROOT / CHANGELOG).replace("\r\n", "\n"), args.version))
    except NothingToRelease as exc:
        print(f"Nothing to release: {exc}", file=sys.stderr)
        return NOTHING_TO_RELEASE
    except ReleaseError as exc:
        print(f"Refusing to release: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
