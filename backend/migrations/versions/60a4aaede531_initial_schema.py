"""initial schema

Revision ID: 60a4aaede531
Revises:
Create Date: 2026-08-29 20:26:20.126649

The whole schema as one baseline: partner -> organization -> workspace for tenancy, global
`app_user` identity joined to organizations by `membership` and to workspaces by `space_grant`,
and the contact / campaign / enrollment / message chain underneath a workspace.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "60a4aaede531"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "active", "invited", "disabled", name="userstatus", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("sso_subject", sa.String(length=255), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("notifications_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("sso_subject"),
    )
    op.create_table(
        "login_attempt",
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("account_id", sa.String(length=64), nullable=True),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_login_attempt_state"), "login_attempt", ["state"], unique=True)
    op.create_table(
        "partner",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("theme", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "suspended", name="partnerstatus", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "organization",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("partner_id", sa.String(length=26), nullable=True),
        sa.Column("plan", sa.String(length=50), nullable=False),
        sa.Column("data_region", sa.String(length=20), nullable=False),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("stripe_customer_id", sa.String(length=64), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(length=64), nullable=True),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["partner_id"], ["partner.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index(
        op.f("ix_organization_partner_id"), "organization", ["partner_id"], unique=False
    )
    op.create_table(
        "audit_event",
        sa.Column("organization_id", sa.String(length=26), nullable=False),
        sa.Column("workspace_id", sa.String(length=26), nullable=True),
        sa.Column("actor_user_id", sa.String(length=26), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=True),
        sa.Column("target_id", sa.String(length=26), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_event_action"), "audit_event", ["action"], unique=False)
    op.create_index(
        op.f("ix_audit_event_organization_id"), "audit_event", ["organization_id"], unique=False
    )
    op.create_index(
        op.f("ix_audit_event_workspace_id"), "audit_event", ["workspace_id"], unique=False
    )
    op.create_table(
        "connection",
        sa.Column("organization_id", sa.String(length=26), nullable=False),
        sa.Column("user_id", sa.String(length=26), nullable=False),
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
        ),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column(
            "seat_type",
            sa.Enum(
                "email",
                "basic",
                "premium",
                "sales_nav",
                "recruiter",
                name="seattype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "ok",
                "needs_reauth",
                "paused",
                name="connectionstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("token_ref", sa.String(length=255), nullable=True),
        sa.Column("daily_sent", sa.Integer(), nullable=False),
        sa.Column("warmup_stage", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_connection_organization_id"), "connection", ["organization_id"], unique=False
    )
    op.create_index(op.f("ix_connection_user_id"), "connection", ["user_id"], unique=False)
    op.create_table(
        "membership",
        sa.Column("user_id", sa.String(length=26), nullable=False),
        sa.Column("organization_id", sa.String(length=26), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "org_admin",
                "member",
                "compliance",
                name="membershiprole",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "organization_id", name="uq_membership_user_org"),
    )
    op.create_index(
        op.f("ix_membership_organization_id"), "membership", ["organization_id"], unique=False
    )
    op.create_index(op.f("ix_membership_user_id"), "membership", ["user_id"], unique=False)
    op.create_table(
        "memory",
        sa.Column("organization_id", sa.String(length=26), nullable=False),
        sa.Column(
            "scope",
            sa.Enum(
                "workspace",
                "vertical",
                "campaign",
                "contact",
                name="memoryscope",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by_run", sa.String(length=26), nullable=True),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_memory_organization_id"), "memory", ["organization_id"], unique=False)
    op.create_index(
        "ix_memory_recall", "memory", ["organization_id", "scope", "scope_id"], unique=False
    )
    op.create_table(
        "provider_credential",
        sa.Column("organization_id", sa.String(length=26), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("last4", sa.String(length=8), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "provider", name="uq_provider_credential_org_provider"
        ),
    )
    op.create_index(
        op.f("ix_provider_credential_organization_id"),
        "provider_credential",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "provider_usage",
        sa.Column("organization_id", sa.String(length=26), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "provider", "kind", "day", name="uq_provider_usage"),
    )
    op.create_index(
        op.f("ix_provider_usage_organization_id"),
        "provider_usage",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "workspace",
        sa.Column("organization_id", sa.String(length=26), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "client", "department", "team", name="workspacekind", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("active", "archived", name="workspacestatus", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_workspace_organization_id"), "workspace", ["organization_id"], unique=False
    )
    op.create_table(
        "campaign",
        sa.Column("workspace_id", sa.String(length=26), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "active",
                "paused",
                "done",
                name="campaignstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("from_email", sa.String(length=320), nullable=True),
        sa.Column("seat_id", sa.String(length=26), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=26), nullable=True),
        sa.Column("criteria", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sequence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("objective", sa.Text(), nullable=True),
        sa.Column(
            "autonomy_level",
            sa.Enum(
                "manual", "assisted", "full", name="autonomylevel", native_enum=False, length=32
            ),
            server_default="assisted",
            nullable=False,
        ),
        sa.Column("constraints", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "authored_by",
            sa.Enum("human", "agent", name="authorship", native_enum=False, length=32),
            server_default="human",
            nullable=False,
        ),
        sa.Column("field_owners", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("next_source_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("brief_source", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["seat_id"], ["connection.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_campaign_created_by_user_id"), "campaign", ["created_by_user_id"], unique=False
    )
    op.create_index(
        op.f("ix_campaign_next_source_at"), "campaign", ["next_source_at"], unique=False
    )
    op.create_index(op.f("ix_campaign_seat_id"), "campaign", ["seat_id"], unique=False)
    op.create_index(op.f("ix_campaign_workspace_id"), "campaign", ["workspace_id"], unique=False)
    op.create_table(
        "contact",
        sa.Column("workspace_id", sa.String(length=26), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("company", sa.String(length=200), nullable=True),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("email_status", sa.String(length=20), nullable=False),
        sa.Column("linkedin_url", sa.String(length=500), nullable=True),
        sa.Column("avatar_url", sa.String(length=500), nullable=True),
        sa.Column("skills", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("company_size", sa.String(length=50), nullable=True),
        sa.Column("industry", sa.String(length=100), nullable=True),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_contact_workspace_id"), "contact", ["workspace_id"], unique=False)
    op.create_table(
        "space_grant",
        sa.Column("user_id", sa.String(length=26), nullable=False),
        sa.Column("workspace_id", sa.String(length=26), nullable=False),
        sa.Column(
            "role",
            sa.Enum("admin", "member", name="spacerole", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "workspace_id", name="uq_space_grant_user_workspace"),
    )
    op.create_index(op.f("ix_space_grant_user_id"), "space_grant", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_space_grant_workspace_id"), "space_grant", ["workspace_id"], unique=False
    )
    op.create_table(
        "suppression",
        sa.Column("organization_id", sa.String(length=26), nullable=False),
        sa.Column("workspace_id", sa.String(length=26), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column(
            "reason",
            sa.Enum(
                "opted_out",
                "unsubscribed",
                "bounced",
                "manual",
                name="suppressionreason",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("contact_id", sa.String(length=26), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_suppression_email"), "suppression", ["email"], unique=False)
    op.create_index(
        op.f("ix_suppression_organization_id"), "suppression", ["organization_id"], unique=False
    )
    op.create_index(
        op.f("ix_suppression_workspace_id"), "suppression", ["workspace_id"], unique=False
    )
    op.create_index(
        "uq_suppression_org_email",
        "suppression",
        ["organization_id", "email"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NULL"),
    )
    op.create_index(
        "uq_suppression_workspace_email",
        "suppression",
        ["workspace_id", "email"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NOT NULL"),
    )
    op.create_table(
        "agent_run",
        sa.Column("workspace_id", sa.String(length=26), nullable=False),
        sa.Column("campaign_id", sa.String(length=26), nullable=True),
        sa.Column(
            "role",
            sa.Enum(
                "strategy", "sourcing", "outreach", name="agentrole", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaign.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_run_campaign_id"), "agent_run", ["campaign_id"], unique=False)
    op.create_index(op.f("ix_agent_run_workspace_id"), "agent_run", ["workspace_id"], unique=False)
    op.create_table(
        "enrollment",
        sa.Column("workspace_id", sa.String(length=26), nullable=False),
        sa.Column("campaign_id", sa.String(length=26), nullable=False),
        sa.Column("contact_id", sa.String(length=26), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "proposed",
                "active",
                "awaiting_approval",
                "scheduled",
                "awaiting_reply",
                "handed_off",
                "opted_out",
                "completed",
                name="enrollmentstate",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("score_rationale", sa.Text(), nullable=True),
        sa.Column("current_step", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reply_pending", sa.Boolean(), nullable=False),
        sa.Column("outcome", sa.String(length=50), nullable=True),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_action", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("signals", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "relationship_status",
            sa.Enum(
                "active",
                "parked",
                "nurture",
                "handed_off",
                "declined",
                name="relationshipstatus",
                native_enum=False,
                length=32,
            ),
            server_default="active",
            nullable=False,
        ),
        sa.Column("park_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaign.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["contact.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", "contact_id", name="uq_enrollment_campaign_contact"),
    )
    op.create_index(op.f("ix_enrollment_campaign_id"), "enrollment", ["campaign_id"], unique=False)
    op.create_index(op.f("ix_enrollment_contact_id"), "enrollment", ["contact_id"], unique=False)
    op.create_index(op.f("ix_enrollment_next_run_at"), "enrollment", ["next_run_at"], unique=False)
    op.create_index(op.f("ix_enrollment_state"), "enrollment", ["state"], unique=False)
    op.create_index(
        op.f("ix_enrollment_workspace_id"), "enrollment", ["workspace_id"], unique=False
    )
    op.create_table(
        "agent_step",
        sa.Column("run_id", sa.String(length=26), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=True),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_step_run_id"), "agent_step", ["run_id"], unique=False)
    op.create_table(
        "message",
        sa.Column("workspace_id", sa.String(length=26), nullable=False),
        sa.Column("enrollment_id", sa.String(length=26), nullable=False),
        sa.Column(
            "direction",
            sa.Enum("outbound", "inbound", name="messagedirection", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column(
            "channel",
            sa.Enum("email", "linkedin", name="channel", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "approved",
                "sent",
                "failed",
                "received",
                name="messagestatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("account_id", sa.String(length=64), nullable=True),
        sa.Column("origin", sa.String(length=16), server_default="ai", nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("provider_message_id", sa.String(length=128), nullable=True),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["enrollment_id"], ["enrollment.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_message_enrollment_id"), "message", ["enrollment_id"], unique=False)
    op.create_index(op.f("ix_message_external_id"), "message", ["external_id"], unique=False)
    op.create_index(op.f("ix_message_status"), "message", ["status"], unique=False)
    op.create_index(op.f("ix_message_workspace_id"), "message", ["workspace_id"], unique=False)
    op.create_index(
        "uq_message_provider_message_id",
        "message",
        ["provider_message_id"],
        unique=True,
        postgresql_where=sa.text("provider_message_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_message_provider_message_id",
        table_name="message",
        postgresql_where=sa.text("provider_message_id IS NOT NULL"),
    )
    op.drop_index(op.f("ix_message_workspace_id"), table_name="message")
    op.drop_index(op.f("ix_message_status"), table_name="message")
    op.drop_index(op.f("ix_message_external_id"), table_name="message")
    op.drop_index(op.f("ix_message_enrollment_id"), table_name="message")
    op.drop_table("message")
    op.drop_index(op.f("ix_agent_step_run_id"), table_name="agent_step")
    op.drop_table("agent_step")
    op.drop_index(op.f("ix_enrollment_workspace_id"), table_name="enrollment")
    op.drop_index(op.f("ix_enrollment_state"), table_name="enrollment")
    op.drop_index(op.f("ix_enrollment_next_run_at"), table_name="enrollment")
    op.drop_index(op.f("ix_enrollment_contact_id"), table_name="enrollment")
    op.drop_index(op.f("ix_enrollment_campaign_id"), table_name="enrollment")
    op.drop_table("enrollment")
    op.drop_index(op.f("ix_agent_run_workspace_id"), table_name="agent_run")
    op.drop_index(op.f("ix_agent_run_campaign_id"), table_name="agent_run")
    op.drop_table("agent_run")
    op.drop_index(
        "uq_suppression_workspace_email",
        table_name="suppression",
        postgresql_where=sa.text("workspace_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_suppression_org_email",
        table_name="suppression",
        postgresql_where=sa.text("workspace_id IS NULL"),
    )
    op.drop_index(op.f("ix_suppression_workspace_id"), table_name="suppression")
    op.drop_index(op.f("ix_suppression_organization_id"), table_name="suppression")
    op.drop_index(op.f("ix_suppression_email"), table_name="suppression")
    op.drop_table("suppression")
    op.drop_index(op.f("ix_space_grant_workspace_id"), table_name="space_grant")
    op.drop_index(op.f("ix_space_grant_user_id"), table_name="space_grant")
    op.drop_table("space_grant")
    op.drop_index(op.f("ix_contact_workspace_id"), table_name="contact")
    op.drop_table("contact")
    op.drop_index(op.f("ix_campaign_workspace_id"), table_name="campaign")
    op.drop_index(op.f("ix_campaign_seat_id"), table_name="campaign")
    op.drop_index(op.f("ix_campaign_next_source_at"), table_name="campaign")
    op.drop_index(op.f("ix_campaign_created_by_user_id"), table_name="campaign")
    op.drop_table("campaign")
    op.drop_index(op.f("ix_workspace_organization_id"), table_name="workspace")
    op.drop_table("workspace")
    op.drop_index(op.f("ix_provider_usage_organization_id"), table_name="provider_usage")
    op.drop_table("provider_usage")
    op.drop_index(op.f("ix_provider_credential_organization_id"), table_name="provider_credential")
    op.drop_table("provider_credential")
    op.drop_index("ix_memory_recall", table_name="memory")
    op.drop_index(op.f("ix_memory_organization_id"), table_name="memory")
    op.drop_table("memory")
    op.drop_index(op.f("ix_membership_user_id"), table_name="membership")
    op.drop_index(op.f("ix_membership_organization_id"), table_name="membership")
    op.drop_table("membership")
    op.drop_index(op.f("ix_connection_user_id"), table_name="connection")
    op.drop_index(op.f("ix_connection_organization_id"), table_name="connection")
    op.drop_table("connection")
    op.drop_index(op.f("ix_audit_event_workspace_id"), table_name="audit_event")
    op.drop_index(op.f("ix_audit_event_organization_id"), table_name="audit_event")
    op.drop_index(op.f("ix_audit_event_action"), table_name="audit_event")
    op.drop_table("audit_event")
    op.drop_index(op.f("ix_organization_partner_id"), table_name="organization")
    op.drop_table("organization")
    op.drop_table("partner")
    op.drop_index(op.f("ix_login_attempt_state"), table_name="login_attempt")
    op.drop_table("login_attempt")
    op.drop_table("app_user")
