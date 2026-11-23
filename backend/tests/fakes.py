"""Fake dump tools and drivers, so run lifecycle tests need no database server."""

import os
import stat
from pathlib import Path
from typing import Any

import pytest

from app.drivers.base import ConnectionTestResult
from app.drivers.firestore import FirestoreDriver
from app.drivers.mongo import MongoDriver
from app.drivers.mysql import MySQLDriver
from app.drivers.postgres import PostgresDriver

_MYSQLDUMP = """#!/bin/sh
dir=$(dirname "$0")
case "$1" in
  --version) echo "mysqldump  Ver 8.4.0 for Linux on x86_64"; exit 0;;
  --help) echo "  --set-gtid-purged=name"; echo "  --no-tablespaces"; exit 0;;
esac
for last; do :; done
echo "$*" > "$dir/mysqldump.args"
opts="${1#--defaults-extra-file=}"
cp "$opts" "$dir/mysqldump.cnf"
stat -c %a "$opts" > "$dir/mysqldump.cnf.mode"
mode=$(cat "$dir/mysqldump.mode" 2>/dev/null || echo ok)
case "$mode" in
  ok) echo "-- fake dump of $last"; echo "CREATE TABLE t (id int);"; exit 0;;
  fail)
    echo "-- partial output before the crash"
    cat "$dir/stderr_text" >&2
    exit 2;;
  hang)
    echo "$$" > "$dir/pid"
    sleep 300 &
    echo "$!" > "$dir/childpid"
    wait;;
esac
"""

_PG_DUMP = """#!/bin/sh
dir=$(dirname "$0")
case "$1" in
  --version) echo "pg_dump (PostgreSQL) $(cat "$dir/pg_version" 2>/dev/null || echo 16.4)"; exit 0;;
esac
echo "$*" > "$dir/pg_dump.args"
echo "$PGPASSWORD" > "$dir/pg_dump.pgpassword"
out=""; prev=""
for a; do
  if [ "$prev" = "-f" ]; then out="$a"; fi
  prev="$a"
done
mode=$(cat "$dir/pg_dump.mode" 2>/dev/null || echo ok)
case "$mode" in
  ok) printf 'PGDMP-fake-custom-format' > "$out"; exit 0;;
  fail)
    printf 'PGDMP-partial' > "$out"
    cat "$dir/stderr_text" >&2
    exit 3;;
  hang)
    echo "$$" > "$dir/pid"
    sleep 300 &
    echo "$!" > "$dir/childpid"
    wait;;
esac
"""

_PG_DUMPALL = """#!/bin/sh
dir=$(dirname "$0")
case "$1" in
  --version) echo "pg_dumpall (PostgreSQL) 16.4"; exit 0;;
esac
mode=$(cat "$dir/pg_dumpall.mode" 2>/dev/null || echo ok)
case "$mode" in
  ok) echo "CREATE ROLE reader;"; exit 0;;
  fail) echo "pg_dumpall: error: query failed: permission denied for table pg_authid" >&2; exit 1;;
esac
"""

_MONGODUMP = """#!/bin/sh
dir=$(dirname "$0")
case "$1" in
  --version) echo "mongodump version: 100.9.4"; echo "git version: abc"; exit 0;;
esac
echo "$*" > "$dir/mongodump.args"
env > "$dir/mongodump.env"
archive=""; config=""
for a; do
  case "$a" in
    --archive=*) archive="${a#--archive=}";;
    --config=*) config="${a#--config=}";;
  esac
done
cp "$config" "$dir/mongodump.yml"
stat -c %a "$config" > "$dir/mongodump.yml.perm"
mode=$(cat "$dir/mongodump.mode" 2>/dev/null || echo ok)
case "$mode" in
  ok)
    echo "2024-01-01T00:00:00.000+0000	writing shop.items to archive on stdout" >&2
    echo "2024-01-01T00:00:00.100+0000	done dumping shop.items (3 documents)" >&2
    printf 'fake-mongo-archive' > "$archive"; exit 0;;
  fail)
    printf 'partial-archive' > "$archive"
    echo "2024-01-01T00:00:00.000+0000	writing shop.items to archive on stdout" >&2
    cat "$dir/stderr_text" >&2; echo >&2
    echo "2024-01-01T00:00:00.100+0000	done dumping shop.items (3 documents)" >&2
    exit 1;;
  hang)
    echo "$$" > "$dir/pid"
    sleep 300 &
    echo "$!" > "$dir/childpid"
    wait;;
esac
"""

