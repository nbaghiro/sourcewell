"""Safety gates: suppression blocks sends, transient failures retry, daily caps enforce."""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AutonomyLevel,
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
from app.services.outreach import messaging as msg_service
from app.services.outreach.messaging import TransientSendError
from app.services.sourcing import suppression
from tests.factories import make_org, make_workspace


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
        autonomy_level=AutonomyLevel.assisted,
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

    async def boom(*_a: object, **_k: object) -> None:
        raise TransientSendError("smtp down")

    monkeypatch.setattr(enr_service, "deliver_outbound", boom)
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
        db_session, campaign=campaign, channel=Channel.email, now=now
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
        db_session, campaign=campaign, channel=Channel.email, now=now
    )
    assert not allowed2 and retry_at is not None


@pytest.mark.db
async def test_org_wide_suppression_covers_every_workspace(db_session: AsyncSession) -> None:
    org, ws, contact, _campaign = await _setup(db_session, "supp-org")
    other = Workspace(organization_id=org.id, name="Other", kind=WorkspaceKind.team)
    db_session.add(other)
    await db_session.flush()
    await suppression.suppress(db_session, organization_id=org.id, email=contact.email)

    for workspace_id in (ws.id, other.id):
        assert await suppression.is_suppressed(
            db_session, organization_id=org.id, email=contact.email, workspace_id=workspace_id
        )


@pytest.mark.db
async def test_workspace_suppression_stays_in_its_workspace(db_session: AsyncSession) -> None:
    org, ws, contact, _campaign = await _setup(db_session, "supp-ws")
    other = Workspace(organization_id=org.id, name="Other", kind=WorkspaceKind.team)
    db_session.add(other)
    await db_session.flush()
    await suppression.suppress(
        db_session, organization_id=org.id, email=contact.email, workspace_id=ws.id
    )

    assert await suppression.is_suppressed(
        db_session, organization_id=org.id, email=contact.email, workspace_id=ws.id
    )
    assert not await suppression.is_suppressed(
        db_session, organization_id=org.id, email=contact.email, workspace_id=other.id
    )
    # An org-wide entry for the same address coexists with the workspace one.
    org_wide = await suppression.suppress(db_session, organization_id=org.id, email=contact.email)
    assert org_wide is not None and org_wide.workspace_id is None
    assert await suppression.is_suppressed(
        db_session, organization_id=org.id, email=contact.email, workspace_id=other.id
    )

    # Removing un-suppresses the address everywhere in the org.
    assert contact.email is not None
    assert await suppression.remove(db_session, organization_id=org.id, email=contact.email)
    assert not await suppression.is_suppressed(
        db_session, organization_id=org.id, email=contact.email, workspace_id=ws.id
    )


# --- only a real decline counts as an opt-out ----------------------------------


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        # The one that cost real candidates: "stop" was matched as a substring.
        ("I'll stop by Thursday if that works", "neutral"),
        ("stopped by your careers page — looks great", "neutral"),
        ("Sounds good, let's talk", "interested"),
        ("What's the salary range?", "neutral"),
        # ...and the genuine opt-outs still land.
        ("STOP", "opted_out"),
        ("stop.", "opted_out"),
        ("Not interested, thanks", "opted_out"),
        ("please remove me from your list", "opted_out"),
        ("how do I unsubscribe?", "opted_out"),
    ],
)
def test_only_a_real_decline_counts_as_an_opt_out(reply: str, expected: str) -> None:
    """An opt-out permanently suppresses the address and the recruiter can't undo it from the
    thread, so a false positive silently costs a candidate."""
    assert msg_service.classify_reply(reply) == expected


@pytest.mark.db
async def test_clicking_unsubscribe_also_ends_the_conversation(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    """The clearest signal a candidate can send used to produce the *weakest* result: the address
    was suppressed, but the thread still read "Awaiting reply" and the next touchpoint was still
    scheduled — only to be refused at send time, failing the enrollment instead of ending it."""
    org = await make_org(db_session, slug="opt-unsub")
    ws = await make_workspace(db_session, org=org)
    campaign = Campaign(workspace_id=ws.id, name="C", criteria={}, sequence=[])
    contact = Contact(
        workspace_id=ws.id, full_name="Lee", email="lee@example.com", skills=[], tags=[]
    )
    db_session.add_all([campaign, contact])
    await db_session.flush()
    enr = Enrollment(
        workspace_id=ws.id,
        campaign_id=campaign.id,
        contact_id=contact.id,
        state=EnrollmentState.awaiting_reply,
        next_run_at=datetime.now(UTC),
    )
    db_session.add(enr)
    await db_session.flush()

    token = suppression.unsubscribe_token(org.id, "lee@example.com")
    r = await db_client.get(f"/unsubscribe?token={token}")
    assert r.status_code == 200

    await db_session.refresh(enr)
    assert enr.state == EnrollmentState.opted_out
    assert enr.outcome == "opted_out"
    assert enr.next_run_at is None  # nothing further is even attempted
    # The unsubscribe token names an organization, not a workspace, so the entry is org-wide.
    assert await suppression.is_suppressed(
        db_session, organization_id=org.id, email="lee@example.com", workspace_id=ws.id
    )


@pytest.mark.db
async def test_unsubscribing_leaves_other_tenants_alone(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    """The token names one organization; the same person may be talking to another customer."""
    theirs = await make_org(db_session, slug="opt-theirs")
    ours = await make_org(db_session, slug="opt-ours")
    other_ws = await make_workspace(db_session, org=theirs)
    campaign = Campaign(workspace_id=other_ws.id, name="C", criteria={}, sequence=[])
    contact = Contact(
        workspace_id=other_ws.id, full_name="Lee", email="lee@example.com", skills=[], tags=[]
    )
    db_session.add_all([campaign, contact])
    await db_session.flush()
    enr = Enrollment(
        workspace_id=other_ws.id,
        campaign_id=campaign.id,
        contact_id=contact.id,
        state=EnrollmentState.awaiting_reply,
    )
    db_session.add(enr)
    await db_session.flush()

    token = suppression.unsubscribe_token(ours.id, "lee@example.com")
    assert (await db_client.get(f"/unsubscribe?token={token}")).status_code == 200

    await db_session.refresh(enr)
    assert enr.state == EnrollmentState.awaiting_reply  # untouched
