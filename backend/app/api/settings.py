"""Workspace/org settings HTTP endpoints: members, connections, data providers, export.

Serializers + data-access helpers live in `app.services.workspace.settings`.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.core.crypto import seal, unseal
from app.core.types import JsonObject
from app.deps import ContextDep, SessionDep, require_org_admin, require_workspace
from app.models import (
    Campaign,
    Connection,
    ConnectionProvider,
    ConnectionStatus,
    Contact,
    Enrollment,
    Membership,
    MembershipRole,
    MembershipScope,
    Message,
    Organization,
    ProviderCredential,
    SeatType,
    User,
    UserStatus,
    Workspace,
)
from app.services.insights import audit
from app.services.people.adapters.registry import PROVIDER_CATALOG, build_one
from app.services.workspace.settings import (
    ConnectionOut,
    DataProviderOut,
    _dump_connection,
    _dump_data_provider,
    _owned_connection,
    _provider_creds,
)

router = APIRouter(prefix="/settings", tags=["settings"])

WORKSPACE_DEFAULTS: JsonObject = {
    "autonomy_default": "approve_each",
    "sending_window": "Mon-Fri, 08:00-18:00, recipient local",
    "daily_cap_email": 120,
    "daily_cap_linkedin": 80,
}


class MemberOut(BaseModel):
    id: str
    name: str
    email: str
    role: str
    scope: str


class WorkspaceSettingsOut(BaseModel):
    id: str
    name: str
    brand_voice: str | None
    settings: JsonObject


class InviteOut(BaseModel):
    id: str
    name: str
    email: str
    role: str


class RoleOut(BaseModel):
    id: str
    role: str


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
    return [
        MemberOut(
            id=u.id,
            name=u.name,
            email=u.email,
            role=m.role.value,
            scope=m.scope.value,
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
    brand_voice: str | None = None
    settings: JsonObject | None = None


@router.get("/workspace", response_model=WorkspaceSettingsOut)
async def get_workspace_settings(ctx: ContextDep, session: SessionDep) -> WorkspaceSettingsOut:
    ws = require_workspace(ctx)
    workspace = await session.get(Workspace, ws)
    if workspace is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    return WorkspaceSettingsOut(
        id=workspace.id,
        name=workspace.name,
        brand_voice=workspace.brand_voice,
        settings={**WORKSPACE_DEFAULTS, **(workspace.settings or {})},
    )


@router.patch("/workspace", response_model=WorkspaceSettingsOut)
async def update_workspace_settings(
    body: WorkspacePatch, ctx: ContextDep, session: SessionDep
) -> WorkspaceSettingsOut:
    ws = require_workspace(ctx)
    workspace = await session.get(Workspace, ws)
    if workspace is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    if body.name is not None:
        workspace.name = body.name
    if body.brand_voice is not None:
        workspace.brand_voice = body.brand_voice
    if body.settings is not None:
        workspace.settings = {**(workspace.settings or {}), **body.settings}
    await session.flush()
    return WorkspaceSettingsOut(
        id=workspace.id,
        name=workspace.name,
        brand_voice=workspace.brand_voice,
        settings={**WORKSPACE_DEFAULTS, **(workspace.settings or {})},
    )


# ---- connection management (stub OAuth: connecting just marks the seat live) ----

_SEAT_FOR = {
    ConnectionProvider.gmail: SeatType.email,
    ConnectionProvider.graph: SeatType.email,
    ConnectionProvider.linkedin: SeatType.recruiter,
}


@router.post("/connections/{provider}/connect", response_model=ConnectionOut)
async def connect(
    provider: ConnectionProvider, ctx: ContextDep, session: SessionDep
) -> ConnectionOut:
    existing = (
        await session.execute(
            select(Connection).where(
                Connection.organization_id == ctx.org_id,
                Connection.user_id == ctx.user_id,
                Connection.provider == provider,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.status = ConnectionStatus.ok
        conn = existing
    else:
        conn = Connection(
            organization_id=ctx.org_id,
            user_id=ctx.user_id,
            provider=provider,
            seat_type=_SEAT_FOR.get(provider, SeatType.email),
            status=ConnectionStatus.ok,
        )
        session.add(conn)
    await session.flush()
    user = await session.get(User, ctx.user_id)
    return _dump_connection(conn, user.email if user else "")


@router.post("/connections/{connection_id}/disconnect", response_model=StatusIdOut)
async def disconnect(connection_id: str, ctx: ContextDep, session: SessionDep) -> StatusIdOut:
    conn = await _owned_connection(session, ctx, connection_id)
    await session.delete(conn)
    await session.flush()
    return StatusIdOut(status="disconnected", id=connection_id)


@router.post("/connections/{connection_id}/reauth", response_model=ConnectionOut)
async def reauth(connection_id: str, ctx: ContextDep, session: SessionDep) -> ConnectionOut:
    conn = await _owned_connection(session, ctx, connection_id)
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
    require_org_admin(ctx)
    dupe = (
        await session.execute(
            select(User).where(User.organization_id == ctx.org_id, User.email == body.email)
        )
    ).scalar_one_or_none()
    if dupe is not None:
        raise HTTPException(status_code=409, detail="a user with that email already exists")
    user = User(
        organization_id=ctx.org_id,
        email=body.email,
        name=body.name,
        status=UserStatus.invited,
    )
    session.add(user)
    await session.flush()
    session.add(
        Membership(
            user_id=user.id,
            organization_id=ctx.org_id,
            scope=MembershipScope.organization,
            role=body.role,
        )
    )
    await session.flush()
    return InviteOut(id=user.id, name=user.name, email=user.email, role=body.role.value)


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
                Membership.scope == MembershipScope.organization,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=404, detail="member not found")
    membership.role = body.role
    await session.flush()
    return RoleOut(id=user_id, role=body.role.value)


@router.delete("/members/{user_id}", response_model=StatusIdOut)
async def remove_member(user_id: str, ctx: ContextDep, session: SessionDep) -> StatusIdOut:
    require_org_admin(ctx)
    if user_id == ctx.user_id:
        raise HTTPException(status_code=400, detail="you can't remove yourself")
    user = await session.get(User, user_id)
    if user is None or user.organization_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="member not found")
    await session.delete(user)
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
        ctx,
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
            ctx,
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
        ctx,
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
        ctx,
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
