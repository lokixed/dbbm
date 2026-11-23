import asyncio
import json
import stat
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.drivers.base import DumpError
from app.drivers.mongo import (
    FULL_INSTANCE,
    MongoDriver,
    build_uri,
    strip_uri_database,
    write_config_file,
)
from app.schemas.source import MongoFieldsConfig
from tests.fakes import FakeBins, FakeMongo, alive
from tests.test_drivers_dump import ListLog, files, make_ctx

PW = "p@ss/w:rd#?%"
FIELDS: dict[str, Any] = {
    "mode": "fields",
    "host": "mongo.internal",
    "username": "backup",
    "databases": ["shop"],
}


def fields_driver(secret: str = PW, **over: Any) -> FakeMongo:
    return FakeMongo({**FIELDS, **over}, {"password": secret})


def uri_driver(uri: str, **over: Any) -> FakeMongo:
    return FakeMongo({"mode": "uri", "databases": ["shop"], **over}, {"uri": uri})


def read_uri(fake_bins: FakeBins) -> str:
    line = fake_bins.read("mongodump.yml").strip()
    assert line.startswith("uri: ")
    return str(json.loads(line.removeprefix("uri: ")))


# ---------- URI construction ----------


def test_field_uri_encodes_credentials_and_options() -> None:
    config = MongoFieldsConfig(
        **{
            **FIELDS,
            "username": "us er@x",
            "tls": True,
            "replica_set": "rs0",
            "auth_database": "adm",
        }
    )
    uri = build_uri(config, PW)
    assert uri == (
        "mongodb://us%20er%40x:p%40ss%2Fw%3Ard%23%3F%25@mongo.internal:27017/"
        "?authSource=adm&tls=true&replicaSet=rs0"
    )


def test_field_uri_without_credentials_has_no_auth_source() -> None:
    config = MongoFieldsConfig(mode="fields", host="mongo.internal", port=27018)
    assert build_uri(config, "") == "mongodb://mongo.internal:27018/"


def test_field_uri_brackets_ipv6_hosts() -> None:
    config = MongoFieldsConfig(mode="fields", host="::1")
    assert build_uri(config, "") == "mongodb://[::1]:27017/"


@pytest.mark.parametrize(
    ("uri", "expected", "database"),
    [
        ("mongodb://u:p@h:1/shop?tls=true", "mongodb://u:p@h:1/?tls=true&authSource=shop", "shop"),
        ("mongodb://u:p@h:1/shop", "mongodb://u:p@h:1/?authSource=shop", "shop"),
        (
            "mongodb+srv://u:p@c.example/shop?authSource=admin",
            "mongodb+srv://u:p@c.example/?authSource=admin",
            "shop",
        ),
        ("mongodb://u:p@h:1/?replicaSet=rs", "mongodb://u:p@h:1/?replicaSet=rs", None),
        ("mongodb://u:p@h1:1,h2:2", "mongodb://u:p@h1:1,h2:2", None),
    ],
)
def test_database_in_uri_is_stripped_but_stays_the_auth_database(
    uri: str, expected: str, database: str | None
) -> None:
    assert strip_uri_database(uri) == (expected, database)


# ---------- config file ----------


def test_config_file_is_0600_and_holds_the_exact_uri(tmp_path: Path) -> None:
    uri = 'mongodb://u:p"w\\d@h/?x=1'
    path = write_config_file(tmp_path, uri)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text().removeprefix("uri: ")) == uri


