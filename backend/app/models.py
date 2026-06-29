"""All SQLAlchemy ORM model classes + the StrEnums used as DB column types.

This single module owns the schema so `Base.metadata` is complete for Alembic + tests.
Nothing else defines ORM models.
"""

import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, TimestampMixin, sa_enum
from app.core.types import JsonList, JsonObject

# --- Enums (StrEnums used as column types) -----------------------------------


class WorkspaceKind(enum.StrEnum):
    client = "client"
    department = "department"
    team = "team"


class WorkspaceStatus(enum.StrEnum):
    active = "active"
    archived = "archived"


class UserStatus(enum.StrEnum):
    active = "active"
    invited = "invited"
    disabled = "disabled"


class MembershipScope(enum.StrEnum):
    organization = "organization"
    workspace = "workspace"


class MembershipRole(enum.StrEnum):
    org_admin = "org_admin"
    workspace_admin = "workspace_admin"
    member = "member"
    compliance = "compliance"


class ConnectionProvider(enum.StrEnum):
    gmail = "gmail"
    graph = "graph"
    linkedin = "linkedin"


class SeatType(enum.StrEnum):
    email = "email"
    basic = "basic"
    premium = "premium"
    sales_nav = "sales_nav"
    recruiter = "recruiter"


class ConnectionStatus(enum.StrEnum):
    ok = "ok"
    needs_reauth = "needs_reauth"
    paused = "paused"


class CampaignStatus(enum.StrEnum):
    draft = "draft"
    active = "active"
    paused = "paused"
    done = "done"


class AutonomyMode(enum.StrEnum):
    approve_each = "approve_each"
    auto = "auto"


class AutonomyLevel(enum.StrEnum):
    """Campaign-level autonomy — drives all three gates (campaign / candidate / message)."""

    manual = "manual"  # approve every gate
    assisted = "assisted"  # approve the campaign once, auto the rest
    full = "full"  # auto everywhere; human only at the warm-reply handoff


class Authorship(enum.StrEnum):
    """Who owns a campaign or one of its strategy sections (provenance)."""

    human = "human"
    agent = "agent"


class RelationshipStatus(enum.StrEnum):
    """The agent's living relationship with a candidate (beyond the send state machine)."""

    active = "active"
    parked = "parked"  # "ask me later" — re-approach at park_until
    nurture = "nurture"
    handed_off = "handed_off"
    declined = "declined"


class MemoryScope(enum.StrEnum):
    """Scope an accumulated learning is keyed by (recall filters on scope + scope_id)."""

    workspace = "workspace"
    vertical = "vertical"
    campaign = "campaign"
    contact = "contact"


class AgentRole(enum.StrEnum):
    """Which agent produced a run."""

    strategy = "strategy"
    sourcing = "sourcing"
    outreach = "outreach"


class EnrollmentState(enum.StrEnum):
    proposed = "proposed"  # ranked; awaiting human approval to pursue
    active = "active"  # approved; ready to draft the next touchpoint
    awaiting_approval = "awaiting_approval"  # a draft message awaits human approval
    scheduled = "scheduled"  # message approved; ready to send (governor-gated)
    awaiting_reply = "awaiting_reply"  # sent; waiting for reply or the step delay
    handed_off = "handed_off"  # positive reply; a human took over (terminal)
    opted_out = "opted_out"  # opted out / not interested (terminal)
    completed = "completed"  # sequence exhausted, no reply (terminal)


TERMINAL = {EnrollmentState.handed_off, EnrollmentState.opted_out, EnrollmentState.completed}


class MessageDirection(enum.StrEnum):
    outbound = "outbound"
    inbound = "inbound"


class Channel(enum.StrEnum):
    email = "email"
    linkedin = "linkedin"


class MessageStatus(enum.StrEnum):
    draft = "draft"
    approved = "approved"
    sent = "sent"
    failed = "failed"
    received = "received"


