import asyncio
import gzip
import hashlib
import stat
from pathlib import Path
from typing import Any

import pytest

from app.drivers.base import DumpContext, DumpError
from app.drivers.mysql import MySQLDriver
from app.drivers.postgres import PostgresDriver, client_major, server_major
from app.schemas.source import MySQLConfig
from tests.fakes import FakeBins, alive

PW = "Sup3r-Secret-Pw!"
MYSQL_CONFIG: dict[str, Any] = {"host": "db.internal", "username": "backup", "databases": "all"}
PG_CONFIG: dict[str, Any] = {"host": "pg.internal", "username": "reader", "databases": "all"}


class ListLog:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, message: str) -> None:
        self.lines.append(message)

    warning = error = info


def make_ctx(
    work: Path, *, timeout: int = 30, compress: bool = True
) -> tuple[DumpContext, ListLog]:
    work.mkdir(parents=True, exist_ok=True)
    log = ListLog()
    return DumpContext(work, timeout, compress, log), log


def mysql(**over: Any) -> MySQLDriver:
    return MySQLDriver({**MYSQL_CONFIG, **over}, {"password": PW})


def postgres(**over: Any) -> PostgresDriver:
    return PostgresDriver({**PG_CONFIG, **over}, {"password": PW})


def files(work: Path) -> list[str]:
    return sorted(str(p.relative_to(work)) for p in work.rglob("*"))


# ---------- database selection and version checks ----------


def test_mysql_all_excludes_system_databases() -> None:
    listed = ["app", "information_schema", "mysql", "performance_schema", "sys", "zeta"]
    assert mysql().select_databases(listed) == ["app", "zeta"]


def test_mysql_explicit_selection_is_used_as_is() -> None:
    assert mysql(databases=["mysql", "app"]).select_databases(["app"]) == ["mysql", "app"]


def test_postgres_all_excludes_templates_and_postgres() -> None:
    listed = ["app", "postgres", "template0", "template1", "zeta"]
    assert postgres().select_databases(listed) == ["app", "zeta"]


def test_postgres_explicit_postgres_database_is_allowed() -> None:
    assert postgres(databases=["postgres"]).select_databases(["app"]) == ["postgres"]


@pytest.mark.parametrize(
    ("line", "major"),
    [
        ("pg_dump (PostgreSQL) 16.4 (Debian 16.4-1.pgdg120+1)", 16),
        ("pg_dump (PostgreSQL) 17beta1", 17),
    ],
)
def test_client_major(line: str, major: int) -> None:
    assert client_major(line) == major


def test_server_major() -> None:
    assert server_major("16.4 (Debian)") == 16 and server_major("9.6.24") == 9
    assert server_major(None) is None and server_major("") is None


def test_older_pg_client_than_server_fails_early() -> None:
    tools = {"pg_dump": "pg_dump (PostgreSQL) 15.6"}
    with pytest.raises(DumpError, match=r"older than the PostgreSQL server \(16\)"):
        postgres().check_compatibility("16.2", tools)
    postgres().check_compatibility("15.1", tools)
    postgres().check_compatibility("14.9", tools)
    postgres().check_compatibility(None, tools)


def test_tool_versions_come_from_the_installed_binaries(fake_bins: FakeBins) -> None:
    fake_bins.set_text("pg_version", "15.6")
    tools = asyncio.run(postgres().tool_versions())
    assert tools == {"pg_dump": "pg_dump (PostgreSQL) 15.6"}
    assert "mysqldump" in asyncio.run(mysql().tool_versions())["mysqldump"]


def test_missing_tool_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    with pytest.raises(DumpError, match="mysqldump is not installed"):
        asyncio.run(mysql().tool_versions())


# ---------- mysql ----------


