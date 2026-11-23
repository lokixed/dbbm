from anyio import from_thread
from fastapi import APIRouter, Request

from app.api.deps import CurrentUser, ServicesDep, client_ip
from app.core.db import SessionDep
from app.schemas.settings import AppSettingsIn, AppSettingsOut
from app.services.app_settings import read_settings, write_settings
from app.services.audit import write_audit

router = APIRouter(prefix="/api/settings", tags=["settings"])

SCHEDULER_KEYS = frozenset({"housekeeping_time", "default_timezone", "misfire_grace_seconds"})


@router.get("")
def get_settings_api(db: SessionDep, _user: CurrentUser) -> AppSettingsOut:
    return read_settings(db)


@router.put("")
def put_settings(
    body: AppSettingsIn,
    request: Request,
    db: SessionDep,
    user: CurrentUser,
    services: ServicesDep,
) -> AppSettingsOut:
    changed = write_settings(db, body)
    values = body.model_dump()
    write_audit(
        db,
        "settings.update",
        user=user,
        object_type="settings",
        details={"changed": {key: values[key] for key in changed}},
        ip=client_ip(request),
    )
    db.commit()
    if SCHEDULER_KEYS.intersection(changed):
        from_thread.run(services.scheduler.apply_settings)
    return read_settings(db)
