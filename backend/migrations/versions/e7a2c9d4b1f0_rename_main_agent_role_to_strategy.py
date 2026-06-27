"""rename the main agent role to strategy

Revision ID: e7a2c9d4b1f0
Revises: c739512aaab1
Create Date: 2026-06-27 00:00:00.000000

`agent_run.role` is a plain varchar (sa_enum is native_enum=False), so this is a data-only
migration — no type change. Existing "main" runs become "strategy".
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e7a2c9d4b1f0"
down_revision: str | None = "c739512aaab1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE agent_run SET role = 'strategy' WHERE role = 'main'")


def downgrade() -> None:
    op.execute("UPDATE agent_run SET role = 'main' WHERE role = 'strategy'")
