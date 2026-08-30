"""Signing up with Google / Microsoft: provision on first arrival, then finish the profile.

The provider gives us a verified email address and a display name — not a username, not a company,
not an avatar. So a first OAuth sign-in creates the account (already verified) and sends the user
to the *signup form* to supply the rest; a returning one goes straight into the app. These pin
which of those two happens, and that the completion step runs exactly once.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Membership, Organization, User, UserStatus, Workspace
from app.services.workspace import auth as auth_service
from app.services.workspace.connections import home_org_id, provision_user
from tests.factories import make_org, make_user
from tests.test_signup import PNG

_PROFILE = {
    "first_name": "Mei",
    "last_name": "Tanaka",
    "username": "mei",
    "company_name": "Northwind Talent",
    "avatar": PNG,
}


def _oauth_callback(monkeypatch: pytest.MonkeyPatch, user_id: str) -> None:
    """Stand in for the WorkOS code exchange, which is the only live hop in the flow."""

    async def _complete(*_args: object, **_kwargs: object) -> str:
        return user_id

    monkeypatch.setattr(auth_service, "complete_workos_login", _complete)


# --- first arrival: provisioned, verified, and still owing a profile ----------


@pytest.mark.db
async def test_first_oauth_sign_in_provisions_a_verified_user_owing_a_profile(
    db_session: AsyncSession,
) -> None:
    user = await provision_user(
        db_session, subject="workos_user_01", name="Mei Tanaka", email="mei@northwind.com"
    )
    # The provider proved the address, so there is nothing to confirm by email...
    assert user.email_verified_at is not None
    # ...but the signup profile is outstanding, which is what routes them to the form.
    assert user.profile_completed_at is None
    assert user.username is None

    # They still get a usable tenant: org, default workspace, org-admin membership.
    org = await db_session.get(Organization, await home_org_id(db_session, user_id=user.id))
    assert org is not None
    workspaces = (
        (await db_session.execute(select(Workspace).where(Workspace.organization_id == org.id)))
        .scalars()
        .all()
    )
    assert [w.name for w in workspaces] == ["Default workspace"]
    membership = (
        (await db_session.execute(select(Membership).where(Membership.user_id == user.id)))
        .scalars()
        .one()
    )
    assert membership.organization_id == org.id


@pytest.mark.db
async def test_returning_oauth_user_is_matched_on_the_provider_identity(
    db_session: AsyncSession,
) -> None:
    """ "Do they have an account with this provider?" is the `sso_subject` match — and a second
    sign-in must not fork a second org off the same identity."""
    first = await provision_user(
        db_session, subject="workos_user_02", name="Mei Tanaka", email="mei@northwind.com"
    )
    first.profile_completed_at = first.email_verified_at
    await db_session.flush()

    again = await provision_user(
        db_session, subject="workos_user_02", name="Mei T", email="mei@northwind.com"
    )
    assert again.id == first.id
    assert await home_org_id(db_session, user_id=again.id) == await home_org_id(
        db_session, user_id=first.id
    )
    orgs = (await db_session.execute(select(Organization))).scalars().all()
    assert len(orgs) == 1


# --- where the callback lands you ---------------------------------------------


@pytest.mark.db
async def test_callback_sends_an_unfinished_signup_to_the_form(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await provision_user(
        db_session, subject="workos_user_03", name="Mei Tanaka", email="mei@northwind.com"
    )
    _oauth_callback(monkeypatch, user.id)

    r = await db_client.get("/auth/callback?code=any")
    assert r.headers["location"].endswith("/signup")
    # Signed in already — the completion form is posted as an authenticated request.
    me = await db_client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["profile_complete"] is False


@pytest.mark.db
async def test_callback_sends_a_returning_user_straight_into_the_app(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await make_org(db_session, slug="oauth-returning")
    user = await make_user(db_session, org=org, email="back@northwind.com")
    user.sso_subject = "workos_user_04"
    user.profile_completed_at = user.created_at
    await db_session.flush()
    _oauth_callback(monkeypatch, user.id)

    r = await db_client.get("/auth/callback?code=any")
    assert not r.headers["location"].endswith("/signup")
    assert (await db_client.get("/auth/me")).json()["profile_complete"] is True


# --- completing the profile ---------------------------------------------------


@pytest.mark.db
async def test_completing_the_profile_names_the_org_and_finishes_signup(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The org was provisioned under a placeholder off the email domain; the company name the
    user actually types is what it ends up called."""
    user = await provision_user(
        db_session, subject="workos_user_05", name="Mei Tanaka", email="mei@northwind.com"
    )
    org = await db_session.get(Organization, await home_org_id(db_session, user_id=user.id))
    assert org is not None and org.name == "Northwind"  # the placeholder, from the domain
    _oauth_callback(monkeypatch, user.id)
    await db_client.get("/auth/callback?code=any")

    r = await db_client.post("/auth/complete-profile", json=_PROFILE)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["username"] == "mei"
    assert r.json()["user"]["avatar_url"] == PNG

    await db_session.refresh(user)
    assert user.profile_completed_at is not None
    assert user.first_name == "Mei" and user.last_name == "Tanaka"
    assert user.name == "Mei Tanaka"
    await db_session.refresh(org)
    assert org.name == "Northwind Talent"
    assert org.slug == "northwind-talent"
    assert (await db_client.get("/auth/me")).json()["profile_complete"] is True


