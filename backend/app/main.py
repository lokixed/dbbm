import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    auth,
    dashboard,
    destinations,
    health,
    jobs,
    notifications,
    retention,
    runs,
    schedule,
    sources,
)
from app.api import settings as settings_api
from app.api.deps import csrf_protect
from app.core.clock import Clock
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.migrations import run_migrations
from app.services.container import Services
from app.services.outbound import OutboundPolicy

logger = logging.getLogger(__name__)


def _validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc
    # FastAPI's default body echoes the submitted input, which may be a password.
    errors: list[dict[str, Any]] = [
        {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()
    ]
    return JSONResponse({"detail": errors}, status_code=422)


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    if not index.is_file():
        logger.info("Frontend build not found at %s; serving API only", dist)
        return
    root = dist.resolve()
    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path in ("api", "healthz") or full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_relative_to(root) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def create_app(
    *, outbound_policy: OutboundPolicy | None = None, clock: Clock | None = None
) -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level, secrets=[settings.master_key.decode("utf-8")])

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        run_migrations()
        services = Services.build(settings, outbound_policy, clock)
        app.state.services = services
        app.state.runner = services.runner
        await services.start()
        try:
            yield
        finally:
            await services.stop()

    app = FastAPI(
        title="DBBackup",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.enable_docs else None,
        openapi_url="/api/openapi.json" if settings.enable_docs else None,
        redoc_url=None,
        dependencies=[Depends(csrf_protect)],
    )
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(sources.router)
    app.include_router(destinations.router)
    app.include_router(jobs.router)
    app.include_router(runs.router)
    app.include_router(dashboard.router)
    app.include_router(retention.router)
    app.include_router(notifications.router)
    app.include_router(schedule.router)
    app.include_router(settings_api.router)
    _mount_frontend(app, settings.frontend_dist)
    return app
