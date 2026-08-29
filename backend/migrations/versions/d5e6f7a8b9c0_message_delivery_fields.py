"""message delivery fields (idempotency + inbound dedupe)

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-07-01 00:00:00.000000

Adds `message.idempotency_key` (provider-side dedupe of a retried outbound send) and
`message.provider_message_id` (the provider's own id for an inbound event, to drop redelivered
webhooks). Both are nullable; `provider_message_id` is indexed for the dedupe lookup.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("message", sa.Column("idempotency_key", sa.String(length=64), nullable=True))
    op.add_column("message", sa.Column("provider_message_id", sa.String(length=128), nullable=True))
    op.create_index(
        "ix_message_provider_message_id", "message", ["provider_message_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_message_provider_message_id", table_name="message")
    op.drop_column("message", "provider_message_id")
    op.drop_column("message", "idempotency_key")
