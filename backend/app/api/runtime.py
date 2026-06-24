"""Admin/QA controls: drive the autonomous engine by hand (no waiting on the worker)."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.deps import ContextDep, SessionDep, require_org_admin
from app.models import Enrollment
from app.worker import run_due

router = APIRouter(prefix="/admin", tags=["admin"])


class FastForwardOut(BaseModel):
    id: str
    next_run_at: str


@router.post("/run-due")
async def run_due_now(ctx: ContextDep, session: SessionDep) -> dict[str, int]:
    """Process every enrollment whose next_run_at is due, once. Call repeatedly to step."""
    require_org_admin(ctx)
    return await run_due(session, now=datetime.now(UTC))


@router.post("/enrollments/{enrollment_id}/fast-forward", response_model=FastForwardOut)
async def fast_forward(enrollment_id: str, ctx: ContextDep, session: SessionDep) -> FastForwardOut:
    """Pull a future-scheduled touchpoint into the present so run-due picks it up now."""
    require_org_admin(ctx)
    enrollment = await session.get(Enrollment, enrollment_id)
    if enrollment is None or enrollment.workspace_id not in ctx.allowed_workspace_ids:
        raise HTTPException(status_code=404, detail="enrollment not found")
    enrollment.next_run_at = datetime.now(UTC)
    await session.flush()
    return FastForwardOut(id=enrollment.id, next_run_at=enrollment.next_run_at.isoformat())
