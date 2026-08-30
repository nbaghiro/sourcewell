"""Shared test fixtures: settings, a transactional DB session, and API clients.

DB fixtures use a real test Postgres (`TEST_DATABASE_URL`) and roll back after each
test. `client` is DB-free; `db_client` wires the same transactional session into the app.
"""

import os
from collections.abc import AsyncIterator, Sequence

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

# Never hit a real SMTP server / LinkedIn provider from the test suite (both channels simulate,
# so LinkedIn touchpoints no-op as "sent" like the demo — tests opt out per-case when they need
# the real provider path).
os.environ.setdefault("EMAIL_DRY_RUN", "1")
# The suite signs up and logs in far more often than a person would; the abuse limiter is
# exercised deliberately in test_auth_hardening.py instead of throttling every other test.
os.environ.setdefault("AUTH_RATE_LIMITS_ENABLED", "0")
os.environ.setdefault("LINKEDIN_DRY_RUN", "1")
# Keep the suite deterministic + offline: blank out any real provider / auth keys from the
# developer's .env (env vars take precedence over .env in pydantic-settings), so the LLM, the
# people-data providers, and the auth providers are all "unconfigured" in tests regardless of host.
os.environ.update(
    dict.fromkeys(
        [
            "ANTHROPIC_API_KEY",
            "PDL_API_KEY",
            "APOLLO_API_KEY",
            "HUNTER_API_KEY",
            "UNIPILE_API_KEY",
            "UNIPILE_DSN",
            "UNIPILE_ACCOUNT_ID",
            "UNIPILE_WEBHOOK_SECRET",
            "WORKOS_API_KEY",
            "WORKOS_CLIENT_ID",
            "SESSION_COOKIE_PASSWORD",
        ],
        "",
    )
)
# Pin the public base URL to its default. Links the app mints (verification, unsubscribe) are
# built from it, and a developer whose .env points at a tunnel would otherwise have those tests
# request an absolute foreign host — where the session cookie lands on the wrong domain.
os.environ["API_BASE_URL"] = "http://localhost:8901"

import app.models  # noqa: F401  (so Base.metadata is complete before create_all)
from app.core.config import Settings, get_settings
from app.core.db import Base, get_session
from app.ext.base import SourceProvider
from app.main import create_app
from app.services.workspace.email_templates import RenderedEmail
from tests.fakes import FakeSourceProvider


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


@pytest.fixture
async def engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(settings.test_database_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await eng.dispose()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    conn = await engine.connect()
    trans = await conn.begin()
    session = AsyncSession(bind=conn, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def db_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


class Outbox:
    """Captures what would have been mailed, in place of the delivery hop."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []  # (to, subject, html)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> "Outbox":
        async def _capture(*, to: str, mail: RenderedEmail) -> bool:
            self.sent.append((to, mail.subject, mail.html))
            return True

        monkeypatch.setattr("app.services.workspace.auth.send_transactional", _capture)
        return self

    @property
    def last_url(self) -> str:
        """The action link out of the most recent email, whichever kind it was — confirmation,
        password reset or invitation. They all carry exactly one `?token=` href."""
        html = self.sent[-1][2]
        marker = html.index("?token=")
        return html[html.rindex('"', 0, marker) + 1 : html.index('"', marker)]


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> Outbox:
    """Every transactional email sent during the test, instead of a real delivery."""
    return Outbox().install(monkeypatch)


@pytest.fixture
def fake_source_providers(monkeypatch: pytest.MonkeyPatch) -> list[FakeSourceProvider]:
    """Route the sourcing paths' provider resolution to a deterministic in-memory provider.

    Tests are keyless (see the env blanking above), so `build_providers_for_org` resolves to an
    empty set; sourcing-path tests that need hits opt in to this fake instead.
    """
    providers = [FakeSourceProvider()]

    async def _build(*args: object, **kwargs: object) -> Sequence[SourceProvider]:
        return providers

    monkeypatch.setattr("app.agents.sourcing.build_providers_for_org", _build)
    return providers
