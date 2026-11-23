"""Fake dump tools and drivers, so run lifecycle tests need no database server."""

import os
import stat
from pathlib import Path
from typing import Any

import pytest

from app.drivers.base import ConnectionTestResult, RestoreOptions
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

_MONGORESTORE = """#!/bin/sh
dir=$(dirname "$0")
case "$1" in
  --version) echo "mongorestore version: 100.9.4"; echo "git version: abc"; exit 0;;
esac
echo "$*" > "$dir/mongorestore.args"
archive=""; config=""
for a; do
  case "$a" in
    --archive=*) archive="${a#--archive=}";;
    --config=*) config="${a#--config=}";;
  esac
done
cp "$config" "$dir/mongorestore.yml"
stat -c %a "$config" > "$dir/mongorestore.yml.perm"
cp "$archive" "$dir/mongorestore.archive"
mode=$(cat "$dir/mongorestore.mode" 2>/dev/null || echo ok)
case "$mode" in
  ok)
    echo "2024-01-01T00:00:00.000+0000	preparing collections to restore from" >&2
    echo "2024-01-01T00:00:00.100+0000	finished restoring shop.items (3 documents, 0 failures)" >&2
    exit 0;;
  fail)
    echo "2024-01-01T00:00:00.000+0000	preparing collections to restore from" >&2
    echo "2024-01-01T00:00:00.100+0000	Failed: shop.items: error restoring from archive: not authorized" >&2
    exit 1;;
  hang)
    echo "$$" > "$dir/restore.pid"
    sleep 300 &
    echo "$!" > "$dir/restore.childpid"
    wait;;
esac
"""

_MYSQL_CLIENT = """#!/bin/sh
dir=$(dirname "$0")
case "$1" in
  --version) echo "mysql  Ver 8.4.0 for Linux on x86_64"; exit 0;;
esac
echo "$*" > "$dir/mysql.args"
cnf="${1#--defaults-extra-file=}"
cp "$cnf" "$dir/mysql.cnf"
stat -c %a "$cnf" > "$dir/mysql.cnf.mode"
mode=$(cat "$dir/mysql.mode" 2>/dev/null || echo ok)
case "$mode" in
  ok) cat > "$dir/mysql.stdin"; exit 0;;
  fail)
    cat > /dev/null
    echo "ERROR 1044 (42000) at line 12: Access denied for user to database" >&2
    exit 1;;
  hang)
    echo "$$" > "$dir/restore.pid"
    sleep 300 &
    echo "$!" > "$dir/restore.childpid"
    wait;;
esac
"""

_PG_RESTORE = """#!/bin/sh
dir=$(dirname "$0")
case "$1" in
  --version) echo "pg_restore (PostgreSQL) $(cat "$dir/pg_restore_version" 2>/dev/null || echo 16.4)"; exit 0;;
esac
echo "$*" > "$dir/pg_restore.args"
echo "$PGPASSWORD" > "$dir/pg_restore.pgpassword"
for last; do :; done
cp "$last" "$dir/pg_restore.input"
mode=$(cat "$dir/pg_restore.mode" 2>/dev/null || echo ok)
case "$mode" in
  ok) exit 0;;
  harmless)
    echo 'pg_restore: error: could not execute query: ERROR:  unrecognized configuration parameter "transaction_timeout"' >&2
    echo 'Command was: SET transaction_timeout = 0;' >&2
    echo 'pg_restore: warning: errors ignored on restore: 1' >&2
    exit 1;;
  fail)
    echo 'pg_restore: error: could not execute query: ERROR:  permission denied for schema public' >&2
    exit 1;;
esac
"""

