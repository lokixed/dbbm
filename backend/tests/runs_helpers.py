import time
from typing import Any

from fastapi.testclient import TestClient

from app.core.config import get_settings
from tests.conftest import api_post

PW = "Sup3r-Secret-Pw!"
ACTIVE = ("queued", "running")


def make_source(
    client: TestClient, name: str = "mysql-main", kind: str = "mysql", **config: Any
) -> dict[str, Any]:
    if kind == "mysql":
        cfg = {"host": "db.internal", "username": "backup", "databases": ["shop", "blog"]}
    else:
        cfg = {"host": "pg.internal", "username": "reader", "databases": ["shop", "blog"]}
    body = {"type": kind, "name": name, "config": {**cfg, **config}, "secrets": {"password": PW}}
    response = api_post(client, "/api/sources", body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def make_destination(
    client: TestClient, name: str = "local1", path: str = "daily"
) -> dict[str, Any]:
    body = {"type": "local", "name": name, "config": {"path": path}}
    response = api_post(client, "/api/destinations", body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def make_job(
    client: TestClient,
    source: dict[str, Any],
    destinations: list[dict[str, Any]],
    name: str = "nightly",
    **over: Any,
) -> dict[str, Any]:
    body = {
        "name": name,
        "source_id": source["id"],
        "destination_ids": [d["id"] for d in destinations],
        **over,
    }
    response = api_post(client, "/api/jobs", body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def start(client: TestClient, job: dict[str, Any]) -> dict[str, Any]:
    response = api_post(client, f"/api/jobs/{job['id']}/run")
    assert response.status_code == 201, response.text
    run: dict[str, Any] = response.json()
    return run


def get_run(client: TestClient, run_id: str) -> dict[str, Any]:
    run: dict[str, Any] = client.get(f"/api/runs/{run_id}").json()
    return run


def wait_final(client: TestClient, run_id: str, timeout: float = 30) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = get_run(client, run_id)
        if run["status"] not in ACTIVE:
            return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish: {get_run(client, run_id)}")


def wait_until(predicate: Any, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not reached in time")


def run_now(client: TestClient, job: dict[str, Any]) -> dict[str, Any]:
    return wait_final(client, start(client, job)["id"])


def backup_files() -> list[str]:
    base = get_settings().backup_dir
    if not base.exists():
        return []
    return sorted(str(p.relative_to(base)) for p in base.rglob("*") if p.is_file())


def work_entries() -> list[str]:
    work = get_settings().work_dir
    return sorted(p.name for p in work.iterdir()) if work.exists() else []
