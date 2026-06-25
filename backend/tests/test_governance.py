"""Safety gates: suppression blocks sends, transient failures retry, daily caps enforce."""

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
    Message,
    MessageDirection,
    MessageStatus,
    Organization,
    SuppressionReason,
    Workspace,
    WorkspaceKind,
)
from app.services.outreach import enrollment as enr_service
from app.services.outreach import governor
from app.services.sourcing import suppression


async def _setup(
    session: AsyncSession, slug: str
) -> tuple[Organization, Workspace, Contact, Campaign]:
    org = Organization(name="Gov", slug=slug, plan="demo")
    session.add(org)
    await session.flush()
    ws = Workspace(organization_id=org.id, name="W", kind=WorkspaceKind.team)
    session.add(ws)
    await session.flush()
    contact = Contact(
        workspace_id=ws.id,
        full_name="Pat Lee",
        email="pat@example.com",
        skills=[],
        source="manual",
        tags=[],
    )
    session.add(contact)
    campaign = Campaign(
        workspace_id=ws.id,
        name="C",
        status=CampaignStatus.active,
        autonomy_mode=AutonomyMode.approve_each,
        criteria={},
        sequence=[{"channel": "email", "delay_days": 0}, {"channel": "email", "delay_days": 3}],
    )
    session.add(campaign)
    await session.flush()
    return org, ws, contact, campaign


def _enrollment(ws: Workspace, campaign: Campaign, contact: Contact) -> Enrollment:
    return Enrollment(
        workspace_id=ws.id,
        campaign_id=campaign.id,
        contact_id=contact.id,
        state=EnrollmentState.scheduled,
        score=80,
        current_step=0,
    )


def _approved_message(ws: Workspace, enr: Enrollment) -> Message:
    return Message(
        workspace_id=ws.id,
        enrollment_id=enr.id,
        direction=MessageDirection.outbound,
        channel=Channel.email,
        status=MessageStatus.approved,
        subject="Hello",
        body="Hi there",
    )


@pytest.mark.db
async def test_suppressed_contact_is_never_sent(db_session: AsyncSession) -> None:
    org, ws, contact, campaign = await _setup(db_session, "gov-suppress")
    await suppression.suppress(
        db_session, organization_id=org.id, email=contact.email, reason=SuppressionReason.opted_out
    )
    enr = _enrollment(ws, campaign, contact)
    db_session.add(enr)
    await db_session.flush()
    msg = _approved_message(ws, enr)
    db_session.add(msg)
    await db_session.flush()

    await enr_service.tick(db_session, enrollment=enr, now=datetime.now(UTC))

    assert enr.state == EnrollmentState.opted_out
    assert msg.status != MessageStatus.sent


@pytest.mark.db
async def test_send_failure_retries_then_advances(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _org, ws, contact, campaign = await _setup(db_session, "gov-retry")

    async def boom(**_: object) -> None:
        raise RuntimeError("smtp down")

    monkeypatch.setattr(enr_service, "send_via_channel", boom)
    enr = _enrollment(ws, campaign, contact)
    db_session.add(enr)
    await db_session.flush()
    msg = _approved_message(ws, enr)
    db_session.add(msg)
    await db_session.flush()
    now = datetime.now(UTC)

    # First two attempts retry (message stays approved, enrollment stays scheduled).
    await enr_service.tick(db_session, enrollment=enr, now=now)
    assert msg.attempts == 1 and msg.status == MessageStatus.approved
    assert enr.next_run_at is not None
    assert enr.state == EnrollmentState.scheduled and enr.next_run_at > now
    await enr_service.tick(db_session, enrollment=enr, now=now)
    assert msg.attempts == 2 and enr.state == EnrollmentState.scheduled

    # Third attempt exhausts retries: mark failed and advance the sequence.
    await enr_service.tick(db_session, enrollment=enr, now=now)
    assert msg.attempts == 3 and msg.status == MessageStatus.failed
    assert enr.state == EnrollmentState.awaiting_reply and enr.current_step == 1


@pytest.mark.db
async def test_governor_enforces_daily_cap(db_session: AsyncSession) -> None:
    _org, ws, contact, campaign = await _setup(db_session, "gov-cap")
    ws.settings = {"daily_cap_email": 1}
    await db_session.flush()
    now = datetime.now(UTC)

    allowed, _ = await governor.can_send_now(
        db_session, workspace_id=ws.id, channel=Channel.email, now=now
    )
    assert allowed

    enr = _enrollment(ws, campaign, contact)
    db_session.add(enr)
    await db_session.flush()
    db_session.add(
        Message(
            workspace_id=ws.id,
            enrollment_id=enr.id,
            direction=MessageDirection.outbound,
            channel=Channel.email,
            status=MessageStatus.sent,
            sent_at=now,
            subject="s",
            body="b",
        )
    )
    await db_session.flush()

    allowed2, retry_at = await governor.can_send_now(
        db_session, workspace_id=ws.id, channel=Channel.email, now=now
    )
    assert not allowed2 and retry_at is not None