def test_dump_keeps_secrets_out_of_arguments_and_removes_the_config(
    fake_bins: FakeBins, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", "master-key-value")
    ctx, log = make_ctx(tmp_path / "work")
    art = asyncio.run(fields_driver().dump_database("shop", ctx))

    args = fake_bins.read("mongodump.args")
    assert "--db=shop" in args and "--gzip" in args and "--oplog" not in args
    assert "--archive=" in args and args.count("--config=") == 1
    for secret in (PW, "p%40ss", "mongodb://", "backup"):
        assert secret not in args
    assert "master-key-value" not in fake_bins.read("mongodump.env")
    assert fake_bins.read("mongodump.yml.perm").strip() == "600"
    assert read_uri(fake_bins).startswith(
        "mongodb://backup:p%40ss%2Fw%3Ard%23%3F%25@mongo.internal"
    )
    assert not any("mongodb://" in line or "p%40ss" in line for line in log.lines)
    # Only the finished artifact remains: no .partial file, no config directory.
    assert files(tmp_path / "work") == [art.path.name]


def test_artifact_is_hashed_after_the_dump(fake_bins: FakeBins, tmp_path: Path) -> None:
    import hashlib

    ctx, _ = make_ctx(tmp_path / "work")
    art = asyncio.run(fields_driver().dump_database("shop", ctx))
    data = art.path.read_bytes()
    assert data == b"fake-mongo-archive" and art.size == len(data)
    assert art.sha256 == hashlib.sha256(data).hexdigest()
    assert art.extension == "archive.gz" and art.compressed and art.compression == "gzip"
    assert art.path.name.endswith(".archive.gz") and not art.path.name.endswith(".partial")


def test_uri_mode_strips_the_path_database_and_uses_the_selected_one(
    fake_bins: FakeBins, tmp_path: Path
) -> None:
    ctx, log = make_ctx(tmp_path / "work")
    uri = "mongodb://u:s3cr3t@h1:27017/other?replicaSet=rs"
    asyncio.run(uri_driver(uri).dump_database("shop", ctx))
    assert "--db=shop" in fake_bins.read("mongodump.args")
    assert read_uri(fake_bins) == "mongodb://u:s3cr3t@h1:27017/?replicaSet=rs&authSource=other"
    assert any("authentication database" in line for line in log.lines)


# ---------- modes and database selection ----------


def test_all_excludes_admin_config_and_local() -> None:
    driver = fields_driver(databases="all")
    listed = ["admin", "blog", "config", "local", "shop"]
    assert driver.select_databases(listed) == ["blog", "shop"]


def test_explicit_selection_may_include_admin() -> None:
    driver = fields_driver(databases=["admin", "shop"])
    assert driver.select_databases(["blog"]) == ["admin", "shop"]


def test_full_oplog_ignores_the_database_selection() -> None:
    driver = fields_driver(dump_mode="full_oplog", databases=["shop"])
    assert driver.select_databases(["a", "b"]) == [FULL_INSTANCE]


def test_dump_mode_is_validated() -> None:
    with pytest.raises(ValidationError):
        MongoFieldsConfig(**{**FIELDS, "dump_mode": "everything"})
    assert MongoFieldsConfig(**FIELDS).dump_mode == "per_database"


def test_full_oplog_is_rejected_early_on_a_standalone_server() -> None:
    driver = fields_driver(dump_mode="full_oplog")
    assert driver.test_connection().ok
    with pytest.raises(DumpError, match="needs a replica set"):
        driver.check_compatibility("7.0.5", {})


def test_full_oplog_is_accepted_on_a_replica_set() -> None:
    class ReplicaSet(FakeMongo):
        replica_set = True

    driver = ReplicaSet({**FIELDS, "dump_mode": "full_oplog"}, {"password": PW})
    driver.test_connection()
    driver.check_compatibility("7.0.5", {})


def test_full_oplog_dump_uses_oplog_and_no_db(fake_bins: FakeBins, tmp_path: Path) -> None:
    ctx, _ = make_ctx(tmp_path / "work")
    driver = fields_driver(dump_mode="full_oplog")
    art = asyncio.run(driver.dump_database(FULL_INSTANCE, ctx))
    args = fake_bins.read("mongodump.args")
    assert "--oplog" in args and "--db" not in args
    assert art.database == "full-instance" and "full-instance" in art.path.name
    assert driver.manifest_extra() == {"dump_mode": "full_oplog"}


def test_full_oplog_mode_refuses_a_single_database(fake_bins: FakeBins, tmp_path: Path) -> None:
    ctx, _ = make_ctx(tmp_path / "work")
    with pytest.raises(DumpError, match="whole server"):
        asyncio.run(fields_driver(dump_mode="full_oplog").dump_database("shop", ctx))
    assert not fake_bins.exists("mongodump.args")


def test_per_database_mode_on_a_replica_set_warns_in_the_log(tmp_path: Path) -> None:
    class ReplicaSet(FakeMongo):
        replica_set = True

    driver = ReplicaSet(FIELDS, {"password": PW})
    driver.test_connection()
    ctx, log = make_ctx(tmp_path / "work")
    asyncio.run(driver.preflight(ctx))
    assert any("not point-in-time" in line and "oplog" in line for line in log.lines)
    standalone = fields_driver()
    standalone.test_connection()
    quiet = ListLog()
    asyncio.run(standalone.preflight(type(ctx)(ctx.work_dir, 30, True, quiet)))
    assert not any("point-in-time" in line for line in quiet.lines)


# ---------- failures ----------


def test_nonzero_exit_fails_even_though_bytes_were_written(
    fake_bins: FakeBins, tmp_path: Path
) -> None:
    fake_bins.set_mode("mongodump", "fail")
    fake_bins.set_text("stderr_text", f"Failed: could not connect mongodb://backup:{PW}@h/ {PW}")
    ctx, log = make_ctx(tmp_path / "work")
    with pytest.raises(DumpError) as caught:
        asyncio.run(fields_driver().dump_database("shop", ctx))
    message = str(caught.value)
    assert "exited with code 1" in message and "Failed: could not connect" in message
    assert PW not in message and "mongodb://" not in message
    assert "writing shop.items" not in message and "done dumping" not in message
    assert any("writing shop.items" in line for line in log.lines)  # the log keeps the noise
    assert not any(PW in line for line in log.lines)
    assert files(tmp_path / "work") == []


def test_timeout_kills_the_process_group(fake_bins: FakeBins, tmp_path: Path) -> None:
    fake_bins.set_mode("mongodump", "hang")
    ctx, _ = make_ctx(tmp_path / "work", timeout=1)
    with pytest.raises(DumpError, match="timed out after 1 seconds"):
        asyncio.run(fields_driver().dump_database("shop", ctx))
    assert not alive(int(fake_bins.read("pid"))) and not alive(int(fake_bins.read("childpid")))
    assert files(tmp_path / "work") == []


def test_missing_mongodump_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    with pytest.raises(DumpError, match="mongodump is not installed"):
        asyncio.run(MongoDriver(FIELDS, {}).tool_versions())


def test_tool_versions_use_the_first_line(fake_bins: FakeBins) -> None:
    assert asyncio.run(fields_driver().tool_versions()) == {
        "mongodump": "mongodump version: 100.9.4"
    }
