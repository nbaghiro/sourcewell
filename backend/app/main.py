"""FastAPI application factory."""

import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.agent.router import router as agent_router
from app.api.campaigns import router as campaigns_router
from app.api.enrollment import router as enrollment_router
from app.api.messaging import router as messaging_router
from app.api.runtime import router as admin_router
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging, logger
from app.insights.analytics import router as analytics_router
from app.insights.audit import router as audit_router
from app.insights.dashboard import router as dashboard_router
from app.people.contacts import router as contacts_router
from app.people.search import router as search_router
from app.people.sourcing.router import router as people_router
from app.people.suppression import router as suppression_router
from app.workspace.auth import router as auth_router
from app.workspace.notifications import router as notifications_router
from app.workspace.settings import router as settings_router
from app.workspace.tenancy import router as tenancy_router

# In-process fixed-window rate limiter (per client IP). Front with a shared store for multi-process.
_RL: dict[str, tuple[float, int]] = {}
_RL_WINDOW = 60.0
_RL_LIMIT = 600


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()
    app = FastAPI(title=settings.app_name)

    # The React app is a separate origin (:8900) and sends the session cookie, so allow
    # credentialed requests from it. Methods/headers are scoped (not "*") alongside credentials.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_url],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "X-User-Id",
            "X-Workspace-Id",
            "X-Signature",
        ],
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