class SuppressionReason(enum.StrEnum):
    opted_out = "opted_out"
    unsubscribed = "unsubscribed"
    bounced = "bounced"
    manual = "manual"


# --- Tenancy: organization -> workspace, user, membership, connection --------


class Organization(IdMixin, TimestampMixin, Base):
    __tablename__ = "organization"

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    plan: Mapped[str] = mapped_column(String(50), default="free")
    data_region: Mapped[str] = mapped_column(String(20), default="us")
    # Stripe billing (the webhook is the source of truth; blank until a subscription is created).
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Workspace(IdMixin, TimestampMixin, Base):
    __tablename__ = "workspace"

    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[WorkspaceKind] = mapped_column(
        sa_enum(WorkspaceKind), default=WorkspaceKind.client
    )
    status: Mapped[WorkspaceStatus] = mapped_column(
        sa_enum(WorkspaceStatus), default=WorkspaceStatus.active
    )
    brand_voice: Mapped[str | None] = mapped_column(String, nullable=True)
    settings: Mapped[JsonObject] = mapped_column(JSONB, default=dict)
    # Industry pack pointer; prompt packs are hardcoded in `app/agents/prompts.py`.
    vertical: Mapped[str] = mapped_column(
        String(50), default="recruiting", server_default="recruiting"
    )


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "app_user"  # "user" is reserved in Postgres

    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[UserStatus] = mapped_column(sa_enum(UserStatus), default=UserStatus.active)
    # The federated identity key: the LinkedIn member_urn from Unipile hosted-auth sign-in.
    sso_subject: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    # Local password hash (scrypt) for the email/password login; null for SSO-provisioned users.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notifications_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (UniqueConstraint("organization_id", "email", name="uq_user_org_email"),)


class LoginAttempt(IdMixin, TimestampMixin, Base):
    """A pending LinkedIn (Unipile hosted-auth) sign-in. Correlates the server-side notify webhook
    with the browser redirect via a one-time `state` token; consumed when the session is minted.
    """

    __tablename__ = "login_attempt"

    state: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | ready
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Membership(IdMixin, TimestampMixin, Base):
    __tablename__ = "membership"

    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[MembershipScope] = mapped_column(sa_enum(MembershipScope))
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspace.id", ondelete="CASCADE"), nullable=True, index=True
    )
    role: Mapped[MembershipRole] = mapped_column(sa_enum(MembershipRole))

    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", name="uq_membership_user_workspace"),
        Index(
            "uq_membership_user_org",
            "user_id",
            unique=True,
            postgresql_where=text("scope = 'organization'"),
        ),
    )


class Connection(IdMixin, TimestampMixin, Base):
    __tablename__ = "connection"

    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    provider: Mapped[ConnectionProvider] = mapped_column(sa_enum(ConnectionProvider))
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    seat_type: Mapped[SeatType] = mapped_column(sa_enum(SeatType), default=SeatType.email)
    status: Mapped[ConnectionStatus] = mapped_column(
        sa_enum(ConnectionStatus), default=ConnectionStatus.ok
    )
    capabilities: Mapped[JsonObject] = mapped_column(JSONB, default=dict)
    token_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    daily_sent: Mapped[int] = mapped_column(default=0)
    warmup_stage: Mapped[int] = mapped_column(default=0)


class ProviderCredential(IdMixin, TimestampMixin, Base):
    """A BYO API key for a Rail B people-data provider (PDL, Apollo, ...).

    Org-scoped, one row per provider. The key is sealed at rest (see `app.core.crypto`); only
    `last4` + status are ever returned to the client.
    """

    __tablename__ = "provider_credential"

    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    secret: Mapped[str] = mapped_column(Text)
    last4: Mapped[str] = mapped_column(String(8), default="")
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    status: Mapped[str] = mapped_column(
        String(20), default="unverified"
    )  # unverified | ok | invalid
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "provider", name="uq_provider_credential_org_provider"),
    )


# --- Contact -----------------------------------------------------------------


