"""Per-collection / per-table restore options and the restore tool-version checks."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.drivers.base import RestoreError, RestoreOptions
from app.drivers.mongo import MongoDriver
from app.drivers.postgres import PostgresDriver
from tests.conftest import api_post
from tests.runs_helpers import make_destination, make_job, make_source, run_now
from tests.test_restore_api import backed_up_mongo, restore, restore_body
from tests.test_runs_m4 import make_mongo

OPTIONS = RestoreOptions("shop", "shop_copy", only=("orders", "users"))


def test_mongo_argv_limits_namespaces_to_selected_collections() -> None:
    argv = MongoDriver._restore_argv(Path("cfg"), Path("a.archive.gz"), OPTIONS)
    assert "--nsInclude=shop.orders" in argv and "--nsInclude=shop.users" in argv
    assert "--nsInclude=shop.*" not in argv
    assert "--nsFrom=shop.*" in argv and "--nsTo=shop_copy.*" in argv


def test_mongo_argv_defaults_to_the_whole_database() -> None:
    argv = MongoDriver._restore_argv(Path("cfg"), Path("a"), RestoreOptions("shop", "shop"))
    assert "--nsInclude=shop.*" in argv


def test_old_pg_restore_is_refused() -> None:
    driver = PostgresDriver({}, {})
    driver._server_version = "16.2"
    with pytest.raises(RestoreError, match="older than the PostgreSQL server"):
        driver.check_restore_compatibility({"pg_restore": "pg_restore (PostgreSQL) 15.4"})
    assert driver.check_restore_compatibility({"pg_restore": "pg_restore (PostgreSQL) 17.0"}) == []


def test_old_mongorestore_only_warns() -> None:
    driver = MongoDriver({}, {})
    driver._server_version = "7.0.5"
    warnings = driver.check_restore_compatibility({"mongorestore": "mongorestore version: 6.0.1"})
    assert len(warnings) == 1 and "older" in warnings[0]
    assert driver.check_restore_compatibility({"mongorestore": "mongorestore version: 7.0.1"}) == []


pytestmark = pytest.mark.usefixtures("fake_bins", "fake_drivers")


def test_selection_rejects_bad_names(authed: TestClient) -> None:
    _, backup = backed_up_mongo(authed)
    target = make_mongo(authed, "mongo-scratch")
    body = restore_body(target, only=["ok", "bad name; rm"])
    assert restore(authed, backup, body).status_code == 422


def test_selection_is_refused_for_mysql(authed: TestClient) -> None:
    source = make_source(authed)
    job = make_job(authed, source, [make_destination(authed)])
    assert run_now(authed, job)["status"] == "success"
    backup = authed.get("/api/backups").json()["items"][0]
    response = api_post(
        authed,
        f"/api/backups/{backup['id']}/restore",
        {
            "target_source_id": source["id"],
            "target_database": "copy",
            "confirm_database": "copy",
            "only": ["t1"],
        },
    )
    assert response.status_code == 422 and "MongoDB and PostgreSQL" in response.json()["detail"]


def test_selection_is_accepted_for_mongo(authed: TestClient) -> None:
    _, backup = backed_up_mongo(authed)
    target = make_mongo(authed, "mongo-scratch")
    response = restore(authed, backup, restore_body(target, only=["orders"]))
    assert response.status_code == 201, response.text
