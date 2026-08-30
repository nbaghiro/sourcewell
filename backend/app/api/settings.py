"""Workspace/org settings HTTP endpoints: members, connections, data providers, export.

Serializers + data-access helpers live in `app.services.workspace.settings`.
"""

import hmac
import json
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, select

from app.agents.prompts import resolve_labels
from app.api.context import ContextDep, SessionDep
from app.api.guards import require_org_admin, require_workspace
from app.api.labels import LabelPack
from app.core import policy
from app.core.config import get_settings
from app.core.crypto import seal, unseal
from app.core.types import JsonObject
from app.ext.registry import PROVIDER_CATALOG, build_one
from app.models import (
    Campaign,
    Connection,
    ConnectionStatus,
    Contact,
    Enrollment,
    Membership,
    MembershipRole,
    Message,
    Organization,
    ProviderCredential,
    SpaceGrant,
    User,
    UserStatus,
    Workspace,
    WorkspaceKind,
)
from app.services.billing import subscriptions
from app.services.billing.credits import credit_status
from app.services.insights import audit
from app.services.workspace import auth as auth_service
from app.services.workspace import connections as connections_service
from app.services.workspace.settings import (
    ConnectionOut,
    DataProviderOut,
    _dump_connection,
    _dump_data_provider,
    _owned_connection,
    _provider_creds,
)

router = APIRouter(prefix="/settings", tags=["settings"])


class UsageOut(BaseModel):
    plan: str
    used: int
    allowance: int
    over: bool
    pct: int
    period_start: datetime
    breakdown: dict[str, int]  # emails / linkedin_dms / inmails / sourced counts this period
    billing_enabled: bool  # whether Stripe is configured (gates the upgrade / portal UI)


