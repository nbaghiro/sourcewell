"""seat-connect attempts record which provider the wizard was opened for

Revision ID: b1e7c94d20af
Revises: 65a7a399f949
Create Date: 2026-08-31 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1e7c94d20af"
down_revision: str | None = "65a7a399f949"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The notify hop carries only an account id and the state token, so the attempt row is the
    # only record of which kind of seat is coming back — a Gmail mailbox must not be written as a
    # LinkedIn profile. Every attempt that exists today is LinkedIn (the wizard could not open for
    # anything else), which is also the server default for any row in flight mid-deploy.
    # Enums here are VARCHAR + CHECK, never a native Postgres type (see `core.db.sa_enum`).
    op.add_column(
        "login_attempt",
        sa.Column(
            "provider",
            sa.Enum(
                "gmail",
                "graph",
                "linkedin",
                name="connectionprovider",
                native_enum=False,
                length=32,
            ),
            nullable=False,
            server_default="linkedin",
        ),
    )


def downgrade() -> None:
    op.drop_column("login_attempt", "provider")
