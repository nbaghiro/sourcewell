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


_PAGE_STYLE = (
    "body{font-family:system-ui;background:#f3f1ea;color:#122019;display:grid;"
    "place-items:center;height:100vh;margin:0}div{text-align:center;max-width:30rem}"
    "button{font:inherit;background:#122019;color:#f3f1ea;border:0;border-radius:4px;"
    "padding:.7rem 1.4rem;cursor:pointer;margin-top:1rem}"
)


def _page(title: str, heading: str, body_html: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title>"
        f"<style>{_PAGE_STYLE}</style></head><body><div><h1>{heading}</h1>"
        f"{body_html}</div></body></html>"
    )


async def _apply_unsubscribe(session: AsyncSession, token: str) -> str:
    """Suppress the address a signed token names and close its live threads; returns the address."""
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
    return email


@router.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_page(token: str) -> HTMLResponse:
    """The confirmation page a recipient lands on. Reading it changes nothing.

    This used to opt the recipient out on load. A GET that mutates is fine until something
    fetches it without being asked — and mail security does exactly that: Outlook SafeLinks,
    Gmail's proxy and every link scanner in between follow the URLs in a message to check them.
    Candidates were being unsubscribed by a scanner they never saw, from mail they may never have
    opened. Now the opt-out needs the POST below, which only a person clicking can send.
    """
    if parse_unsubscribe(token) is None:
        raise HTTPException(status_code=400, detail="invalid or expired link")
    return _page(
        "Unsubscribe",
        "Unsubscribe?",
        "<p>You won't receive further messages from this sender.</p>"
        f'<form method="post" action="/unsubscribe?token={token}">'
        '<button type="submit">Unsubscribe me</button></form>',
    )


@router.post("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe(
    token: str, session: Annotated[AsyncSession, Depends(get_session)]
) -> HTMLResponse:
    """Public, signed unsubscribe target (no auth) — the confirmation form, and one-click.

    Outbound mail carries `List-Unsubscribe-Post: List-Unsubscribe=One-Click` (RFC 8058), which
    tells the recipient's mail client it may POST this URL directly. Until this route existed that
    header was a promise nothing kept: the client's POST got a 405 and the unsubscribe button in
    Gmail and Outlook simply failed.
    """
    email = await _apply_unsubscribe(session, token)
    return _page(
        "Unsubscribed",
        "You're unsubscribed",
        f"<p>{email} won't receive further messages from this sender.</p>",
    )
