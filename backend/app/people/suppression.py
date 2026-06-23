"""Suppression list (org do-not-contact): logic, signed unsubscribe tokens, and endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.signing import sign, verify
from app.insights import audit
from app.models import Suppression, SuppressionReason
from app.workspace.tenancy import ContextDep, SessionDep, require_org_admin

router = APIRouter(tags=["suppression"])


# --- Service -----------------------------------------------------------------


def normalize(email: str | None) -> str:
    return (email or "").strip().lower()


async def is_suppressed(session: AsyncSession, *, organization_id: str, email: str | None) -> bool:
    e = normalize(email)
    if not e:
        return False
    row = (
        await session.execute(
            select(Suppression).where(
                Suppression.organization_id == organization_id, Suppression.email == e
            )
        )
    ).scalar_one_or_none()
    return row is not None


async def suppress(
    session: AsyncSession,
    *,
    organization_id: str,
    email: str | None,
    reason: SuppressionReason = SuppressionReason.manual,
    contact_id: str | None = None,
    note: str | None = None,
) -> Suppression | None:
    """Add an email to the org's do-not-contact list (idempotent)."""
    e = normalize(email)
    if not e:
        return None
    existing = (
        await session.execute(
            select(Suppression).where(
                Suppression.organization_id == organization_id, Suppression.email == e
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = Suppression(
        organization_id=organization_id, email=e, reason=reason, contact_id=contact_id, note=note
    )
    session.add(row)
    await session.flush()
    return row


async def list_for_org(session: AsyncSession, organization_id: str) -> list[Suppression]:
    rows = await session.execute(
        select(Suppression)
        .where(Suppression.organization_id == organization_id)
        .order_by(Suppression.created_at.desc())
    )
    return list(rows.scalars().all())


async def remove(session: AsyncSession, *, organization_id: str, email: str) -> bool:
    row = (
        await session.execute(
            select(Suppression).where(
                Suppression.organization_id == organization_id,
                Suppression.email == normalize(email),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True


def unsubscribe_token(organization_id: str, email: str) -> str:
    return sign(f"{organization_id}|{normalize(email)}")


def unsubscribe_url(organization_id: str, email: str) -> str:
    base = get_settings().api_base_url.rstrip("/")
    return f"{base}/unsubscribe?token={unsubscribe_token(organization_id, email)}"


def parse_unsubscribe(token: str) -> tuple[str, str] | None:
    payload = verify(token)
    if not payload or "|" not in payload:
        return None
    org_id, email = payload.split("|", 1)
    return org_id, email


# --- Schemas -----------------------------------------------------------------


class SuppressionOut(BaseModel):
    id: str
    email: str
    reason: str
    note: str | None
    created_at: str | None


class SuppressionIn(BaseModel):
    email: str
    reason: SuppressionReason = SuppressionReason.manual
    note: str | None = None


class RemovedOut(BaseModel):
    status: str
    email: str


def _dump(s: Suppression) -> SuppressionOut:
    return SuppressionOut(
        id=s.id,
        email=s.email,
        reason=s.reason.value,
        note=s.note,
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
        session, organization_id=ctx.org_id, email=body.email, reason=body.reason, note=body.note
    )
    if row is None:
        raise HTTPException(status_code=400, detail="a valid email is required")
    await audit.record(
        session,
        ctx,
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
    await audit.record(session, ctx, action="suppression.removed", summary=f"Un-suppressed {email}")
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
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'><title>Unsubscribed</title>"
        "<style>body{font-family:system-ui;background:#f3f1ea;color:#122019;display:grid;"
        "place-items:center;height:100vh;margin:0}div{text-align:center;max-width:30rem}</style>"
        "</head><body><div><h1>You're unsubscribed</h1>"
        f"<p>{email} won't receive further messages from this sender.</p></div></body></html>"
    )
