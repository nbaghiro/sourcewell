"""Phase 6: the Outreach agent — rewrite, live conversation (handoff / opt-out / reply), HITL."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import outreach
from app.agents.outreach import handle_reply, run_conversation
from app.models import (
    AutonomyLevel,
    Campaign,
    CampaignStatus,
    Contact,
    Enrollment,
    EnrollmentState,
    Message,
    MessageDirection,
    MessageStatus,
    Organization,
)
from app.services.outreach import messaging
from app.services.sourcing.suppression import is_suppressed
from tests.factories import make_org, make_workspace
from tests.fake_llm import FakeLLM, text_turn, tool_turn


async def _thread(
    session: AsyncSession, *, slug: str, autonomy: AutonomyLevel = AutonomyLevel.assisted
) -> tuple[Organization, Enrollment, Contact]:
    org = await make_org(session, slug=slug)
    ws = await make_workspace(session, org=org)
    c = Campaign(
        workspace_id=ws.id,
        name="C",
        status=CampaignStatus.active,
        autonomy_level=autonomy,
        criteria={},
        sequence=[],
    )
    contact = Contact(
        workspace_id=ws.id, full_name="Tomas R", email="tomas@example.com", skills=[], tags=[]
    )
    session.add_all([c, contact])
    await session.flush()
    enr = Enrollment(
        workspace_id=ws.id,
        campaign_id=c.id,
        contact_id=contact.id,
        state=EnrollmentState.awaiting_reply,
    )
    session.add(enr)
    await session.flush()
    return org, enr, contact


async def _outbound(session: AsyncSession, enrollment_id: str) -> list[Message]:
    rows = await session.execute(
        select(Message).where(
            Message.enrollment_id == enrollment_id,
            Message.direction == MessageDirection.outbound,
        )
    )
    return list(rows.scalars().all())


# --- rewrite (single call) ---------------------------------------------------


async def test_rewrite_message_fallback() -> None:
    out = await messaging.rewrite_message("Hi there", "make it warmer")  # no LLM in tests
    assert out == "Hi there"


# --- the conversation agent --------------------------------------------------


@pytest.mark.db
async def test_conversation_hands_off(db_session: AsyncSession) -> None:
    org, enr, _contact = await _thread(db_session, slug="oa-handoff")
    llm = FakeLLM([tool_turn("hand_off", {"summary": "interested"}), text_turn("Handed off.")])
    res = await run_conversation(
        db_session,
        llm=llm,
        enrollment=enr,
        inbound_text="Yes, I'm interested — let's talk!",
        organization_id=org.id,
        now=datetime.now(UTC),
    )
    assert res.status == "done"
    assert enr.state == EnrollmentState.handed_off


@pytest.mark.db
async def test_conversation_opts_out_and_suppresses(db_session: AsyncSession) -> None:
    org, enr, contact = await _thread(db_session, slug="oa-optout")
    llm = FakeLLM([tool_turn("opt_out", {}), text_turn("Removed.")])
    await run_conversation(
        db_session,
        llm=llm,
        enrollment=enr,
        inbound_text="please unsubscribe me",
        organization_id=org.id,
        now=datetime.now(UTC),
    )
    assert enr.state == EnrollmentState.opted_out
    assert await is_suppressed(db_session, organization_id=org.id, email=contact.email)


@pytest.mark.db
async def test_conversation_reply_full_autonomy_sends(db_session: AsyncSession) -> None:
    org, enr, _contact = await _thread(db_session, slug="oa-auto", autonomy=AutonomyLevel.full)
    llm = FakeLLM(
        [tool_turn("reply", {"text": "Great — here's more on the role."}), text_turn("Sent.")]
    )
    await run_conversation(
        db_session,
        llm=llm,
        enrollment=enr,
        inbound_text="Tell me more?",
        organization_id=org.id,
        now=datetime.now(UTC),
    )
    out = await _outbound(db_session, enr.id)
    assert out and out[0].status == MessageStatus.sent


@pytest.mark.db
async def test_conversation_reply_assisted_queues_draft(db_session: AsyncSession) -> None:
    org, enr, _contact = await _thread(
        db_session, slug="oa-assist", autonomy=AutonomyLevel.assisted
    )
    llm = FakeLLM([tool_turn("reply", {"text": "Happy to share details."}), text_turn("Drafted.")])
    await run_conversation(
        db_session,
        llm=llm,
        enrollment=enr,
        inbound_text="What's the comp?",
        organization_id=org.id,
        now=datetime.now(UTC),
    )
    out = await _outbound(db_session, enr.id)
    assert out and out[0].status == MessageStatus.draft  # HITL: queued, not sent
    assert enr.state == EnrollmentState.awaiting_approval


@pytest.mark.db
async def test_handle_reply_deterministic_fallback(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(outreach, "default_llm", lambda: None)  # force the deterministic path
    org, enr, _contact = await _thread(db_session, slug="oa-det")
    result = await handle_reply(
        db_session,
        enrollment=enr,
        text="not interested, please remove me",
        now=datetime.now(UTC),
        organization_id=org.id,
    )
    assert result == "deterministic"
    inbound = await db_session.execute(
        select(Message).where(
            Message.enrollment_id == enr.id, Message.direction == MessageDirection.inbound
        )
    )
    assert inbound.scalars().all()  # the reply was ingested