@pytest.mark.db
async def test_the_completion_form_never_moves_the_email(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The address is the provider's, not the form's — the client shows it read-only, and the
    endpoint has nowhere to put one even if a caller sends it."""
    user = await provision_user(
        db_session, subject="workos_user_06", name="Mei Tanaka", email="mei@northwind.com"
    )
    _oauth_callback(monkeypatch, user.id)
    await db_client.get("/auth/callback?code=any")

    r = await db_client.post(
        "/auth/complete-profile", json={**_PROFILE, "email": "someone.else@evil.com"}
    )
    assert r.status_code == 200
    await db_session.refresh(user)
    assert user.email == "mei@northwind.com"


@pytest.mark.db
async def test_completion_runs_once(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It is the last step of signup, not a settings editor — a second post is refused rather
    than letting anyone rename the org through it."""
    user = await provision_user(
        db_session, subject="workos_user_07", name="Mei Tanaka", email="mei@northwind.com"
    )
    _oauth_callback(monkeypatch, user.id)
    await db_client.get("/auth/callback?code=any")

    assert (await db_client.post("/auth/complete-profile", json=_PROFILE)).status_code == 200
    replay = await db_client.post(
        "/auth/complete-profile", json={**_PROFILE, "company_name": "Someone Else Inc"}
    )
    assert replay.status_code == 409
    org = await db_session.get(Organization, await home_org_id(db_session, user_id=user.id))
    assert org is not None and org.name == "Northwind Talent"


@pytest.mark.db
async def test_completion_rejects_a_taken_username(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`username` is unique across the install, so the form has to fail the same way signup does
    rather than surfacing an IntegrityError as a 500."""
    other_org = await make_org(db_session, slug="oauth-taken")
    other = await make_user(db_session, org=other_org, email="taken@acme.com")
    other.username = "mei"
    await db_session.flush()

    user = await provision_user(
        db_session, subject="workos_user_08", name="Mei Tanaka", email="mei@northwind.com"
    )
    _oauth_callback(monkeypatch, user.id)
    await db_client.get("/auth/callback?code=any")

    r = await db_client.post("/auth/complete-profile", json=_PROFILE)
    assert r.status_code == 409
    assert "username" in r.json()["detail"].lower()
    await db_session.refresh(user)
    assert user.profile_completed_at is None  # still owed, so they stay on the form


@pytest.mark.db
async def test_completion_needs_a_session(db_client: AsyncClient) -> None:
    assert (await db_client.post("/auth/complete-profile", json=_PROFILE)).status_code == 401


# --- the other doors are unaffected -------------------------------------------


@pytest.mark.db
async def test_password_signup_owes_nothing(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    """The password form collects every profile field up front, so it must not route the user
    back into a completion step they already did."""
    from tests.test_signup import payload

    assert (await db_client.post("/auth/signup", json=payload())).status_code == 201
    user = (
        (await db_session.execute(select(User).where(User.email == "ada@acme.com"))).scalars().one()
    )
    assert user.profile_completed_at is not None


@pytest.mark.db
async def test_an_invited_teammate_owes_nothing(db_session: AsyncSession) -> None:
    """They join an org that already exists: no company to name, no password to set. Asking them
    for a company name would let a teammate rename the org they were invited into."""
    org = await make_org(db_session, slug="oauth-invited")
    invited = await make_user(db_session, org=org, email="teammate@acme.com")
    invited.sso_subject = None
    invited.status = UserStatus.invited
    invited.profile_completed_at = None
    await db_session.flush()

    linked = await provision_user(
        db_session, subject="workos_user_09", name="Team Mate", email="teammate@acme.com"
    )
    assert linked.id == invited.id
    assert linked.status is UserStatus.active
    assert linked.profile_completed_at is not None
    assert await home_org_id(db_session, user_id=linked.id) == org.id


# --- the gate is enforced server-side, not just routed around in the client ----


@pytest.mark.db
async def test_an_unfinished_signup_cannot_use_the_rest_of_the_api(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Routing is not enforcement.

    The client sends an unfinished user to the form, but nothing stopped them calling the API
    directly — and working in an org still carrying its placeholder name, as a user with no
    username. 403, not 401: they *are* signed in, they just aren't finished.
    """
    user = await provision_user(
        db_session, subject="workos_user_10", name="Mei Tanaka", email="mei@northwind.com"
    )
    _oauth_callback(monkeypatch, user.id)
    await db_client.get("/auth/callback?code=any")

    for path in (
        "/contacts",
        "/campaigns",
        "/inbox",
        "/settings/connections",
        "/dashboard/summary",
    ):
        r = await db_client.get(path)
        assert r.status_code == 403, f"{path} -> {r.status_code}"
        assert r.json()["detail"] == "profile_incomplete"

    # ...and it lifts the moment the form is posted.
    assert (await db_client.post("/auth/complete-profile", json=_PROFILE)).status_code == 200
    workspace = (await db_client.get("/auth/me")).json()["workspaces"][0]["id"]
    assert (
        await db_client.get("/campaigns", headers={"X-Workspace-Id": workspace})
    ).status_code == 200


@pytest.mark.db
async def test_the_gate_exempts_exactly_the_two_endpoints_that_lift_it(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/auth/me` is how the client learns it is unfinished and `/auth/complete-profile` is what
    finishes it — gating either would deadlock the flow."""
    user = await provision_user(
        db_session, subject="workos_user_11", name="Mei Tanaka", email="mei@northwind.com"
    )
    _oauth_callback(monkeypatch, user.id)
    await db_client.get("/auth/callback?code=any")

    me = await db_client.get("/auth/me")
    assert me.status_code == 200 and me.json()["profile_complete"] is False
    assert (await db_client.post("/auth/complete-profile", json=_PROFILE)).status_code == 200


@pytest.mark.db
async def test_the_gate_does_not_catch_ordinary_accounts(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Every other way of arriving at an account is past signup — a password signup, an org
    bootstrap, an invite, the demo seed. None of them may be locked out by this."""
    org = await make_org(db_session, slug="gate-ordinary")
    user = await make_user(db_session, org=org)
    await db_session.flush()
    r = await db_client.get("/auth/me", headers={"X-User-Id": user.id})
    assert r.status_code == 200 and r.json()["profile_complete"] is True