def test_mysql_dump_streams_through_gzip(fake_bins: FakeBins, tmp_path: Path) -> None:
    ctx, log = make_ctx(tmp_path / "work")
    art = asyncio.run(mysql().dump_database("shop", ctx))
    assert art.extension == "sql.gz" and art.compressed and art.path.name.endswith(".sql.gz")
    raw = art.path.read_bytes()
    assert gzip.decompress(raw).decode().startswith("-- fake dump of shop")
    assert art.size == len(raw) and art.sha256 == hashlib.sha256(raw).hexdigest()
    assert stat.S_IMODE(art.path.stat().st_mode) == 0o600
    # Only the artifact is left: no .partial file and no options file directory.
    assert files(tmp_path / "work") == [art.path.name]
    assert any("Running: mysqldump --defaults-extra-file=" in line for line in log.lines)


def test_mysql_uncompressed_when_compress_is_off(fake_bins: FakeBins, tmp_path: Path) -> None:
    ctx, _ = make_ctx(tmp_path / "work", compress=False)
    art = asyncio.run(mysql().dump_database("shop", ctx))
    assert art.extension == "sql" and not art.compressed
    assert art.path.read_text().startswith("-- fake dump of shop")


def test_mysql_credentials_only_in_the_private_options_file(
    fake_bins: FakeBins, tmp_path: Path
) -> None:
    ctx, log = make_ctx(tmp_path / "work")
    asyncio.run(mysql().dump_database("shop", ctx))
    args = fake_bins.read("mysqldump.args").split()
    assert args[0].startswith("--defaults-extra-file=")  # must be the first argument
    assert args[-1] == "shop" and "--single-transaction" in args and "--databases" not in args
    assert {"--routines", "--triggers", "--events", "--set-gtid-purged=OFF"} <= set(args)
    assert "--no-tablespaces" in args  # tablespaces would need the PROCESS privilege
    assert PW not in " ".join(args) and not any(PW in line for line in log.lines)
    assert fake_bins.read("mysqldump.cnf.mode").strip() == "600"
    assert f'password="{PW}"' in fake_bins.read("mysqldump.cnf")


def test_mysql_nonzero_exit_with_partial_output_fails(fake_bins: FakeBins, tmp_path: Path) -> None:
    fake_bins.set_mode("mysqldump", "fail")
    fake_bins.set_text("stderr_text", f"mysqldump: Got error: 1045 for user using password={PW}\n")
    ctx, log = make_ctx(tmp_path / "work")
    with pytest.raises(DumpError) as raised:
        asyncio.run(mysql().dump_database("shop", ctx))
    message = str(raised.value)
    assert "exited with code 2" in message and "1045" in message and PW not in message
    assert not any(PW in line for line in log.lines)
    assert files(tmp_path / "work") == []  # the partial output was removed


def test_mysql_timeout_kills_the_process_group(fake_bins: FakeBins, tmp_path: Path) -> None:
    fake_bins.set_mode("mysqldump", "hang")
    ctx, _ = make_ctx(tmp_path / "work", timeout=1)
    with pytest.raises(DumpError, match="timed out after 1 seconds"):
        asyncio.run(mysql().dump_database("shop", ctx))
    assert not alive(int(fake_bins.read("pid"))) and not alive(int(fake_bins.read("childpid")))
    assert files(tmp_path / "work") == []


def test_mysql_rejects_option_like_database_names(fake_bins: FakeBins, tmp_path: Path) -> None:
    ctx, _ = make_ctx(tmp_path / "work")
    with pytest.raises(DumpError):
        asyncio.run(mysql().dump_database("--all-databases", ctx))
    assert not fake_bins.exists("mysqldump.args")


def test_mysql_options_file_escapes_special_characters(tmp_path: Path) -> None:
    driver = MySQLDriver(MYSQL_CONFIG, {"password": 'p"a\\s#s\nx'})
    path = driver._write_options_file(MySQLConfig(**MYSQL_CONFIG), tmp_path)
    assert 'password="p\\"a\\\\s#s\\nx"' in path.read_text()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


# ---------- postgres ----------


