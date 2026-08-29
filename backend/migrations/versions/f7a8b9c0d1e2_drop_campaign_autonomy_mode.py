"""drop campaign.autonomy_mode — autonomy_level is the single autonomy field

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-08-29 00:00:00.000000

`autonomy_mode` (approve_each|auto) predates `autonomy_level` (manual|assisted|full) and had to be
hand-reconciled with it on every create/update. Every gate reads `autonomy_level`, so the legacy
column goes. The downgrade recreates it from `autonomy_level` (full -> auto, else approve_each).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: str | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("campaign", "autonomy_mode")


def downgrade() -> None:
    op.add_column(
        "campaign",
        sa.Column(
            "autonomy_mode",
            sa.String(length=20),
            nullable=False,
            server_default="approve_each",
        ),
    )
    op.execute("UPDATE campaign SET autonomy_mode = 'auto' WHERE autonomy_level = 'full'")
    op.create_check_constraint(
        "ck_campaign_autonomy_mode",
        "campaign",
        "autonomy_mode IN ('approve_each', 'auto')",
    )
