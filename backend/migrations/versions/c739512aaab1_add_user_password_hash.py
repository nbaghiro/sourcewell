"""add user password_hash

Revision ID: c739512aaab1
Revises: beb2da1b519f
Create Date: 2026-06-25 20:17:58.456656

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c739512aaab1"
down_revision: str | None = "beb2da1b519f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_user", sa.Column("password_hash", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("app_user", "password_hash")
