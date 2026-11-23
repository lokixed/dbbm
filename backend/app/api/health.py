import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.db import SessionDep
from app.version import __version__

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz(request: Request, db: SessionDep) -> JSONResponse:
    body: dict[str, Any] = {"status": "ok", "db": "ok", "version": __version__}
    healthy = True
    try:
        db.connection().execute(text("SELECT 1"))
    except Exception:
        logger.exception("Health check failed: database not reachable")
        body["db"] = "error"
        healthy = False
    services = getattr(request.app.state, "services", None)
    if services is not None:
        body["scheduler"] = services.scheduler.status()
        # A second instance on the same data directory ("not owner") is reported unhealthy.
        if not services.scheduler.healthy():
            healthy = False
    if not healthy:
        body["status"] = "error"
        return JSONResponse(body, status_code=503)
    return JSONResponse(body)


@router.get("/api/version")
def version() -> dict[str, str]:
    return {"version": __version__}