_GCLOUD = """#!/bin/sh
dir=$(dirname "$0")
mode() { cat "$dir/gcloud_$1.mode" 2>/dev/null || echo ok; }
fail() { cat "$dir/gcloud_stderr" >&2 2>/dev/null || echo "ERROR: simulated failure" >&2; exit 1; }
echo "$*" >> "$dir/gcloud.calls"
case "$1" in
  --version) echo "Google Cloud SDK 999.0.0"; echo "bq 2.1.0"; exit 0;;
esac
for last; do :; done
case "$1 $2" in
  "auth activate-service-account")
    key="${3#--key-file=}"
    cp "$key" "$dir/gcloud.key"
    stat -c %a "$key" > "$dir/gcloud.key.mode"
    echo "$key" > "$dir/gcloud.key.path"
    echo "$CLOUDSDK_CONFIG|$HOME|$CLOUDSDK_CORE_DISABLE_PROMPTS|$CLOUDSDK_CORE_DISABLE_USAGE_REPORTING|$CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK" > "$dir/gcloud.env"
    [ "$(mode auth)" = fail ] && fail
    echo "credentials" > "$CLOUDSDK_CONFIG/credentials.db"
    exit 0;;
  "storage ls") [ "$(mode ls)" = fail ] && fail; exit 0;;
  "firestore export")
    echo "$*" > "$dir/gcloud.export.args"
    [ "$(mode export)" = fail ] && fail
    echo '{"name": "projects/p1/databases/(default)/operations/OP123"}'
    exit 0;;
  "firestore operations")
    case "$3" in
      describe)
        n=$(cat "$dir/describe.count" 2>/dev/null || echo 0)
        n=$((n + 1)); echo "$n" > "$dir/describe.count"
        case "$(mode describe)" in
          forever) echo '{"done": false, "metadata": {"operationState": "PROCESSING"}}'; exit 0;;
          broken) fail;;
          error) echo '{"done": true, "metadata": {"operationState": "FAILED"}, "error": {"code": 7, "message": "PERMISSION_DENIED token=ya29.secretvalue"}}'; exit 0;;
        esac
        if [ "$n" -le 1 ]; then
          echo '{"done": false, "metadata": {"operationState": "PROCESSING", "progressDocuments": {"completedWork": "5", "estimatedWork": "10"}}}'
        else
          echo '{"done": true, "metadata": {"operationState": "SUCCESSFUL"}}'
        fi
        exit 0;;
      cancel) echo "$*" > "$dir/cancel.called"; [ "$(mode cancel)" = fail ] && fail; exit 0;;
    esac;;
  "storage cp")
    echo "$*" > "$dir/gcloud.cp.args"
    [ "$(mode cp)" = fail ] && fail
    mkdir -p "$last/ts/all_namespaces/kind_items"
    echo "data" > "$last/ts/all_namespaces/kind_items/output-0"
    [ "$(mode cp)" = nometa ] || echo "meta" > "$last/ts/ts.overall_export_metadata"
    exit 0;;
  "storage rm")
    echo "$*" > "$dir/gcloud.rm.args"
    [ "$(mode rm)" = fail ] && fail
    exit 0;;
esac
echo "fake gcloud: unhandled: $*" >&2
exit 2
"""


class FakeBins:
    def __init__(self, directory: Path) -> None:
        self.dir = directory

    def set_mode(self, tool: str, mode: str) -> None:
        (self.dir / f"{tool}.mode").write_text(mode)

    def set_text(self, name: str, text: str) -> None:
        (self.dir / name).write_text(text)

    def read(self, name: str) -> str:
        return (self.dir / name).read_text()

    def exists(self, name: str) -> bool:
        return (self.dir / name).exists()


@pytest.fixture
def fake_bins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeBins:
    directory = tmp_path / "fakebin"
    directory.mkdir()
    for name, script in (
        ("mysqldump", _MYSQLDUMP),
        ("pg_dump", _PG_DUMP),
        ("pg_dumpall", _PG_DUMPALL),
        ("mongodump", _MONGODUMP),
        ("gcloud", _GCLOUD),
    ):
        path = directory / name
        path.write_text(script)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    return FakeBins(directory)


class FakeMySQL(MySQLDriver):
    listed = ["blog", "mysql", "shop"]
    ok = True

    def _probe(self) -> ConnectionTestResult:
        if not self.ok:
            return ConnectionTestResult(ok=False, message="OperationalError: Access denied")
        return ConnectionTestResult(
            ok=True, message="Connected to MySQL 8.0.36.", server_version="8.0.36",
            databases=list(self.listed),
        )  # fmt: skip


class FakePostgres(PostgresDriver):
    listed = ["blog", "postgres", "shop"]
    server_version = "16.4"

    def _probe(self) -> ConnectionTestResult:
        return ConnectionTestResult(
            ok=True,
            message=f"Connected to PostgreSQL {self.server_version}.",
            server_version=self.server_version,
            databases=list(self.listed),
        )


class FakeMongo(MongoDriver):
    listed = ["admin", "blog", "shop"]
    replica_set = False

    def _probe(self) -> ConnectionTestResult:
        self._server_version = "7.0.5"
        self._is_replica_set = self.replica_set
        return ConnectionTestResult(
            ok=True, message="Connected to MongoDB 7.0.5.", server_version="7.0.5",
            databases=list(self.listed),
        )  # fmt: skip


class FakeFirestore(FirestoreDriver):
    def _probe(self) -> ConnectionTestResult:
        return ConnectionTestResult(ok=True, message="Connected to Firestore.")


def alive(pid: int) -> bool:
    """True if the process exists and is not a zombie."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


@pytest.fixture
def fake_drivers(monkeypatch: pytest.MonkeyPatch) -> None:
    def factory(source_type: str, config: dict[str, object], secrets: dict[str, str]) -> object:
        cls: Any = {
            "mysql": FakeMySQL,
            "postgres": FakePostgres,
            "mongo": FakeMongo,
            "firestore": FakeFirestore,
        }[source_type]
        return cls(config, secrets)

    monkeypatch.setattr("app.services.runner.get_driver", factory)
