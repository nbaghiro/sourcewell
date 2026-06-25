"""Shared test fixtures: settings, a transactional DB session, and API clients.

DB fixtures use a real test Postgres (`TEST_DATABASE_URL`) and roll back after each
test. `client` is DB-free; `db_client` wires the same transactional session into the app.
"""

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

# Never hit a real SMTP server from the test suite.
os.environ.setdefault("EMAIL_DRY_RUN", "1")
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

import app.models  # noqa: F401  (so Base.metadata is complete before create_all)
from app.core.config import Settings, get_settings
from app.core.db import Base, get_session
from app.main import create_app


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
