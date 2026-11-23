from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlmodel import col, select

from app.core.config import Settings, get_settings
from app.core.db import SessionDep
from app.core.security import (
    SAFE_METHODS,
    SESSION_COOKIE,
    SessionData,
    read_session,
    validate_csrf,
)
from app.models import User
from app.services.container import Services

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_session_data(request: Request, settings: SettingsDep) -> SessionData | None:
    return read_session(settings, request.cookies.get(SESSION_COOKIE))


SessionDataDep = Annotated[SessionData | None, Depends(get_session_data)]


def csrf_protect(request: Request, settings: SettingsDep, session_data: SessionDataDep) -> None:
    if request.method in SAFE_METHODS:
        return
    if not validate_csrf(request, settings, session_data):
        raise HTTPException(status_code=403, detail="csrf_failed")


def optional_user(session_data: SessionDataDep, db: SessionDep) -> User | None:
    if session_data is None:
        return None
    user = db.exec(select(User).where(col(User.id) == session_data.user_id)).first()
    if user is None or user.session_version != session_data.session_version:
        return None
    return user


def current_user(user: Annotated[User | None, Depends(optional_user)]) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


CurrentUser = Annotated[User, Depends(current_user)]
OptionalUser = Annotated[User | None, Depends(optional_user)]
ServicesDep = Annotated[Services, Depends(get_services)]
