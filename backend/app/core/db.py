"""Database engine, session, declarative base, and common model helpers."""

import enum
from collections.abc import AsyncIterator
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from ulid import ULID

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    """Generate a sortable ULID primary key."""
    return str(ULID())


def sa_enum(e: type[enum.StrEnum]) -> SAEnum:
    """A non-native (VARCHAR + CHECK) enum column storing member values."""
    return SAEnum(
        e, native_enum=False, length=32, values_callable=lambda x: [str(m.value) for m in x]
    )


class IdMixin:
    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


engine = create_async_engine(get_settings().database_url, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: a session committed on success, rolled back on error."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