@router.get("/usage", response_model=UsageOut)
async def account_usage(ctx: ContextDep, session: SessionDep) -> UsageOut:
    """The account's pooled monthly credit usage vs. its plan allowance — a soft limit (overage is
    allowed). Powers the usage meter + the over-allowance warning."""
    org = await session.get(Organization, ctx.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")
    st = await credit_status(
        session,
        organization_id=org.id,
        plan=org.plan,
        now=datetime.now(UTC),
        period_start_at=org.current_period_start,
    )
    return UsageOut(
        plan=org.plan,
        used=st.used,
        allowance=st.allowance,
        over=st.over,
        pct=st.pct,
        period_start=st.period_start,
        breakdown={
            "emails": st.emails,
            "inmails": st.inmails,
            "sourced": st.sourced,
        },
        billing_enabled=subscriptions.is_enabled(),
    )


class MemberOut(BaseModel):
    id: str
    name: str
    email: str
    role: MembershipRole
    workspace_ids: list[str]  # explicit grants; empty for the org-wide roles


class WorkspaceSettingsOut(BaseModel):
    id: str
    name: str
    kind: WorkspaceKind
    labels: LabelPack
    # The whole policy chain flattened: platform → partner → org → workspace.
    settings: JsonObject
    # Just this workspace's own overrides, so the UI can tell "inherited" from "set here".
    overrides: JsonObject


class InviteOut(BaseModel):
    id: str
    name: str
    email: str
    role: MembershipRole
    # False when the mail hop failed. The seat exists either way, but nobody can use it until the
    # link lands, so the UI has to say so rather than reporting a clean success.
    email_sent: bool


class RoleOut(BaseModel):
    id: str
    role: MembershipRole


class StatusIdOut(BaseModel):
    status: str
    id: str


@router.get("/members", response_model=list[MemberOut])
async def members(ctx: ContextDep, session: SessionDep) -> list[MemberOut]:
    rows = (
        (
            await session.execute(
                select(Membership, User)
                .join(User, Membership.user_id == User.id)
                .where(Membership.organization_id == ctx.org_id)
                .order_by(User.created_at)
            )
        )
        .tuples()
        .all()
    )
    grants = (
        (
            await session.execute(
                select(SpaceGrant.user_id, SpaceGrant.workspace_id)
                .join(Workspace, SpaceGrant.workspace_id == Workspace.id)
                .where(Workspace.organization_id == ctx.org_id)
            )
        )
        .tuples()
        .all()
    )
    by_user: dict[str, list[str]] = {}
    for user_id, workspace_id in grants:
        by_user.setdefault(user_id, []).append(workspace_id)
    return [
        MemberOut(
            id=u.id,
            name=u.name,
            email=u.email,
            role=m.role,
            workspace_ids=by_user.get(u.id, []),
        )
        for m, u in rows
    ]


@router.get("/connections", response_model=list[ConnectionOut])
async def connections(ctx: ContextDep, session: SessionDep) -> list[ConnectionOut]:
    rows = (
        (
            await session.execute(
                select(Connection, User)
                .join(User, Connection.user_id == User.id)
                .where(Connection.organization_id == ctx.org_id)
            )
        )
        .tuples()
        .all()
    )
    return [_dump_connection(c, u.email) for c, u in rows]


# ---- workspace preferences ----


class WorkspacePatch(BaseModel):
    name: str | None = None
    settings: JsonObject | None = None


async def _workspace_settings(session: SessionDep, workspace: Workspace) -> WorkspaceSettingsOut:
    resolved = await policy.for_workspace(session, workspace_id=workspace.id)
    return WorkspaceSettingsOut(
        id=workspace.id,
        name=workspace.name,
        kind=workspace.kind,
        labels=LabelPack(**vars(resolve_labels(resolved.get_str("vertical"), workspace.kind))),
        settings=resolved.effective(),
        overrides=workspace.settings or {},
    )


@router.get("/workspace", response_model=WorkspaceSettingsOut)
async def get_workspace_settings(ctx: ContextDep, session: SessionDep) -> WorkspaceSettingsOut:
    workspace = await session.get(Workspace, require_workspace(ctx))
    if workspace is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    return await _workspace_settings(session, workspace)


@router.patch("/workspace", response_model=WorkspaceSettingsOut)
async def update_workspace_settings(
    body: WorkspacePatch, ctx: ContextDep, session: SessionDep
) -> WorkspaceSettingsOut:
    workspace = await session.get(Workspace, require_workspace(ctx))
    if workspace is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    if body.name is not None:
        workspace.name = body.name
    if body.settings is not None:
        workspace.settings = {**(workspace.settings or {}), **body.settings}
    await session.flush()
    return await _workspace_settings(session, workspace)


# ---- connection management ----
#
# A seat is only ever created by a real provider round-trip: LinkedIn through Unipile's
# hosted-auth wizard (`/connections/linkedin/link`), whose notify webhook writes the Connection.
# There is deliberately no endpoint that marks a seat "connected" without an account behind it —
# that is what made Settings claim LinkedIn was live when nothing had been authorised.


class ConnectLinkOut(BaseModel):
    """The hosted-auth wizard to redirect to, or null when LinkedIn connect isn't configured."""

    url: str | None


@router.post("/connections/linkedin/link", response_model=ConnectLinkOut)
async def linkedin_connect_link(ctx: ContextDep, session: SessionDep) -> ConnectLinkOut:
    """Start connecting *this* user's LinkedIn sending seat (Unipile hosted auth).

    Returns the wizard URL for the client to redirect to; Unipile's notify webhook attaches the
    resulting account to the user, which is what the messaging layer sends from. `null` means
    Unipile isn't configured — the caller falls back to the local stub connect.
    """
    return ConnectLinkOut(
        url=await connections_service.start_linkedin_connect(session, user_id=ctx.user_id)
    )


@router.post("/connections/linkedin/notify")
async def linkedin_notify(request: Request, session: SessionDep) -> dict[str, str]:
    """Unipile's server-side notify hop: attach the connected seat to the user who started it.

    Public and token-gated (the shared secret rides in the query string, which is all the wizard
    link lets us template). Not a sign-in: the wizard is only ever opened by someone already
    signed in, and an attempt naming no user is ignored.
    """
    secret = get_settings().unipile_webhook_secret
    token = request.query_params.get("token") or ""
    if not secret or not hmac.compare_digest(token, secret):
        raise HTTPException(status_code=401, detail="invalid token")
    try:
        parsed: object = json.loads(await request.body() or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON") from None
    payload = parsed if isinstance(parsed, dict) else {}
    account_id = payload.get("account_id")
    state = payload.get("name")  # the state token we set as `name` on the hosted-auth link
    if isinstance(account_id, str) and isinstance(state, str):
        await connections_service.complete_linkedin_notify(
            session, state=state, account_id=account_id
        )
    return {"status": "ok"}


@router.post("/connections/{connection_id}/disconnect", response_model=StatusIdOut)
async def disconnect(connection_id: str, ctx: ContextDep, session: SessionDep) -> StatusIdOut:
    conn = await _owned_connection(session, ctx.org_id, connection_id)
    await session.delete(conn)
    await session.flush()
    return StatusIdOut(status="disconnected", id=connection_id)


@router.post("/connections/{connection_id}/reauth", response_model=ConnectionOut)
async def reauth(connection_id: str, ctx: ContextDep, session: SessionDep) -> ConnectionOut:
    conn = await _owned_connection(session, ctx.org_id, connection_id)
    conn.status = ConnectionStatus.ok
    await session.flush()
    user = await session.get(User, conn.user_id)
    return _dump_connection(conn, user.email if user else "")


# ---- member management (org admin only) ----


class InviteRequest(BaseModel):
    email: str
    name: str
    role: MembershipRole = MembershipRole.member


class RolePatch(BaseModel):
    role: MembershipRole


@router.post("/members/invite", response_model=InviteOut)
async def invite_member(body: InviteRequest, ctx: ContextDep, session: SessionDep) -> InviteOut:
    """Invite a teammate: create their pending seat and mail them the link that activates it.

    Re-inviting an address whose invite is still pending re-sends that link rather than failing —
    an admin's natural "did they get it?" retry, and the only resend path there needs to be.
    """
    require_org_admin(ctx)
    email = body.email.strip().lower()
    inviter = await session.get(User, ctx.user_id)
    # Identity is global: an existing user is invited into this org, not duplicated.
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        user = User(
            email=email,
            name=body.name,
            status=UserStatus.invited,
            # They join an org that already exists — no company to name, no password to set.
            profile_completed_at=datetime.now(UTC),
        )
        session.add(user)
        await session.flush()
    membership = (
        await session.execute(
            select(Membership).where(
                Membership.user_id == user.id, Membership.organization_id == ctx.org_id
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        membership = Membership(user_id=user.id, organization_id=ctx.org_id, role=body.role)
        session.add(membership)
        await session.flush()
    elif user.status is not UserStatus.invited:
        raise HTTPException(status_code=409, detail="that person is already a member")
    sent = await auth_service.send_invite_email(
        session, user=user, inviter=inviter, organization_id=ctx.org_id
    )
    return InviteOut(
        id=user.id, name=user.name, email=user.email, role=membership.role, email_sent=sent
    )


@router.patch("/members/{user_id}", response_model=RoleOut)
async def update_member_role(
    user_id: str, body: RolePatch, ctx: ContextDep, session: SessionDep
) -> RoleOut:
    require_org_admin(ctx)
    membership = (
        await session.execute(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.organization_id == ctx.org_id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=404, detail="member not found")
    membership.role = body.role
    await session.flush()
    return RoleOut(id=user_id, role=body.role)


@router.delete("/members/{user_id}", response_model=StatusIdOut)
async def remove_member(user_id: str, ctx: ContextDep, session: SessionDep) -> StatusIdOut:
    require_org_admin(ctx)
    if user_id == ctx.user_id:
        raise HTTPException(status_code=400, detail="you can't remove yourself")
    membership = (
        await session.execute(
            select(Membership).where(
                Membership.user_id == user_id, Membership.organization_id == ctx.org_id
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=404, detail="member not found")
    await session.execute(
        delete(SpaceGrant).where(
            SpaceGrant.user_id == user_id,
            SpaceGrant.workspace_id.in_(
                select(Workspace.id).where(Workspace.organization_id == ctx.org_id)
            ),
        )
    )
    await session.delete(membership)
    await session.flush()
    return StatusIdOut(status="removed", id=user_id)


# ---- data-provider credentials (BYO people-data keys; org admin only) ----


class DataProviderIn(BaseModel):
    api_key: str
    enabled: bool = True
    label: str | None = None


@router.get("/data-providers", response_model=list[DataProviderOut])
async def list_data_providers(ctx: ContextDep, session: SessionDep) -> list[DataProviderOut]:
    creds = await _provider_creds(session, ctx.org_id)
    return [_dump_data_provider(spec, creds.get(spec.key)) for spec in PROVIDER_CATALOG]


@router.put("/data-providers/{provider}", response_model=DataProviderOut)
async def set_data_provider(
    provider: str, body: DataProviderIn, ctx: ContextDep, session: SessionDep
) -> DataProviderOut:
    require_org_admin(ctx)
    spec = next((s for s in PROVIDER_CATALOG if s.key == provider), None)
    if spec is None:
        raise HTTPException(status_code=404, detail="unknown provider")
    api_key = body.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key required")
    cred = (await _provider_creds(session, ctx.org_id)).get(provider)
    if cred is None:
        cred = ProviderCredential(organization_id=ctx.org_id, provider=provider)
        session.add(cred)
    cred.secret = seal(api_key)
    cred.last4 = api_key[-4:]
    cred.enabled = body.enabled
    cred.label = body.label
    cred.status = "unverified"
    await session.flush()
    await audit.record(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.current_workspace_id,
        actor_user_id=ctx.user_id,
        action="provider.key_set",
        summary=f"Set the {provider} API key",
        target_type="provider",
        target_id=provider,
    )
    return _dump_data_provider(spec, cred)


@router.delete("/data-providers/{provider}", response_model=StatusIdOut)
async def delete_data_provider(provider: str, ctx: ContextDep, session: SessionDep) -> StatusIdOut:
    require_org_admin(ctx)
    cred = (await _provider_creds(session, ctx.org_id)).get(provider)
    if cred is not None:
        await session.delete(cred)
        await session.flush()
        await audit.record(
            session,
            org_id=ctx.org_id,
            workspace_id=ctx.current_workspace_id,
            actor_user_id=ctx.user_id,
            action="provider.key_removed",
            summary=f"Removed the {provider} API key",
            target_type="provider",
            target_id=provider,
        )
    return StatusIdOut(status="removed", id=provider)


@router.post("/data-providers/{provider}/verify", response_model=DataProviderOut)
async def verify_data_provider(
    provider: str, ctx: ContextDep, session: SessionDep
) -> DataProviderOut:
    """Test a stored provider key against the provider and record the result."""
    require_org_admin(ctx)
    spec = next((s for s in PROVIDER_CATALOG if s.key == provider), None)
    if spec is None:
        raise HTTPException(status_code=404, detail="unknown provider")
    cred = (await _provider_creds(session, ctx.org_id)).get(provider)
    if cred is None:
        raise HTTPException(status_code=404, detail="provider is not configured")
    adapter = build_one(provider, unseal(cred.secret))
    ok = await adapter.verify_credentials() if adapter is not None else False
    cred.status = "ok" if ok else "invalid"
    cred.last_verified_at = datetime.now(UTC)
    await session.flush()
    await audit.record(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.current_workspace_id,
        actor_user_id=ctx.user_id,
        action="provider.key_verified",
        summary=f"Verified the {provider} key ({cred.status})",
        target_type="provider",
        target_id=provider,
    )
    return _dump_data_provider(spec, cred)


class ExportOrganization(BaseModel):
    id: str
    name: str
    slug: str
    data_region: str


class ExportWorkspace(BaseModel):
    id: str
    name: str
    kind: str


class ExportContact(BaseModel):
    id: str
    full_name: str
    title: str | None
    company: str | None
    email: str | None
    linkedin_url: str | None
    location: str | None
    skills: list[str]
    tags: list[str]
    notes: str | None
    source: str


class ExportCampaign(BaseModel):
    id: str
    name: str
    status: str
    criteria: JsonObject


class ExportEnrollment(BaseModel):
    id: str
    campaign_id: str
    contact_id: str
    state: str
    score: int


class ExportMessage(BaseModel):
    id: str
    enrollment_id: str
    direction: str
    channel: str
    status: str
    subject: str | None
    body: str


class OrgExport(BaseModel):
    exported_at: str
    organization: ExportOrganization | None
    workspaces: list[ExportWorkspace]
    contacts: list[ExportContact]
    campaigns: list[ExportCampaign]
    enrollments: list[ExportEnrollment]
    messages: list[ExportMessage]


@router.get("/export")
async def export_org(ctx: ContextDep, session: SessionDep) -> OrgExport:
    """GDPR data-portability: a JSON dump of the organization's data (org admin only)."""
    require_org_admin(ctx)
    org = await session.get(Organization, ctx.org_id)
    workspaces = list(
        (await session.execute(select(Workspace).where(Workspace.organization_id == ctx.org_id)))
        .scalars()
        .all()
    )
    ws_ids = [w.id for w in workspaces]
    contacts = (
        list(
            (await session.execute(select(Contact).where(Contact.workspace_id.in_(ws_ids))))
            .scalars()
            .all()
        )
        if ws_ids
        else []
    )
    campaigns = (
        list(
            (await session.execute(select(Campaign).where(Campaign.workspace_id.in_(ws_ids))))
            .scalars()
            .all()
        )
        if ws_ids
        else []
    )
    enrollments = (
        list(
            (await session.execute(select(Enrollment).where(Enrollment.workspace_id.in_(ws_ids))))
            .scalars()
            .all()
        )
        if ws_ids
        else []
    )
    messages = (
        list(
            (await session.execute(select(Message).where(Message.workspace_id.in_(ws_ids))))
            .scalars()
            .all()
        )
        if ws_ids
        else []
    )
    await audit.record(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.current_workspace_id,
        actor_user_id=ctx.user_id,
        action="org.exported",
        summary="Exported organization data (GDPR)",
    )
    return OrgExport(
        exported_at=datetime.now(UTC).isoformat(),
        organization=(
            ExportOrganization(id=org.id, name=org.name, slug=org.slug, data_region=org.data_region)
            if org
            else None
        ),
        workspaces=[ExportWorkspace(id=w.id, name=w.name, kind=w.kind.value) for w in workspaces],
        contacts=[
            ExportContact(
                id=c.id,
                full_name=c.full_name,
                title=c.title,
                company=c.company,
                email=c.email,
                linkedin_url=c.linkedin_url,
                location=c.location,
                skills=c.skills,
                tags=c.tags,
                notes=c.notes,
                source=c.source,
            )
            for c in contacts
        ],
        campaigns=[
            ExportCampaign(id=c.id, name=c.name, status=c.status.value, criteria=c.criteria)
            for c in campaigns
        ],
        enrollments=[
            ExportEnrollment(
                id=e.id,
                campaign_id=e.campaign_id,
                contact_id=e.contact_id,
                state=e.state.value,
                score=e.score,
            )
            for e in enrollments
        ],
        messages=[
            ExportMessage(
                id=m.id,
                enrollment_id=m.enrollment_id,
                direction=m.direction.value,
                channel=m.channel.value,
                status=m.status.value,
                subject=m.subject,
                body=m.body,
            )
            for m in messages
        ],
    )