def test_postgres_dump_uses_custom_format_and_env_password(
    fake_bins: FakeBins, tmp_path: Path
) -> None:
    ctx, log = make_ctx(tmp_path / "work")
    art = asyncio.run(postgres().dump_database("shop", ctx))
    assert art.extension == "dump" and art.compressed and art.compression == "pg_dump_custom"
    assert art.path.read_bytes() == b"PGDMP-fake-custom-format"
    assert art.sha256 == hashlib.sha256(art.path.read_bytes()).hexdigest()
    args = fake_bins.read("pg_dump.args").split()
    assert "-Fc" in args and args[args.index("-d") + 1] == "shop"
    assert PW not in " ".join(args) and not any(PW in line for line in log.lines)
    assert fake_bins.read("pg_dump.pgpassword").strip() == PW
    assert files(tmp_path / "work") == [art.path.name]


def test_postgres_nonzero_exit_with_partial_output_fails(
    fake_bins: FakeBins, tmp_path: Path
) -> None:
    fake_bins.set_mode("pg_dump", "fail")
    fake_bins.set_text("stderr_text", f"pg_dump: error: connection failed, PGPASSWORD={PW}\n")
    ctx, _ = make_ctx(tmp_path / "work")
    with pytest.raises(DumpError) as raised:
        asyncio.run(postgres().dump_database("shop", ctx))
    assert "exited with code 3" in str(raised.value) and PW not in str(raised.value)
    assert files(tmp_path / "work") == []


def test_postgres_timeout_kills_the_process_group(fake_bins: FakeBins, tmp_path: Path) -> None:
    fake_bins.set_mode("pg_dump", "hang")
    ctx, _ = make_ctx(tmp_path / "work", timeout=1)
    with pytest.raises(DumpError, match="timed out"):
        asyncio.run(postgres().dump_database("shop", ctx))
    assert not alive(int(fake_bins.read("pid"))) and not alive(int(fake_bins.read("childpid")))


@pytest.mark.parametrize("name", ["host=evil.example dbname=x", "postgresql://evil/x"])
def test_postgres_rejects_connection_string_database_names(
    fake_bins: FakeBins, tmp_path: Path, name: str
) -> None:
    ctx, _ = make_ctx(tmp_path / "work")
    with pytest.raises(DumpError):
        asyncio.run(postgres().dump_database(name, ctx))
    assert not fake_bins.exists("pg_dump.args")


def test_postgres_globals_are_optional(fake_bins: FakeBins, tmp_path: Path) -> None:
    ctx, _ = make_ctx(tmp_path / "work")
    assert asyncio.run(postgres().dump_globals(ctx)) is None
    art = asyncio.run(postgres(include_globals=True).dump_globals(ctx))
    assert art is not None and art.database == "globals" and art.extension == "sql.gz"
    assert b"CREATE ROLE" in gzip.decompress(art.path.read_bytes())


def test_postgres_globals_failure_raises(fake_bins: FakeBins, tmp_path: Path) -> None:
    fake_bins.set_mode("pg_dumpall", "fail")
    ctx, _ = make_ctx(tmp_path / "work")
    with pytest.raises(DumpError, match="pg_dumpall exited with code 1"):
        asyncio.run(postgres(include_globals=True).dump_globals(ctx))
    assert files(tmp_path / "work") == []


# ---------- process arguments ----------


def test_no_password_in_any_spawned_argument_list(
    fake_bins: FakeBins, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: list[tuple[Any, ...]] = []
    original = asyncio.create_subprocess_exec

    async def spy(*args: Any, **kwargs: Any) -> Any:
        recorded.append(args)
        return await original(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    ctx, _ = make_ctx(tmp_path / "work")

    async def scenario() -> None:
        my, pg = mysql(), postgres(include_globals=True)
        await my.tool_versions()
        await my.dump_database("shop", ctx)
        await pg.tool_versions()
        await pg.dump_database("shop", ctx)
        await pg.dump_globals(ctx)

    asyncio.run(scenario())
    assert len(recorded) == 7
    for args in recorded:
        assert all(PW not in str(arg) for arg in args), args
        assert all(isinstance(arg, str) for arg in args)  # argument lists, never a shell string
