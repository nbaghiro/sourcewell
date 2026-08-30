"""Team invites: the emailed link is what turns a pending row into an account.

An invite writes a `User` carrying an address its owner has not agreed to, so the row proves
nothing on its own. Until the link is clicked the seat cannot be signed in to and cannot be linked
to a Google/Microsoft identity — which is what stops an invite from being a *claim* on someone
else's address.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Membership, MembershipRole, User, UserStatus
from app.services.workspace import auth as auth_service
from app.services.workspace.connections import home_org_id, provision_user
from tests.conftest import Outbox
from tests.factories import make_org, make_user


async def _admin(session: AsyncSession, *, slug: str) -> User:
    org = await make_org(session, slug=slug)
    return await make_user(
        session,
        org=org,
        role=MembershipRole.org_admin,
        name="Ada Lovelace",
        email=f"admin@{slug}.com",
    )


async def _org_of(session: AsyncSession, user: User) -> str | None:
    return await home_org_id(session, user_id=user.id)


async def _invite(
    client: AsyncClient, admin: User, email: str = "mei@northwind.com"
) -> dict[str, Any]:
    r = await client.post(
        "/settings/members/invite",
        json={"email": email, "name": "Mei Tanaka", "role": "member"},
        headers={"X-User-Id": admin.id},
    )
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


# --- inviting sends the link --------------------------------------------------


@pytest.mark.db
async def test_inviting_mails_the_link_and_leaves_the_seat_pending(
    db_client: AsyncClient, db_session: AsyncSession, outbox: Outbox
) -> None:
    admin = await _admin(db_session, slug="inv-send")
    body = await _invite(db_client, admin)
    assert body["email_sent"] is True

    invited = (
        (await db_session.execute(select(User).where(User.email == "mei@northwind.com")))
        .scalars()
        .one()
    )
    assert invited.status is UserStatus.invited
    assert invited.email_verified_at is None  # nothing proven until the link is clicked
    membership = (
        (await db_session.execute(select(Membership).where(Membership.user_id == invited.id)))
        .scalars()
        .one()
    )
    assert membership.role is MembershipRole.member

    to, subject, _html = outbox.sent[-1]
    assert to == "mei@northwind.com"
    assert "Ada Lovelace" in subject and "Sourcewell" in subject


@pytest.mark.db
async def test_re_inviting_a_pending_address_resends_rather_than_failing(
    db_client: AsyncClient, db_session: AsyncSession, outbox: Outbox
) -> None:
    """An admin's natural "did they get it?" retry. Without this the only 'resend' was a 409."""
    admin = await _admin(db_session, slug="inv-resend")
    first = await _invite(db_client, admin)
    again = await _invite(db_client, admin)

    assert again["id"] == first["id"]  # the same seat, not a second one
    assert len(outbox.sent) == 2
    users = (
        (await db_session.execute(select(User).where(User.email == "mei@northwind.com")))
        .scalars()
        .all()
    )
    assert len(users) == 1


@pytest.mark.db
async def test_inviting_an_active_member_still_conflicts(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _admin(db_session, slug="inv-dupe")
    r = await db_client.post(
        "/settings/members/invite",
        json={"email": admin.email, "name": "Ada", "role": "member"},
        headers={"X-User-Id": admin.id},
    )
    assert r.status_code == 409


# --- accepting the link -------------------------------------------------------


@pytest.mark.db
async def test_accepting_the_link_activates_the_member_and_signs_them_in(
    db_client: AsyncClient, db_session: AsyncSession, outbox: Outbox
) -> None:
    admin = await _admin(db_session, slug="inv-accept")
    await _invite(db_client, admin)
    path = outbox.last_url.replace("http://localhost:8901", "")

    r = await db_client.get(path)
    assert "/?invited=1" in r.headers["location"]

    me = await db_client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "mei@northwind.com"
    assert me.json()["profile_complete"] is True  # they joined an org — nothing left to ask

    invited = (
        (await db_session.execute(select(User).where(User.email == "mei@northwind.com")))
        .scalars()
        .one()
    )
    assert invited.status is UserStatus.active
    assert invited.email_verified_at is not None


@pytest.mark.db
async def test_a_forged_or_expired_link_mints_no_session(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _admin(db_session, slug="inv-forged")
    invited = await make_user(db_session, org=await make_org(db_session, slug="inv-x"))
    assert admin  # the invite is unrelated to who forged the link

    forged = await db_client.get("/auth/invite?token=not-a-real-token")
    assert "error=invite_invalid" in forged.headers["location"]

    stale = auth_service.invite_token(invited.id, ttl_hours=-1)
    expired = await db_client.get(f"/auth/invite?token={stale}")
    assert "error=invite_invalid" in expired.headers["location"]
    assert (await db_client.get("/auth/me")).status_code == 401


@pytest.mark.db
async def test_the_link_survives_a_mail_scanner_prefetch(
    db_client: AsyncClient, db_session: AsyncSession, outbox: Outbox
) -> None:
    """Same reason as the signup confirmation: security scanners click every URL in an inbound
    message, so refusing the second click would strand a brand-new teammate."""
    admin = await _admin(db_session, slug="inv-scanner")
    await _invite(db_client, admin)
    path = outbox.last_url.replace("http://localhost:8901", "")

    assert "/?invited=1" in (await db_client.get(path)).headers["location"]
    db_client.cookies.clear()  # the scanner keeps nothing
    assert "/?invited=1" in (await db_client.get(path)).headers["location"]
    assert (await db_client.get("/auth/me")).status_code == 200


@pytest.mark.db
async def test_a_disabled_member_cannot_re_enter_through_an_old_link(
    db_client: AsyncClient, db_session: AsyncSession, outbox: Outbox
) -> None:
    """Accepting confirms an address; it must never resurrect a revoked account."""
    admin = await _admin(db_session, slug="inv-disabled")
    await _invite(db_client, admin)
    path = outbox.last_url.replace("http://localhost:8901", "")
    invited = (
        (await db_session.execute(select(User).where(User.email == "mei@northwind.com")))
        .scalars()
        .one()
    )
    invited.status = UserStatus.disabled
    await db_session.flush()

    r = await db_client.get(path)
    assert "error=invite_invalid" in r.headers["location"]
    assert (await db_client.get("/auth/me")).status_code == 401


# --- the capture the link closes ----------------------------------------------


@pytest.mark.db
async def test_an_accepted_invite_does_link_the_google_sign_in(
    db_client: AsyncClient, db_session: AsyncSession, outbox: Outbox
) -> None:
    """The flip side: once they've proven the address, signing in with Google joins the org they
    were invited to instead of forking a second one."""
    admin = await _admin(db_session, slug="inv-linked")
    await _invite(db_client, admin)
    await db_client.get(outbox.last_url.replace("http://localhost:8901", ""))
    invited = (
        (await db_session.execute(select(User).where(User.email == "mei@northwind.com")))
        .scalars()
        .one()
    )

    arriving = await provision_user(
        db_session, subject="wos-mei", name="Mei Tanaka", email="mei@northwind.com"
    )
    assert arriving.id == invited.id
    assert arriving.sso_subject == "wos-mei"
