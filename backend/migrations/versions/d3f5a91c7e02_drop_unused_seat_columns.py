"""drop the seat/attempt columns nothing reads

Revision ID: d3f5a91c7e02
Revises: c8b2f60e4a17
Create Date: 2026-09-01 09:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3f5a91c7e02"
down_revision: str | None = "c8b2f60e4a17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `connection` carried three columns from the original sketch of per-seat sending that the
    # product never grew into: OAuth tokens live in Unipile (we hold an `external_id`, never a
    # token), and the daily/warmup throttles are enforced by the governor against sent messages,
    # not by a counter on the seat. None of the three was ever written.
    op.drop_column("connection", "token_ref")
    op.drop_column("connection", "daily_sent")
    op.drop_column("connection", "warmup_stage")
    # `login_attempt` is a correlation row for one hosted-auth round-trip, not a state machine:
    # the seat it produces lands on `connection`, and an abandoned attempt is aged out by TTL. Its
    # `status` and `account_id` were written and never read back.
    op.drop_column("login_attempt", "status")
    op.drop_column("login_attempt", "account_id")


def downgrade() -> None:
    op.add_column("login_attempt", sa.Column("account_id", sa.String(length=64), nullable=True))
    op.add_column(
        "login_attempt",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
    )
    op.add_column(
        "connection", sa.Column("warmup_stage", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "connection", sa.Column("daily_sent", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("connection", sa.Column("token_ref", sa.String(length=255), nullable=True))
