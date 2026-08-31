"""Suppression list HTTP layer: admin CRUD endpoints + the public signed unsubscribe link."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.context import ContextDep, SessionDep
from app.api.guards import require_org_admin, require_workspace
from app.core.db import get_session
from app.models import Suppression, SuppressionReason
from app.services.insights import audit
from app.services.outreach.enrollment import close_for_opt_out
from app.services.sourcing.suppression import (
    list_for_org,
    parse_unsubscribe,
    remove,
    suppress,
)

router = APIRouter(tags=["suppression"])


# --- Schemas -----------------------------------------------------------------


class SuppressionOut(BaseModel):
    id: str
    email: str
    reason: SuppressionReason
    note: str | None
    workspace_id: str | None  # null = org-wide
    created_at: str | None


class SuppressionIn(BaseModel):
    email: str
    reason: SuppressionReason = SuppressionReason.manual
    note: str | None = None
    # Scope the entry to the request's workspace instead of the whole organization.
    workspace_only: bool = False


class RemovedOut(BaseModel):
    status: str
    email: str


def _dump(s: Suppression) -> SuppressionOut:
    return SuppressionOut(
        id=s.id,
        email=s.email,
        reason=s.reason,
        note=s.note,
        workspace_id=s.workspace_id,
        created_at=s.created_at.isoformat() if s.created_at else None,
    )


# --- Endpoints ---------------------------------------------------------------


@router.get("/suppressions", response_model=list[SuppressionOut])
async def list_suppressions(ctx: ContextDep, session: SessionDep) -> list[SuppressionOut]:
    return [_dump(s) for s in await list_for_org(session, ctx.org_id)]


@router.post("/suppressions", response_model=SuppressionOut)
async def add_suppression(
    body: SuppressionIn, ctx: ContextDep, session: SessionDep
) -> SuppressionOut:
    require_org_admin(ctx)
    row = await suppress(
        session,
        organization_id=ctx.org_id,
        email=body.email,
        reason=body.reason,
        note=body.note,
        workspace_id=require_workspace(ctx) if body.workspace_only else None,
    )
    if row is None:
        raise HTTPException(status_code=400, detail="a valid email is required")
    await audit.record(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.current_workspace_id,
        actor_user_id=ctx.user_id,
        action="suppression.added",
        summary=f"Suppressed {row.email}",
        target_type="suppression",
        target_id=row.id,
    )
    return _dump(row)


@router.delete("/suppressions/{email}", response_model=RemovedOut)
async def remove_suppression(email: str, ctx: ContextDep, session: SessionDep) -> RemovedOut:
    require_org_admin(ctx)
    await remove(session, organization_id=ctx.org_id, email=email)
    await audit.record(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.current_workspace_id,
        actor_user_id=ctx.user_id,
        action="suppression.removed",
        summary=f"Un-suppressed {email}",
    )
    return RemovedOut(status="removed", email=email)


@router.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe(
    token: str, session: Annotated[AsyncSession, Depends(get_session)]
) -> HTMLResponse:
    """Public, signed unsubscribe link target (no auth)."""
    parsed = parse_unsubscribe(token)
    if parsed is None:
        raise HTTPException(status_code=400, detail="invalid or expired link")
    org_id, email = parsed
    await suppress(
        session, organization_id=org_id, email=email, reason=SuppressionReason.unsubscribed
    )
    # Suppressing the address blocks future sends; this ends the conversations already open with
    # them, so the recruiter sees "Opted out" rather than a thread that still reads as waiting on
    # a reply — and so the next touchpoint stops being scheduled at all rather than being
    # attempted and refused.
    await close_for_opt_out(session, organization_id=org_id, email=email, now=datetime.now(UTC))
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'><title>Unsubscribed</title>"
        "<style>body{font-family:system-ui;background:#f3f1ea;color:#122019;display:grid;"
        "place-items:center;height:100vh;margin:0}div{text-align:center;max-width:30rem}</style>"
        "</head><body><div><h1>You're unsubscribed</h1>"
        f"<p>{email} won't receive further messages from this sender.</p></div></body></html>"
    )
