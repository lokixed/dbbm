import logging
import threading

from fastapi import APIRouter, HTTPException, Request, Response
from sqlmodel import Session, col, select

from app.api.deps import CurrentUser, OptionalUser, SessionDataDep, SettingsDep, client_ip
from app.core.db import SessionDep
from app.core.security import (
    burn_password_check,
    clear_session_cookies,
    create_session_token,
    ensure_csrf_cookie,
    get_rate_limiter,
    hash_password,
    needs_rehash,
    set_session_cookies,
    verify_password,
)
from app.models import User
from app.schemas.auth import AuthStatus, LoginRequest, SetupRequest, UserOut
from app.services.audit import write_audit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

# One worker process only (DESIGN.md section 2), so an in-process lock closes the setup race.
_setup_lock = threading.Lock()


def _has_users(db: Session) -> bool:
    return db.exec(select(User.id).limit(1)).first() is not None


def _user_out(user: User) -> UserOut:
    return UserOut(id=str(user.id), username=user.username)


@router.get("/status")
def auth_status(
    request: Request,
    response: Response,
    settings: SettingsDep,
    session_data: SessionDataDep,
    db: SessionDep,
) -> AuthStatus:
    ensure_csrf_cookie(request, response, settings, session_data)
    return AuthStatus(setup_required=not _has_users(db))


@router.post("/setup", status_code=201)
def setup(body: SetupRequest, request: Request, db: SessionDep) -> UserOut:
    with _setup_lock:
        if _has_users(db):
            raise HTTPException(status_code=409, detail="Setup has already been completed")
        user = User(
            username=body.username, password_hash=hash_password(body.password.get_secret_value())
        )
        db.add(user)
        db.flush()  # no ORM relationship, so the user must be inserted before the audit row
        write_audit(db, "auth.setup", user=user, ip=client_ip(request))
        db.commit()
    logger.info("Initial admin account created")
    return _user_out(user)


@router.post("/login")
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    settings: SettingsDep,
    db: SessionDep,
) -> UserOut:
    ip = client_ip(request)
    limiter = get_rate_limiter()

    retry_after = limiter.retry_after(ip, body.username)
    if retry_after:
        write_audit(
            db,
            "auth.login.failure",
            username=body.username,
            details={"reason": "rate_limited"},
            ip=ip,
        )
        db.commit()
        logger.warning("Login blocked by rate limit")
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    password = body.password.get_secret_value()
    user = db.exec(select(User).where(col(User.username) == body.username)).first()
    if user is None:
        burn_password_check(password)
    if user is None or not verify_password(user.password_hash, password):
        limiter.record_failure(ip, body.username)
        write_audit(
            db,
            "auth.login.failure",
            user=user,
            username=body.username,
            details={"reason": "bad_credentials"},
            ip=ip,
        )
        db.commit()
        logger.info("Login failed")
        raise HTTPException(status_code=401, detail="Invalid username or password")

    limiter.record_success(ip, body.username)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        db.add(user)
    token, data = create_session_token(settings, user.id, user.session_version)
    set_session_cookies(response, settings, token, data)
    write_audit(db, "auth.login.success", user=user, ip=ip)
    db.commit()
    logger.info("Login succeeded")
    return _user_out(user)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    settings: SettingsDep,
    db: SessionDep,
    user: OptionalUser,
) -> Response:
    response = Response(status_code=204)
    clear_session_cookies(response, settings)
    if user is not None:
        write_audit(db, "auth.logout", user=user, ip=client_ip(request))
        db.commit()
    return response


@router.get("/me")
def me(
    request: Request,
    response: Response,
    settings: SettingsDep,
    session_data: SessionDataDep,
    user: CurrentUser,
) -> UserOut:
    ensure_csrf_cookie(request, response, settings, session_data)
    return _user_out(user)
