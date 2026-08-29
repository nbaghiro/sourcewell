"""Sending a reply consumes the AI-suggested draft and records who authored the message."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.context import TenantContext
from app.api.messaging import SendRequest, send_reply
from app.models import (
    Campaign,
    Channel,
    Contact,
    Enrollment,
    EnrollmentState,
    MembershipRole,
    Message,
    MessageDirection,
    MessageStatus,
)
from tests.factories import make_org, make_workspace


async def _thread_with_draft(session: AsyncSession) -> tuple[TenantContext, str]:
    org = await make_org(session, slug="reply")
    ws = await make_workspace(session, org=org)
    camp = Campaign(workspace_id=ws.id, name="C", criteria={}, sequence=[])
    contact = Contact(
        workspace_id=ws.id, full_name="Ada", email="ada@example.com", skills=[], tags=[]
    )
    session.add_all([camp, contact])
    await session.flush()
    enr = Enrollment(
        workspace_id=ws.id,
        campaign_id=camp.id,
        contact_id=contact.id,
        state=EnrollmentState.scheduled,
        score=50,
    )
    session.add(enr)
    await session.flush()
    session.add(
        Message(
            workspace_id=ws.id,
            enrollment_id=enr.id,
            direction=MessageDirection.outbound,
            channel=Channel.email,
            status=MessageStatus.draft,
            body="AI-suggested draft",
        )
    )
    await session.flush()
    ctx = TenantContext(
        user_id="u1",
        org_id=org.id,
        roles=frozenset({MembershipRole.org_admin}),
        is_org_admin=True,
        allowed_workspace_ids=frozenset({ws.id}),
        current_workspace_id=ws.id,
    )
    return ctx, enr.id


async def _drafts(session: AsyncSession, enr_id: str) -> int:
    rows = (
        (
            await session.execute(
                select(Message).where(
                    Message.enrollment_id == enr_id, Message.status == MessageStatus.draft
                )
            )
        )
        .scalars()
        .all()
    )
    return len(rows)


@pytest.mark.db
async def test_reply_consumes_draft_and_flags_ai(db_session: AsyncSession) -> None:
    ctx, enr_id = await _thread_with_draft(db_session)
    assert await _drafts(db_session, enr_id) == 1

    out = await send_reply(
        enr_id, SendRequest(text="Sending times over.", origin="ai"), ctx, db_session
    )

    assert out.status == "sent"
    assert out.origin == "ai"  # "send the suggestion as-is" path
    assert await _drafts(db_session, enr_id) == 0  # the lingering draft is consumed


@pytest.mark.db
async def test_reply_defaults_to_human_authored(db_session: AsyncSession) -> None:
    ctx, enr_id = await _thread_with_draft(db_session)
    out = await send_reply(enr_id, SendRequest(text="Typed by hand."), ctx, db_session)
    assert out.origin == "human"  # composer default
    assert await _drafts(db_session, enr_id) == 0