class Contact(IdMixin, TimestampMixin, Base):
    __tablename__ = "contact"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspace.id", ondelete="CASCADE"), index=True
    )
    full_name: Mapped[str] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # Deliverability: unverified | valid | risky | invalid | unknown
    email_status: Mapped[str] = mapped_column(String(20), default="unverified")
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    skills: Mapped[list[str]] = mapped_column(JSONB, default=list)
    source: Mapped[str] = mapped_column(String(50), default="manual")
    # CRM enrichment
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    company_size: Mapped[str | None] = mapped_column(String(50), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Vertical-specific fields keep the core columns generic.
    attributes: Mapped[JsonObject] = mapped_column(JSONB, default=dict)


# --- Campaign ----------------------------------------------------------------


class Campaign(IdMixin, TimestampMixin, Base):
    __tablename__ = "campaign"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspace.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[CampaignStatus] = mapped_column(
        sa_enum(CampaignStatus), default=CampaignStatus.draft
    )
    autonomy_mode: Mapped[AutonomyMode] = mapped_column(
        sa_enum(AutonomyMode), default=AutonomyMode.approve_each
    )
    from_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # criteria: {"titles": [...], "skills": [...]}
    criteria: Mapped[JsonObject] = mapped_column(JSONB, default=dict)
    # sequence: [{"channel": "email", "delay_days": 0, "subject": "...", "body": "..."}]
    sequence: Mapped[JsonList] = mapped_column(JSONB, default=list)

    # --- Agent-native fields -------------------------------------------------
    # The natural-language brief that drives AI design (blank for a pure-manual campaign).
    objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Generalizes autonomy_mode across all three gates (campaign / candidate / message).
    autonomy_level: Mapped[AutonomyLevel] = mapped_column(
        sa_enum(AutonomyLevel), default=AutonomyLevel.assisted, server_default="assisted"
    )
    # Standing guardrails: do-not-contact, voice, send caps, budget, handoff rules.
    constraints: Mapped[JsonObject] = mapped_column(JSONB, default=dict)
    # Who created the campaign; seeds the initial field ownership.
    authored_by: Mapped[Authorship] = mapped_column(
        sa_enum(Authorship), default=Authorship.human, server_default="human"
    )
    # Per-section provenance: {"audience": "agent", "messaging": "human", ...}. Agents write
    # only agent-owned sections; a human edit pins a section to "human".
    field_owners: Mapped[JsonObject] = mapped_column(JSONB, default=dict)
    # Self-clocking sourcing cadence (the Sourcing agent's source_due tick).
    next_source_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # The original brief kept for re-reference / regeneration: {origin, raw_text, ref}.
    brief_source: Mapped[JsonObject] = mapped_column(JSONB, default=dict)


# --- Enrollment --------------------------------------------------------------


class Enrollment(IdMixin, TimestampMixin, Base):
    __tablename__ = "enrollment"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspace.id", ondelete="CASCADE"), index=True
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaign.id", ondelete="CASCADE"), index=True
    )
    contact_id: Mapped[str] = mapped_column(
        ForeignKey("contact.id", ondelete="CASCADE"), index=True
    )

    state: Mapped[EnrollmentState] = mapped_column(
        sa_enum(EnrollmentState), default=EnrollmentState.proposed, index=True
    )
    score: Mapped[int] = mapped_column(Integer, default=0)
    score_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    reply_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    outcome: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Agent-native fields: the candidate's living, per-person journey -----
    # The agent-decided next-best-action (replaces a rigid sequence step).
    next_action: Mapped[JsonObject] = mapped_column(JSONB, default=dict)
    # Observed engagement: opens, clicks, profile activity, reply sentiment.
    signals: Mapped[JsonObject] = mapped_column(JSONB, default=dict)
    relationship_status: Mapped[RelationshipStatus] = mapped_column(
        sa_enum(RelationshipStatus),
        default=RelationshipStatus.active,
        server_default="active",
    )
    park_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("campaign_id", "contact_id", name="uq_enrollment_campaign_contact"),
    )


