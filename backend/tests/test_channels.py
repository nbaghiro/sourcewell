"""Multichannel send dispatch (LinkedIn dry-run) + email-threaded inbound ingestion."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AutonomyMode,
    Campaign,
    CampaignStatus,
    Channel,
    Contact,
    Enrollment,
    EnrollmentState,
    MessageDirection,
    MessageStatus,
    Organization,
    Workspace,
    WorkspaceKind,
)
from app.services.outreach import enrollment as enr_service
from app.services.outreach import messaging as msg_service


async def _ws_contact(session: AsyncSession, slug: str) -> tuple[Organization, Workspace, Contact]:
    org = Organization(name="Ch", slug=slug, plan="demo")
    session.add(org)
    await session.flush()
    ws = Workspace(organization_id=org.id, name="W", kind=WorkspaceKind.team)
    session.add(ws)
    await session.flush()
    contact = Contact(
        workspace_id=ws.id,
        full_name="Lee Park",
        email="lee@example.com",
        linkedin_url="https://linkedin.com/in/leepark",
        skills=[],
        source="manual",
        tags=[],
    )
    session.add(contact)
    await session.flush()
    return org, ws, contact


@pytest.mark.db
async def test_linkedin_step_sends_dry_run_and_advances(db_session: AsyncSession) -> None:
    _org, ws, contact = await _ws_contact(db_session, "ch-li")
    campaign = Campaign(
        workspace_id=ws.id,
        name="C",
        status=CampaignStatus.active,
        autonomy_mode=AutonomyMode.auto,
        criteria={},
        sequence=[{"channel": "linkedin", "delay_days": 0}],
    )
    db_session.add(campaign)
    await db_session.flush()
    now = datetime.now(UTC)
    enr = Enrollment(
        workspace_id=ws.id,
        campaign_id=campaign.id,
        contact_id=contact.id,
        state=EnrollmentState.active,
        score=80,
        current_step=0,
        next_run_at=now,
    )
    db_session.add(enr)
    await db_session.flush()

    await enr_service.tick(db_session, enrollment=enr, now=now)  # active -> scheduled (auto)
    assert enr.state == EnrollmentState.scheduled
    await enr_service.tick(
        db_session, enrollment=enr, now=now
    )  # scheduled -> send (linkedin no-op)
    assert enr.state == EnrollmentState.awaiting_reply

    msgs = await msg_service.list_thread(db_session, workspace_id=ws.id, enrollment_id=enr.id)
    sent = [m for m in msgs if m.direction == MessageDirection.outbound]
    assert sent and sent[0].channel == Channel.linkedin and sent[0].status == MessageStatus.sent


@pytest.mark.db
async def test_inbound_webhook_threads_by_sender_email(db_session: AsyncSession) -> None:
    _org, ws, contact = await _ws_contact(db_session, "ch-inbound")
    campaign = Campaign(
        workspace_id=ws.id,
        name="C",
        status=CampaignStatus.active,
        autonomy_mode=AutonomyMode.approve_each,
        criteria={},
        sequence=[{"channel": "email", "delay_days": 0}],
    )
    db_session.add(campaign)
    await db_session.flush()
    enr = Enrollment(
        workspace_id=ws.id,
        campaign_id=campaign.id,
        contact_id=contact.id,
        state=EnrollmentState.awaiting_reply,
        score=80,
        current_step=1,
    )
    db_session.add(enr)
    await db_session.flush()

    result = await msg_service.ingest_inbound(
        db_session,
        from_email="LEE@example.com",  # case-insensitive match
        text="Yes, I'm interested — tell me more!",
        now=datetime.now(UTC),
    )
    assert result is not None
    message, intent = result
    assert intent == "interested"
    assert message.enrollment_id == enr.id
    assert enr.state == EnrollmentState.handed_off
