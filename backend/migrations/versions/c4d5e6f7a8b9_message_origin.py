"""message origin (ai vs human authored)

Revision ID: c4d5e6f7a8b9
Revises: f3b8c1da9e20
Create Date: 2026-07-01 00:00:00.000000

Adds `message.origin` — who composed an outbound message: "ai" (agent-drafted) or "human" (typed by
the user). Defaults to "ai" since most outbound is agent-generated; manual replies set "human".
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: str | None = "f3b8c1da9e20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "message",
        sa.Column("origin", sa.String(length=16), nullable=False, server_default="ai"),
    )


def downgrade() -> None:
    op.drop_column("message", "origin")
