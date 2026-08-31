"""conversations can exist without a campaign (direct 1:1 messaging)

Revision ID: c8b2f60e4a17
Revises: b1e7c94d20af
Create Date: 2026-08-31 18:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8b2f60e4a17"
down_revision: str | None = "b1e7c94d20af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A recruiter messaging one person directly has no sequence to run, so requiring a campaign
    # meant inventing a fake one to hold the thread. Widening is safe: every existing row has a
    # campaign and keeps it.
    op.alter_column("enrollment", "campaign_id", existing_type=sa.String(26), nullable=True)


def downgrade() -> None:
    # Direct conversations have no campaign to fall back to, so they go rather than block the
    # narrowing — the messages are still on the contact's timeline.
    op.execute("DELETE FROM enrollment WHERE campaign_id IS NULL")
    op.alter_column("enrollment", "campaign_id", existing_type=sa.String(26), nullable=False)
