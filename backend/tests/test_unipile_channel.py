"""The Unipile channel client + Message reply-mapping fields."""

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.ext.unipile import UnipileChannel
from app.models import (
    Campaign,
    Contact,
    Enrollment,
    EnrollmentState,
    Message,
    MessageDirection,
)
from tests.factories import make_org, make_workspace

_DSN = "https://api1.unipile.com:1234"


# --- the channel client (respx-mocked, no live API) --------------------------


@respx.mock
async def test_linkedin_send_resolves_provider_id_and_returns_chat_id() -> None:
    respx.get(f"{_DSN}/api/v1/users/leepark").mock(
        return_value=httpx.Response(200, json={"provider_id": "PID-1"})
    )
    chats = respx.post(f"{_DSN}/api/v1/chats").mock(
        return_value=httpx.Response(200, json={"chat_id": "CHAT-1"})
    )
    chat_id = await UnipileChannel("linkedin", "key", _DSN).send(
        account_id="acct", to="https://linkedin.com/in/leepark", subject=None, body="hi"
    )
    assert chat_id == "CHAT-1"
    assert chats.called  # resolved the provider id, then posted the chat


@respx.mock
async def test_linkedin_reply_posts_to_chat_thread() -> None:
    route = respx.post(f"{_DSN}/api/v1/chats/CHAT-1/messages").mock(
        return_value=httpx.Response(200, json={})
    )
    await UnipileChannel("linkedin", "key", _DSN).reply(
        account_id="acct", thread_id="CHAT-1", body="following up"
    )
    assert route.called


@respx.mock
async def test_email_send_posts_to_emails() -> None:
    respx.post(f"{_DSN}/api/v1/emails").mock(return_value=httpx.Response(200, json={"id": "EM-1"}))
    email_id = await UnipileChannel("email", "key", _DSN).send(
        account_id="acct", to="lee@example.com", subject="Hi", body="hello"
    )
    assert email_id == "EM-1"


# --- the reply-mapping model fields ------------------------------------------


@pytest.mark.db
async def test_message_reply_mapping_fields_roundtrip(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="msg-rm")
    ws = await make_workspace(db_session, org=org)
    c = Campaign(workspace_id=ws.id, name="C", criteria={}, sequence=[])
    contact = Contact(workspace_id=ws.id, full_name="Lee", skills=[], tags=[])
    db_session.add_all([c, contact])
    await db_session.flush()
    enr = Enrollment(
        workspace_id=ws.id, campaign_id=c.id, contact_id=contact.id, state=EnrollmentState.active
    )
    db_session.add(enr)
    await db_session.flush()
    msg = Message(
        workspace_id=ws.id,
        enrollment_id=enr.id,
        direction=MessageDirection.outbound,
        body="hi",
        external_id="CHAT-9",
        account_id="acct-1",
    )
    db_session.add(msg)
    await db_session.flush()
    await db_session.refresh(msg)
    assert msg.external_id == "CHAT-9"
    assert msg.account_id == "acct-1"