_AGE = """#!/bin/sh
# A stand-in for the age CLI: "encrypts" to a header listing the recipients, and "decrypts" only
# when the identity on stdin matches one of them. It fails the way age does for a wrong key, a
# truncated file and a damaged file. Identities are never recorded.
dir=$(dirname "$0")
case "$1" in
  --version) echo "v1.3.2"; exit 0;;
esac
prev=""; out=""; recips=""; ident=""
for a; do
  case "$prev" in
    -o) out="$a";;
    -r) recips="$recips $a";;
    -i) ident="$a";;
  esac
  prev="$a"
done
src="$a"
case "$1" in
  --encrypt)
    echo "$*" > "$dir/age.encrypt.args"
    [ "$(cat "$dir/age.mode" 2>/dev/null || echo ok)" = fail ] && { echo "age: error: simulated failure" >&2; exit 1; }
    size=$(wc -c < "$src" | tr -d ' ')
    sum=$(cksum < "$src" | cut -d' ' -f1)
    { printf 'AGEFAKE\n%s\n%s\n%s\n' "$recips " "$size" "$sum"; cat "$src"; } > "$out"
    exit 0;;
  --decrypt)
    echo "$*" > "$dir/age.decrypt.args"
    [ "$ident" = "-" ] || { echo "age: error: this fake needs -i -" >&2; exit 1; }
    key=$(cat)
    [ -s "$src" ] || { echo "age: error: failed to read header: parsing age header: file is empty" >&2; exit 1; }
    [ "$(head -n 1 "$src")" = AGEFAKE ] || { echo "age: error: failed to read header: parsing age header: unexpected intro: \"hello\"" >&2; exit 1; }
    [ "$(wc -l < "$src")" -ge 4 ] || { echo "age: error: failed to read header: failed to parse header: failed to read line: EOF" >&2; exit 1; }
    pub="age1$(printf %s "${key#AGE-SECRET-KEY-1}" | tr 'A-Z' 'a-z')"
    case "$(sed -n 2p "$src")" in
      *" $pub "*) ;;
      *) echo "age: error: no identity matched any of the recipients" >&2; exit 1;;
    esac
    size=$(sed -n 3p "$src"); sum=$(sed -n 4p "$src")
    header=$(head -n 4 "$src" | wc -c | tr -d ' ')
    tail -c +$((header + 1)) "$src" > "$out.body"
    have=$(wc -c < "$out.body" | tr -d ' ')
    if [ "$have" -lt "$size" ]; then rm -f "$out.body"; echo "age: error: failed to decrypt and authenticate payload chunk, file may be corrupted or tampered with" >&2; exit 1; fi
    if [ "$(cksum < "$out.body" | cut -d' ' -f1)" != "$sum" ]; then rm -f "$out.body"; echo "age: error: failed to decrypt and authenticate payload chunk, file may be corrupted or tampered with" >&2; exit 1; fi
    mv "$out.body" "$out"
    exit 0;;
esac
echo "fake age: unhandled: $*" >&2
exit 2
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
  "firestore import")
    echo "$*" > "$dir/gcloud.import.args"
    [ "$(mode import)" = fail ] && fail
    echo '{"name": "projects/p1/databases/(default)/operations/IMP456"}'
    exit 0;;
  "storage cp")
    echo "$*" > "$dir/gcloud.cp.args"
    case "$last" in
      gs://*)
        # An upload to the staging bucket: record what would be copied.
        echo "$*" > "$dir/gcloud.upload.args"
        [ "$(mode upload)" = fail ] && fail
        find "$4" -type f | sed "s#^$4/##" | sort > "$dir/gcloud.upload.files"
        exit 0;;
    esac
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
        ("mongorestore", _MONGORESTORE),
        ("mysql", _MYSQL_CLIENT),
        ("pg_restore", _PG_RESTORE),
        ("age", _AGE),
        ("gcloud", _GCLOUD),
    ):
        path = directory / name
        path.write_text(script)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    return FakeBins(directory)


class FakeMySQL(MySQLDriver):
    prepared: list[tuple[str, bool]] = []

    def _prepare_target_sync(self, options: RestoreOptions, timeout: int) -> None:
        FakeMySQL.prepared.append((options.target_database, options.drop))

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
    prepared: list[str] = []

    def _prepare_target_sync(self, options: RestoreOptions) -> None:
        FakePostgres.prepared.append(options.target_database)

    def _probe(self) -> ConnectionTestResult:
        self._server_version = self.server_version
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
    monkeypatch.setattr("app.services.restore_run.get_driver", factory)
