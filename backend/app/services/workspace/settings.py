"""Workspace/org settings: serializers + data-access helpers (service layer).

HTTP endpoints + request/response schemas live in `app/api/settings.py`. The two response schemas
that the dump helpers construct (`ConnectionOut`, `DataProviderOut`) live here so the serializers
stay self-contained without a `services -> api` import.
"""

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ext.registry import ProviderSpec
from app.models import (
    Connection,
    ProviderCredential,
)


class ConnectionOut(BaseModel):
    id: str
    provider: str
    status: str
    seat_type: str
    user_email: str
    external_id: str | None


class DataProviderOut(BaseModel):
    key: str
    name: str
    live: bool  # has a working integration today
    docs_url: str
    configured: bool
    enabled: bool
    last4: str | None
    status: str


def _dump_connection(c: Connection, email: str) -> ConnectionOut:
    return ConnectionOut(
        id=c.id,
        provider=c.provider.value,
        status=c.status.value,
        seat_type=c.seat_type.value,
        user_email=email,
        external_id=c.external_id,
    )


def _dump_data_provider(spec: ProviderSpec, cred: ProviderCredential | None) -> DataProviderOut:
    return DataProviderOut(
        key=spec.key,
        name=spec.name,
        live=spec.live,
        docs_url=spec.docs_url,
        configured=cred is not None,
        enabled=cred.enabled if cred else False,
        last4=cred.last4 if cred else None,
        status=cred.status if cred else "not_configured",
    )


async def _owned_connection(session: AsyncSession, org_id: str, connection_id: str) -> Connection:
    conn = await session.get(Connection, connection_id)
    if conn is None or conn.organization_id != org_id:
        raise HTTPException(status_code=404, detail="connection not found")
    return conn


async def _provider_creds(session: AsyncSession, org_id: str) -> dict[str, ProviderCredential]:
    rows = (
        (
            await session.execute(
                select(ProviderCredential).where(ProviderCredential.organization_id == org_id)
            )
        )
        .scalars()
        .all()
    )
    return {r.provider: r for r in rows}
