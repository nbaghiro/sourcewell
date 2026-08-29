"""race-safe inbound dedupe: unique partial index on message.provider_message_id

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-07-01 00:00:00.000000

Replaces the plain index on `message.provider_message_id` with a partial UNIQUE index (only where
the value is non-null), so concurrent redeliveries of the same inbound webhook can't both insert.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_message_provider_message_id", table_name="message")
    op.create_index(
        "uq_message_provider_message_id",
        "message",
        ["provider_message_id"],
        unique=True,
        postgresql_where=sa.text("provider_message_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_message_provider_message_id", table_name="message")
    op.create_index(
        "ix_message_provider_message_id", "message", ["provider_message_id"], unique=False
    )