# --- Message -----------------------------------------------------------------


class Message(IdMixin, TimestampMixin, Base):
    __tablename__ = "message"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspace.id", ondelete="CASCADE"), index=True
    )
    enrollment_id: Mapped[str] = mapped_column(
        ForeignKey("enrollment.id", ondelete="CASCADE"), index=True
    )
    direction: Mapped[MessageDirection] = mapped_column(sa_enum(MessageDirection))
    channel: Mapped[Channel] = mapped_column(sa_enum(Channel), default=Channel.email)
    status: Mapped[MessageStatus] = mapped_column(
        sa_enum(MessageStatus), default=MessageStatus.draft, index=True
    )
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When a draft is queued to auto-send (the "next touchpoint" preview on scheduled enrollments).
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Send attempts so far (for retry/backoff on transient send failures).
    attempts: Mapped[int] = mapped_column(default=0)
    # Provider thread/chat id (maps an inbound reply back to this thread) + the seat that sent it.
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


# --- Suppression -------------------------------------------------------------


class Suppression(IdMixin, TimestampMixin, Base):
    __tablename__ = "suppression"

    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320), index=True)
    reason: Mapped[SuppressionReason] = mapped_column(
        sa_enum(SuppressionReason), default=SuppressionReason.manual
    )
    contact_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "email", name="uq_suppression_org_email"),
    )


# --- Audit -------------------------------------------------------------------


class AuditEvent(IdMixin, TimestampMixin, Base):
    __tablename__ = "audit_event"

    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(String(26), nullable=True, index=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")


# --- Provider usage ----------------------------------------------------------


class ProviderUsage(IdMixin, TimestampMixin, Base):
    __tablename__ = "provider_usage"

    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16))  # search | enrich | verify | import
    day: Mapped[date] = mapped_column(Date)
    count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("organization_id", "provider", "kind", "day", name="uq_provider_usage"),
    )


# --- Agent memory + run traces -----------------------------------------------


class Memory(IdMixin, TimestampMixin, Base):
    """A compounding learning the agents read (by scope) and write.

    Recall is keyed (organization + scope + scope_id) for now; the nullable `embedding`
    column is the seam for vector recall later (no pgvector dependency yet).
    """

    __tablename__ = "memory"

    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[MemoryScope] = mapped_column(sa_enum(MemoryScope))
    # The keyed entity: a workspace/campaign/contact id, or the vertical name.
    scope_id: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    # Vector seam — populated only when vector recall is turned on.
    embedding: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    meta: Mapped[JsonObject] = mapped_column(JSONB, default=dict)
    created_by_run: Mapped[str | None] = mapped_column(String(26), nullable=True)

    __table_args__ = (Index("ix_memory_recall", "organization_id", "scope", "scope_id"),)


class AgentRun(IdMixin, TimestampMixin, Base):
    """One bounded agent run — the trace that powers the activity feed + budgets."""

    __tablename__ = "agent_run"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspace.id", ondelete="CASCADE"), index=True
    )
    campaign_id: Mapped[str | None] = mapped_column(
        ForeignKey("campaign.id", ondelete="CASCADE"), nullable=True, index=True
    )
    role: Mapped[AgentRole] = mapped_column(sa_enum(AgentRole))
    # cold_start | review | chat | source_due | reply
    trigger: Mapped[str] = mapped_column(String(32))
    # running | done | error | over_budget
    status: Mapped[str] = mapped_column(String(20), default="running")
    summary: Mapped[str] = mapped_column(Text, default="")
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentStep(IdMixin, TimestampMixin, Base):
    """One step within an AgentRun (a thought, a tool call, or a tool result)."""

    __tablename__ = "agent_step"

    run_id: Mapped[str] = mapped_column(ForeignKey("agent_run.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))  # thought | tool_call | result
    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content: Mapped[JsonObject] = mapped_column(JSONB, default=dict)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
