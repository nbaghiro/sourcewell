"""The public Unipile inbound receiver — replies → handle_reply + account lifecycle."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import messaging as messaging_api
from app.core.config import Settings
from app.models import (
    Campaign,
    Connection,
    ConnectionProvider,
    ConnectionStatus,
    Contact,
    Enrollment,
    EnrollmentState,
    Message,
    MessageDirection,
)
from app.services.workspace import notifications as notifications_service
from app.services.workspace.connections import upsert_seat
from tests.factories import make_org, make_user, make_workspace

_SECRET = "shh"


def _with_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        messaging_api, "get_settings", lambda: Settings(unipile_webhook_secret=_SECRET)
    )


async def _enrollment_with_chat(session: AsyncSession, *, slug: str, chat_id: str) -> Enrollment:
    org = await make_org(session, slug=slug)
    ws = await make_workspace(session, org=org)
    c = Campaign(workspace_id=ws.id, name="C", criteria={}, sequence=[])
    contact = Contact(
        workspace_id=ws.id, full_name="Lee", email="lee@example.com", skills=[], tags=[]
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
    session.add(
        Message(
            workspace_id=ws.id,
            enrollment_id=enr.id,
            direction=MessageDirection.outbound,
            body="hi",
            external_id=chat_id,
            account_id="acct-1",
        )
    )
    await session.flush()
    return enr


# --- auth gates --------------------------------------------------------------


@pytest.mark.db
async def test_unipile_webhook_not_configured(db_client: AsyncClient) -> None:
    resp = await db_client.post("/webhooks/unipile", json={})
    assert resp.status_code == 503  # blank secret disables the receiver


@pytest.mark.db
async def test_unipile_webhook_bad_token(
    db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_secret(monkeypatch)
    resp = await db_client.post("/webhooks/unipile?token=wrong", json={"text": "hi"})
    assert resp.status_code == 401


# --- inbound replies + account lifecycle -------------------------------------


@pytest.mark.db
async def test_unipile_webhook_linkedin_reply_maps_chat(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_secret(monkeypatch)
    enr = await _enrollment_with_chat(db_session, slug="uw-li", chat_id="CHAT-1")
    resp = await db_client.post(
        f"/webhooks/unipile?token={_SECRET}",
        json={
            "event": "message_received",
            "account_id": "acct-1",
            "chat_id": "CHAT-1",
            "message": {"text": "Yes, I'm interested!"},
        },
    )
    assert resp.status_code == 200
    # Recorded, not routed: the agent runs on the worker so Unipile gets a fast ack.
    assert resp.json()["status"] == "queued"
    inbound = (
        (
            await db_session.execute(
                select(Message).where(
                    Message.enrollment_id == enr.id,
                    Message.direction == MessageDirection.inbound,
                )
            )
        )
        .scalars()
        .all()
    )
    assert inbound  # the reply was mapped via chat_id → external_id and ingested


@pytest.mark.db
async def test_unipile_webhook_unknown_chat_ignored(
    db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_secret(monkeypatch)
    resp = await db_client.post(
        f"/webhooks/unipile?token={_SECRET}",
        json={"event": "message_received", "chat_id": "NOPE", "message": {"text": "hi"}},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


@pytest.mark.db
async def test_unipile_webhook_account_credentials_flips_seat(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_secret(monkeypatch)
    org = await make_org(db_session, slug="uw-acct")
    user = await make_user(db_session)
    seat = await upsert_seat(
        db_session,
        organization_id=org.id,
        user_id=user.id,
        provider=ConnectionProvider.linkedin,
        account_id="acct-x",
    )
    resp = await db_client.post(
        f"/webhooks/unipile?token={_SECRET}",
        json={"event": "credentials", "account_id": "acct-x"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "account_updated"
    await db_session.refresh(seat)
    assert seat.status == ConnectionStatus.needs_reauth


@pytest.mark.db
async def test_a_broken_seat_reaches_its_owner(db_session: AsyncSession) -> None:
    """A disconnected seat used to surface only as a badge on a Settings tab nobody was looking
    at, so outreach silently stopped and the recruiter found out days later. It now lands in their
    notification feed, pointing at the page where they can fix it."""
    org = await make_org(db_session, slug="seat-broken")
    ws = await make_workspace(db_session, org=org)
    user = await make_user(db_session, org=org)
    db_session.add(
        Connection(
            organization_id=org.id,
            user_id=user.id,
            provider=ConnectionProvider.linkedin,
            external_id="ACCT-DEAD",
            status=ConnectionStatus.needs_reauth,
        )
    )
    await db_session.flush()

    feed = await notifications_service.build_feed(db_session, workspace_id=ws.id, user=user)
    item = next(i for i in feed.items if i.type == "seat_needs_reauth")
    assert "linkedin" in item.title
    assert item.link == "/settings"  # not the inbox — there is nothing to do there


@pytest.mark.db
async def test_a_colleagues_broken_seat_is_not_your_problem(db_session: AsyncSession) -> None:
    """Seats belong to a person, and it is the owner who has to reconnect. Settings shows the
    org-wide view for an admin who wants it."""
    org = await make_org(db_session, slug="seat-broken-other")
    ws = await make_workspace(db_session, org=org)
    mine = await make_user(db_session, org=org, email="mine@acme.com")
    theirs = await make_user(db_session, org=org, email="theirs@acme.com")
    db_session.add(
        Connection(
            organization_id=org.id,
            user_id=theirs.id,
            provider=ConnectionProvider.linkedin,
            external_id="ACCT-THEIRS",
            status=ConnectionStatus.needs_reauth,
        )
    )
    await db_session.flush()

    feed = await notifications_service.build_feed(db_session, workspace_id=ws.id, user=mine)
    assert [i for i in feed.items if i.type == "seat_needs_reauth"] == []
