"""FastAPI application factory."""

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.agent import router as agent_router
from app.api.analytics import router as analytics_router
from app.api.audit import router as audit_router
from app.api.auth import router as auth_router
from app.api.billing import router as billing_router
from app.api.campaigns import router as campaigns_router
from app.api.contacts import router as contacts_router
from app.api.dashboard import router as dashboard_router
from app.api.discovery import router as people_router
from app.api.enrollment import router as enrollment_router
from app.api.messaging import router as messaging_router
from app.api.notifications import router as notifications_router
from app.api.runtime import router as admin_router
from app.api.search import router as search_router
from app.api.settings import router as settings_router
from app.api.suppression import router as suppression_router
from app.api.tenancy import router as tenancy_router
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging, logger
from app.services.outreach.receiving import ensure_inbound_webhooks_quietly

# In-process fixed-window rate limiter (per client IP). Front with a shared store for multi-process.
_RL: dict[str, tuple[float, int]] = {}
_RL_WINDOW = 60.0
_RL_LIMIT = 600

# Every header the React app may put on a request. This is a *contract with the client*, not a
# preference: a browser preflight naming a header that isn't here is rejected outright with
# `400 Disallowed CORS headers`, which takes the whole app down while the server logs nothing but
# OPTIONS 400s. `X-Organization-Id` went missing from this list once and did exactly that — the
# app worked until the first sign-in, then every request died at the preflight.
#
# `tests/test_cors.py` reads the client's own header list out of `frontend/src/lib/api/tenant.ts`
# and fails if this list doesn't cover it, so adding a header on one side can't silently break
# the other.
CORS_ALLOW_HEADERS = [
    "Content-Type",
    "Authorization",
    "X-User-Id",
    "X-Workspace-Id",
    "X-Organization-Id",
    "X-Signature",
]


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()

    # Refuse to boot insecurely rather than serve a deployment that looks healthy but isn't.
    problems = settings.production_config_errors()
    if problems:
        listed = "\n  - ".join(problems)
        raise RuntimeError(
            f"Refusing to start in environment={settings.environment!r} with insecure "
            f"settings:\n  - {listed}"
        )

    # Not fatal, but the failure it describes is completely silent otherwise: the user confirms
    # their email, is signed in on one host, and lands on another still logged out.
    scope_warning = settings.cookie_scope_warning()
    if scope_warning:
        logger.warning("config: %s", scope_warning)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Assert the inbound subscription on every boot. Unipile only pushes to URLs we've
        # subscribed, and the URL changes with the deployment (and with every dev tunnel), so
        # without this the receiver is correct but never hears anything. Fail-soft by design.
        await ensure_inbound_webhooks_quietly()
        yield

    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    # The React app is a separate origin (:8900) and sends the session cookie, so allow
    # credentialed requests from it. Methods/headers are scoped (not "*") alongside credentials.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_url],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=CORS_ALLOW_HEADERS,
    )

    @app.middleware("http")
    async def observe_and_limit(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path != "/health":
            client = request.client.host if request.client else "unknown"
            now = time.monotonic()
            start, count = _RL.get(client, (now, 0))
            if now - start >= _RL_WINDOW:
                start, count = now, 0
            count += 1
            _RL[client] = (start, count)
            if count > _RL_LIMIT:
                return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
        started = time.monotonic()
        response = await call_next(request)
        ms = (time.monotonic() - started) * 1000
        logger.info(
            "%s %s -> %s (%.0fms)", request.method, request.url.path, response.status_code, ms
        )
        return response

    @app.get("/health")
    async def health() -> dict[str, str]:
        db = "ok"
        try:
            async with SessionLocal() as session:
                await session.execute(text("SELECT 1"))
        except Exception:
            db = "down"
        return {
            "status": "ok" if db == "ok" else "degraded",
            "app": settings.app_name,
            "env": settings.environment,
            "db": db,
        }

    app.include_router(auth_router)
    app.include_router(tenancy_router)
    app.include_router(contacts_router)
    app.include_router(campaigns_router)
    app.include_router(enrollment_router)
    app.include_router(messaging_router)
    app.include_router(dashboard_router)
    app.include_router(settings_router)
    app.include_router(billing_router)
    app.include_router(notifications_router)
    app.include_router(search_router)
    app.include_router(people_router)
    app.include_router(suppression_router)
    app.include_router(analytics_router)
    app.include_router(audit_router)
    app.include_router(admin_router)
    app.include_router(agent_router)
    return app


app = create_app()
