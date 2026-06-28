"""organization stripe billing columns

Revision ID: f3b8c1da9e20
Revises: e7a2c9d4b1f0
Create Date: 2026-06-28 00:00:00.000000

Adds the Stripe subscription anchor to `organization`: the customer + subscription ids and the
current billing-period window. The webhook keeps these current; usage credits reset on the period.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3b8c1da9e20"
down_revision: str | None = "e7a2c9d4b1f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("organization", sa.Column("stripe_customer_id", sa.String(length=64), nullable=True))
    op.add_column(
        "organization", sa.Column("stripe_subscription_id", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "organization", sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "organization", sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("organization", "current_period_end")
    op.drop_column("organization", "current_period_start")
    op.drop_column("organization", "stripe_subscription_id")
    op.drop_column("organization", "stripe_customer_id")
