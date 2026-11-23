import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import CurrentUser, ServicesDep, client_ip
from app.schemas.retention import (
    RetentionCopyOut,
    RetentionFailureOut,
    RetentionPreviewOut,
    RetentionRunOut,
)
from app.services.retention import Plan, Policy, RunCopy

router = APIRouter(prefix="/api/destinations", tags=["retention"])


def _copy_out(copy: RunCopy) -> RetentionCopyOut:
    return RetentionCopyOut(
        run_id=str(copy.run_id),
        job_name=copy.job_name,
        status=copy.status,
        started_at=copy.moment,
        counted=copy.counted,
        protected=copy.protected,
        artifacts=[a.path for a in copy.artifacts],
        bytes=copy.size,
    )


def plan_out(plan: Plan) -> RetentionPreviewOut:
    return RetentionPreviewOut(
        keep_last=plan.policy.keep_last,
        keep_days=plan.policy.keep_days,
        delete=[_copy_out(c) for c in plan.delete],
        keep=[_copy_out(c) for c in plan.keep],
        delete_runs=len(plan.delete),
        delete_artifacts=sum(len(c.artifacts) for c in plan.delete),
        delete_bytes=sum(c.size for c in plan.delete),
    )


@router.get("/{destination_id}/retention/preview")
async def preview_retention(
    destination_id: uuid.UUID,
    _user: CurrentUser,
    services: ServicesDep,
    keep_last: Annotated[int | None, Query(ge=1, le=100_000)] = None,
    keep_days: Annotated[int | None, Query(ge=1, le=36_500)] = None,
) -> RetentionPreviewOut:
    """What retention would delete, using the given values or else the saved policy."""
    policy = Policy(keep_last, keep_days) if keep_last or keep_days else None
    try:
        plan = await services.retention.preview(destination_id, policy)
    except LookupError:
        raise HTTPException(status_code=404, detail="Destination not found") from None
    return plan_out(plan)


@router.post("/{destination_id}/retention/run")
async def run_retention(
    destination_id: uuid.UUID, request: Request, user: CurrentUser, services: ServicesDep
) -> RetentionRunOut:
    try:
        result = await services.retention.run(
            destination_id, trigger="manual", user=user, ip=client_ip(request)
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Destination not found") from None
    return RetentionRunOut(
        destination_id=str(result.destination_id),
        deleted_runs=result.deleted_runs,
        deleted_artifacts=result.deleted_artifacts,
        deleted_bytes=result.deleted_bytes,
        failures=[RetentionFailureOut(**f) for f in result.failures],
        skipped=result.skipped,
    )
