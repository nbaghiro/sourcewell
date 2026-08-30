"""Self-serve signup: the form's six fields + password → org, admin user, session."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization, User, Workspace
from app.services.workspace.auth import confirm_verification, slugify, verification_token
from app.services.workspace.connections import home_org_id

# A 1x1 transparent PNG — what the form's client-side resize produces, in miniature.
PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def payload(**overrides: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "username": "ada",
        "email": "ada@acme.com",
        "company_name": "Acme Talent",
        "avatar": PNG,
        "password": "correct-horse",
    }
    body.update(overrides)
    return body


async def _created_user(db_session: AsyncSession, email: str) -> User:
    user = (
        (await db_session.execute(select(User).where(User.email == email).limit(1)))
        .scalars()
        .first()
    )
    assert user is not None
    return user


@pytest.mark.db
async def test_signup_creates_org_and_user(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    r = await db_client.post("/auth/signup", json=payload())
    assert r.status_code == 201, r.text
    assert r.json() == {"email": "ada@acme.com", "email_sent": True}

    user = await _created_user(db_session, "ada@acme.com")
    assert user.username == "ada"
    assert user.avatar_url == PNG
    assert (user.first_name, user.last_name) == ("Ada", "Lovelace")
    assert user.name == "Ada Lovelace"  # display name derived from the two halves
    assert user.password_hash is not None and user.password_hash != "correct-horse"
    assert user.email_verified_at is None  # inert until the emailed link is clicked

    org = await db_session.get(Organization, await home_org_id(db_session, user_id=user.id))
    assert org is not None and org.name == "Acme Talent" and org.slug == "acme-talent"

    # a brand-new org lands with somewhere to work
    workspaces = (
        (await db_session.execute(select(Workspace).where(Workspace.organization_id == org.id)))
        .scalars()
        .all()
    )
    assert len(workspaces) == 1

    # signup does NOT sign anyone in — the confirmation link does
    assert await db_client.get("/auth/me") is not None
    assert (await db_client.get("/auth/me")).status_code == 401


@pytest.mark.db
async def test_signup_password_works_once_verified(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    await db_client.post("/auth/signup", json=payload(email="grace@acme.com", username="grace"))
    creds = {"email": "grace@acme.com", "password": "correct-horse"}

    # the gate: right password, unconfirmed address
    assert (await db_client.post("/auth/password", json=creds)).status_code == 403

    user = await _created_user(db_session, "grace@acme.com")
    assert await confirm_verification(db_session, token=verification_token(user.id)) is not None

    r = await db_client.post("/auth/password", json=creds)
    assert r.status_code == 200
    assert r.json()["user"]["username"] == "grace"


@pytest.mark.db
async def test_signup_normalizes_email_and_username(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    r = await db_client.post(
        "/auth/signup",
        json=payload(email="  Mixed.Case@Acme.COM ", username=" MixedCase ", first_name=" Mei "),
    )
    assert r.status_code == 201
    user = await _created_user(db_session, "mixed.case@acme.com")
    assert user.email == "mixed.case@acme.com"
    assert user.username == "mixedcase"
    assert user.first_name == "Mei"


@pytest.mark.db
async def test_duplicate_email_and_username_conflict(db_client: AsyncClient) -> None:
    assert (await db_client.post("/auth/signup", json=payload())).status_code == 201

    dupe_email = await db_client.post("/auth/signup", json=payload(username="ada2"))
    assert dupe_email.status_code == 409
    assert "email" in dupe_email.json()["detail"].lower()

    dupe_name = await db_client.post("/auth/signup", json=payload(email="other@acme.com"))
    assert dupe_name.status_code == 409
    assert "username" in dupe_name.json()["detail"].lower()


@pytest.mark.db
async def test_same_company_name_gets_a_unique_slug(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Several orgs may share a company name — back-to-back signups must not collide on slug.

    (A ULID's leading characters are a timestamp, so a prefix-derived suffix repeats here.)
    """
    responses = [
        await db_client.post(
            "/auth/signup", json=payload(email=f"a{i}@acme.com", username=f"ada{i}")
        )
        for i in range(4)
    ]
    slugs = []
    for i, r in enumerate(responses):
        assert r.status_code == 201, r.text
        user = await _created_user(db_session, f"a{i}@acme.com")
        org = await db_session.get(Organization, await home_org_id(db_session, user_id=user.id))
        assert org is not None
        slugs.append(org.slug)
    assert slugs[0] == "acme-talent"
    assert all(s.startswith("acme-talent-") for s in slugs[1:])
    assert len(set(slugs)) == len(slugs)


@pytest.mark.parametrize(
    "field,value",
    [
        ("first_name", "   "),
        ("last_name", ""),
        ("company_name", " "),
        ("username", "ab"),  # too short
        ("username", "Has Spaces"),
        ("username", "-leading-dash"),
        ("email", "not-an-email"),
        ("password", "short"),
        ("avatar", "https://example.com/me.jpg"),  # must be an uploaded image, not a link
    ],
)
@pytest.mark.db
async def test_rejects_invalid_fields(db_client: AsyncClient, field: str, value: str) -> None:
    r = await db_client.post("/auth/signup", json=payload(**{field: value}))
    assert r.status_code == 422, f"{field}={value!r} should not be accepted"


@pytest.mark.parametrize("avatar", [None, "", "   "])
@pytest.mark.db
async def test_avatar_is_optional(
    db_client: AsyncClient, db_session: AsyncSession, avatar: str | None
) -> None:
    """Not everyone has a photo to hand at signup; the UI falls back to initials."""
    body = payload(email="nopic@acme.com", username="nopic")
    body["avatar"] = avatar
    r = await db_client.post("/auth/signup", json=body)
    assert r.status_code == 201, r.text
    user = await _created_user(db_session, "nopic@acme.com")
    assert user.avatar_url is None


@pytest.mark.db
async def test_avatar_may_be_omitted_entirely(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    body = payload(email="absent@acme.com", username="absent")
    del body["avatar"]
    assert (await db_client.post("/auth/signup", json=body)).status_code == 201
    assert (await _created_user(db_session, "absent@acme.com")).avatar_url is None


@pytest.mark.db
async def test_rejects_oversized_avatar(db_client: AsyncClient) -> None:
    huge = "data:image/png;base64," + "A" * 2_000_000
    r = await db_client.post("/auth/signup", json=payload(avatar=huge))
    assert r.status_code == 422


@pytest.mark.parametrize(
    "name,slug",
    [
        ("Acme Talent", "acme-talent"),
        ("Acme, Inc.", "acme-inc"),
        ("  Hello   World  ", "hello-world"),
        ("!!!", "org"),
    ],
)
def test_slugify(name: str, slug: str) -> None:
    assert slugify(name) == slug


@pytest.mark.db
async def test_password_signup_lands_in_a_usable_workspace(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    """All three ways of creating an org share one implementation, so all three produce the same
    default workspace — this one used to add its own copy afterwards."""
    assert (await db_client.post("/auth/signup", json=payload())).status_code == 201
    user = await _created_user(db_session, "ada@acme.com")
    workspaces = (
        (
            await db_session.execute(
                select(Workspace).where(
                    Workspace.organization_id == await home_org_id(db_session, user_id=user.id)
                )
            )
        )
        .scalars()
        .all()
    )
    assert [w.name for w in workspaces] == ["Default workspace"]
