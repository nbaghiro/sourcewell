"""Account-level usage credits — pooled monthly metering across the org's workspaces."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Campaign,
    Channel,
    Contact,
    Enrollment,
    EnrollmentState,
    Message,
    MessageDirection,
    MessageStatus,
)
from app.services.billing.credits import (
    CREDIT_WEIGHTS,
    CreditStatus,
    credit_status,
    monthly_allowance,
    period_start,
)
from tests.factories import make_org, make_workspace


def _sent(
    ws_id: str, enr_id: str, channel: Channel, when: datetime, *, inmail: bool = False
) -> Message:
    return Message(
        workspace_id=ws_id,
        enrollment_id=enr_id,
        direction=MessageDirection.outbound,
        channel=channel,
        status=MessageStatus.sent,
        sent_at=when,
        is_inmail=inmail,
    )


@pytest.mark.db
async def test_credit_status_pools_sends_and_sourcing(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="credits")
    ws = await make_workspace(db_session, org=org)
    camp = Campaign(workspace_id=ws.id, name="C", criteria={}, sequence=[])
    contacts = [
        Contact(workspace_id=ws.id, full_name=f"C{i}", skills=[], tags=[]) for i in range(5)
    ]
    db_session.add_all([camp, *contacts])
    await db_session.flush()

    now = datetime.now(UTC)
    start = period_start(now)
    in_period = start + timedelta(days=1)
    last_period = start - timedelta(days=1)

    # 4 enrollments sourced this period + 1 from before (excluded).
    enrs = [
        Enrollment(
            workspace_id=ws.id,
            campaign_id=camp.id,
            contact_id=contacts[i].id,
            state=EnrollmentState.proposed,
            score=50,
            created_at=in_period if i < 4 else last_period,
        )
        for i in range(5)
    ]
    db_session.add_all(enrs)
    await db_session.flush()
    e = enrs[0].id

    db_session.add_all(
        [
            _sent(ws.id, e, Channel.email, in_period),
            _sent(ws.id, e, Channel.email, in_period),
            _sent(ws.id, e, Channel.email, in_period),
            _sent(ws.id, e, Channel.linkedin, in_period),  # a DM — the cheap LinkedIn send
            _sent(ws.id, e, Channel.linkedin, in_period, inmail=True),
            _sent(ws.id, e, Channel.email, last_period),  # before the period — excluded
            Message(  # a draft — never sent, excluded
                workspace_id=ws.id,
                enrollment_id=e,
                direction=MessageDirection.outbound,
                channel=Channel.email,
                status=MessageStatus.draft,
            ),
        ]
    )
    await db_session.flush()

    st = await credit_status(db_session, organization_id=org.id, plan=org.plan, now=now)
    # A LinkedIn DM is not an InMail: only the send that actually went out as one costs double.
    assert (st.emails, st.linkedin_dms, st.inmails, st.sourced) == (3, 1, 1, 4)
    assert st.used == (
        3 * CREDIT_WEIGHTS["email"]
        + 1 * CREDIT_WEIGHTS["linkedin"]
        + 1 * CREDIT_WEIGHTS["inmail"]
        + 4 * CREDIT_WEIGHTS["sourced"]
    )
    assert st.allowance == monthly_allowance(org.plan)


def test_credit_status_over_and_pct() -> None:
    now = datetime.now(UTC)
    over = CreditStatus(
        emails=0, linkedin_dms=0, inmails=0, sourced=0, used=250, allowance=200, period_start=now
    )
    assert over.over is True and over.pct == 125
    ok = CreditStatus(
        emails=0, linkedin_dms=0, inmails=0, sourced=0, used=50, allowance=200, period_start=now
    )
    assert ok.over is False and ok.pct == 25


@pytest.mark.db
async def test_a_direct_conversation_is_not_a_sourced_candidate(db_session: AsyncSession) -> None:
    """`sourced` counts enrollments, and a direct conversation is also an `Enrollment` — so
    clicking "Message" on a contact charged a sourcing credit for a candidate nobody sourced.
    The same null-campaign guard was added to analytics and the dashboard; this was missed."""
    org = await make_org(db_session, slug="credits-direct")
    ws = await make_workspace(db_session, org=org)
    campaign = Campaign(workspace_id=ws.id, name="C", criteria={}, sequence=[])
    contacts = [
        Contact(workspace_id=ws.id, full_name=f"C{i}", skills=[], tags=[]) for i in range(2)
    ]
    db_session.add_all([campaign, *contacts])
    await db_session.flush()
    db_session.add_all(
        [
            # One real sourced candidate...
            Enrollment(
                workspace_id=ws.id,
                campaign_id=campaign.id,
                contact_id=contacts[0].id,
                state=EnrollmentState.active,
            ),
            # ...and one thread opened by hand, which is not a sourcing event.
            Enrollment(
                workspace_id=ws.id,
                campaign_id=None,
                contact_id=contacts[1].id,
                state=EnrollmentState.active,
            ),
        ]
    )
    await db_session.flush()

    status = await credit_status(
        db_session, organization_id=org.id, plan="pro", now=datetime.now(UTC)
    )
    assert status.sourced == 1
    assert status.used == CREDIT_WEIGHTS["sourced"]
