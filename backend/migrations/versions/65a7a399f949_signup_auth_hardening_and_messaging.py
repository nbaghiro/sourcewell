"""signup profile, auth hardening, InMail, and tenant-scoped inbound idempotency

Revision ID: 65a7a399f949
Revises: 60a4aaede531
Create Date: 2026-08-30 10:43:40.874795

Three groups of columns: the signup profile and verification state on `app_user`, the login
throttle and session epoch that back password sign-in, and the messaging fields — the campaign's
InMail opt-in, the per-message InMail stamp, and the inbound routing marker. The inbound dedupe
index moves from global to (workspace, provider_message_id), because provider ids are not unique
across accounts and a global constraint let one tenant's recorded id shadow another's.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "65a7a399f949"
down_revision: str | None = "60a4aaede531"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INBOUND_ONLY = sa.text("provider_message_id IS NOT NULL")


def upgrade() -> None:
    op.add_column("app_user", sa.Column("first_name", sa.String(length=100), nullable=True))
    op.add_column("app_user", sa.Column("last_name", sa.String(length=100), nullable=True))
    op.add_column("app_user", sa.Column("username", sa.String(length=50), nullable=True))
    op.add_column("app_user", sa.Column("avatar_url", sa.Text(), nullable=True))
    op.add_column(
        "app_user", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "app_user", sa.Column("profile_completed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "app_user",
        sa.Column("failed_login_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("app_user", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "app_user", sa.Column("session_epoch", sa.Integer(), server_default="0", nullable=False)
    )
    op.create_unique_constraint("app_user_username_key", "app_user", ["username"])

    op.add_column(
        "campaign",
        sa.Column("use_inmail", sa.Boolean(), server_default="false", nullable=False),
    )

    op.add_column(
        "message", sa.Column("is_inmail", sa.Boolean(), server_default="false", nullable=False)
    )
    op.add_column("message", sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column(
        "message",
        "provider_message_id",
        existing_type=sa.VARCHAR(length=128),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
    op.drop_index(
        "uq_message_provider_message_id", table_name="message", postgresql_where=_INBOUND_ONLY
    )
    op.create_index(
        "uq_message_workspace_provider_message_id",
        "message",
        ["workspace_id", "provider_message_id"],
        unique=True,
        postgresql_where=_INBOUND_ONLY,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_message_workspace_provider_message_id",
        table_name="message",
        postgresql_where=_INBOUND_ONLY,
    )
    op.create_index(
        "uq_message_provider_message_id",
        "message",
        ["provider_message_id"],
        unique=True,
        postgresql_where=_INBOUND_ONLY,
    )
    op.alter_column(
        "message",
        "provider_message_id",
        existing_type=sa.String(length=255),
        type_=sa.VARCHAR(length=128),
        existing_nullable=True,
    )
    op.drop_column("message", "processed_at")
    op.drop_column("message", "is_inmail")

    op.drop_column("campaign", "use_inmail")

    op.drop_constraint("app_user_username_key", "app_user", type_="unique")
    op.drop_column("app_user", "session_epoch")
    op.drop_column("app_user", "locked_until")
    op.drop_column("app_user", "failed_login_count")
    op.drop_column("app_user", "profile_completed_at")
    op.drop_column("app_user", "email_verified_at")
    op.drop_column("app_user", "avatar_url")
    op.drop_column("app_user", "username")
    op.drop_column("app_user", "last_name")
    op.drop_column("app_user", "first_name")
