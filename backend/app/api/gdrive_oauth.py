"""The "Connect Google Drive" flow: start, Google's redirect back to this install, status."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.api.common import invalid_field, undecryptable
from app.api.deps import CurrentUser, OptionalUser, SessionDataDep, client_ip
from app.core.db import SessionDep
from app.core.security import SessionData
from app.models import Destination
from app.schemas.destination import (
    GdriveOAuthStartIn,
    GdriveOAuthStartOut,
    GdriveOAuthStatusOut,
)
from app.services.audit import write_audit
from app.services.gdrive_oauth import (
    ERROR_MESSAGES,
    OAuthFlowError,
    OAuthFlows,
    redirect_uri_for,
)
from app.services.secrets import SecretsUnavailable, load_secrets

DONE_PATH = "/oauth/google/done"

router = APIRouter(tags=["destinations"])


def get_oauth_flows(request: Request) -> OAuthFlows:
    flows: OAuthFlows = request.app.state.gdrive_oauth
    return flows


OAuthFlowsDep = Annotated[OAuthFlows, Depends(get_oauth_flows)]


def require_sid(session_data: SessionData | None) -> str:
    if session_data is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return session_data.sid


def _stored_client_secret(db: Session, destination_id: uuid.UUID, client_id: str) -> str:
    row = db.get(Destination, destination_id)
    if row is None or row.type != "gdrive":
        raise HTTPException(status_code=404, detail="Destination not found")
    try:
        stored = load_secrets(row.secrets_enc)
    except SecretsUnavailable:
        raise undecryptable() from None
    secret = stored.get("client_secret")
    if not secret or row.config.get("client_id") != client_id:
        raise invalid_field(
            ["client_secret"], "Enter the client secret for this client ID to connect."
        )
    return secret


@router.post("/api/destinations/gdrive/oauth/start")
def start_flow(
    body: GdriveOAuthStartIn,
    request: Request,
    db: SessionDep,
    user: CurrentUser,
    session_data: SessionDataDep,
    flows: OAuthFlowsDep,
) -> GdriveOAuthStartOut:
    sid = require_sid(session_data)
    try:
        redirect_uri = redirect_uri_for(request.headers.get("origin"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if body.client_secret is not None:
        client_secret = body.client_secret.get_secret_value()
    elif body.destination_id is not None:
        client_secret = _stored_client_secret(db, body.destination_id, body.client_id)
    else:
        raise invalid_field(["client_secret"], "Enter the OAuth client secret to connect.")
    flow, url = flows.start(
        client_id=body.client_id,
        client_secret=client_secret,
        scope=body.scope,
        redirect_uri=redirect_uri,
        sid=sid,
    )
    write_audit(
        db,
        "destination.oauth_start",
        user=user,
        object_type="destination",
        object_id=str(body.destination_id) if body.destination_id else None,
        details={"scope": body.scope},
        ip=client_ip(request),
    )
    db.commit()
    return GdriveOAuthStartOut(flow_id=flow.id, authorize_url=url)


@router.get("/api/destinations/gdrive/oauth/{flow_id}")
def flow_status(
    flow_id: str, _user: CurrentUser, session_data: SessionDataDep, flows: OAuthFlowsDep
) -> GdriveOAuthStatusOut:
    flow = flows.get(flow_id, require_sid(session_data))
    if flow is None:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES["expired"])
    message = ERROR_MESSAGES.get(flow.error) if flow.error else None
    return GdriveOAuthStatusOut(status=flow.status, error=flow.error, message=message)


def _done(flow_id: str | None, error: str | None = None) -> RedirectResponse:
    query = []
    if flow_id:
        query.append(f"flow={flow_id}")
    if error:
        query.append(f"error={error}")
    target = DONE_PATH + ("?" + "&".join(query) if query else "")
    return RedirectResponse(target, status_code=303)


@router.get("/api/oauth/google/callback", include_in_schema=False)
async def oauth_callback(
    user: OptionalUser,
    session_data: SessionDataDep,
    flows: OAuthFlowsDep,
    state: str = "",
    code: str = "",
    error: str = "",
) -> RedirectResponse:
    """Google redirects here after the consent screen. Answers with a redirect to the UI page that
    reports the result; the code and token never reach the browser beyond this request."""
    if user is None or session_data is None:
        return _done(None, "not_signed_in")
    try:
        flow = flows.claim(state, session_data.sid)
    except OAuthFlowError as exc:
        return _done(None, exc.code)
    if error or not code:
        failure = "access_denied" if error == "access_denied" else "google_error"
        flows.fail(flow, failure)
        return _done(flow.id, failure)
    try:
        await flows.complete(flow, code)
    except OAuthFlowError as exc:
        return _done(flow.id, exc.code)
    return _done(flow.id)
