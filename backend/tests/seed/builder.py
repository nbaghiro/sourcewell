"""Demo/test data builder — a demo org with three richly-populated verticals.

Mirrors the client-side in-memory demo (frontend/src/lib/api/demo/) so a backend-seeded demo user
renders identically: Recruiting + Enterprise Sales + Partnerships, each with enriched contacts,
campaign lifecycle (active/paused/draft/done), enrollments scored via the real Evaluator, deep
multi-channel threads, queued (scheduled) sends, and a generated audit trail. Reusable from the seed
CLI (`make seed`, i.e. `python -m tests.seed`) and from test fixtures.
"""

# ruff: noqa: RUF001

import random
from datetime import UTC, datetime, timedelta
from typing import TypedDict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import hash_password
from app.core.types import JsonObject
from app.models import (
    AgentRole,
    AgentRun,
    AgentStep,
    AuditEvent,
    AutonomyLevel,
    Campaign,
    CampaignStatus,
    Channel,
    Connection,
    ConnectionProvider,
    ConnectionStatus,
    Contact,
    Enrollment,
    EnrollmentState,
    Membership,
    MembershipRole,
    Message,
    MessageDirection,
    MessageStatus,
    Organization,
    SeatType,
    SpaceGrant,
    SpaceRole,
    User,
    Workspace,
    WorkspaceKind,
)
from app.services.billing.credits import CreditStatus, credit_status, period_start
from app.targeting import evaluate
from tests.seed.data import (
    SequenceStep,
    _stable_hash,
    build_messages,
    make_contacts,
    states_for,
)

DEMO_ORG_SLUG = "acme-talent"


class CampaignSpec(TypedDict):
    name: str
    status: str
    autonomy_level: str
    criteria: JsonObject
    steps: list[SequenceStep]


class DemoSummary(TypedDict):
    org_id: str
    org_slug: str
    workspaces: int
    enrollments_by_state: dict[str, int]
    credits: dict[str, int]


# ---- campaign specs per vertical (name / status / autonomy / criteria / sequence) ----

RECRUIT_CAMPAIGNS: list[CampaignSpec] = [
    {
        "name": "Senior Backend Engineer",
        "status": "active",
        "autonomy_level": "assisted",
        "criteria": {
            "titles": ["Senior Backend Engineer", "Staff Engineer"],
            "skills": ["Python", "Go"],
            "locations": ["EU"],
        },
        "steps": [
            {
                "channel": "email",
                "delay_days": 0,
                "subject": "Quick question, {first}",
                "body": "Saw your work at {company}.",
            },
            {"channel": "linkedin", "delay_days": 3, "body": "Following up, {first}."},
            {"channel": "email", "delay_days": 5, "body": "Last nudge — happy to share the JD."},
        ],
    },
    {
        "name": "Data Platform Lead",
        "status": "active",
        "autonomy_level": "assisted",
        "criteria": {"titles": ["Data Platform Engineer"], "skills": ["Spark", "dbt"]},
        "steps": [
            {"channel": "email", "delay_days": 0, "subject": "Data platform role"},
            {"channel": "linkedin", "delay_days": 3},
        ],
    },
    {
        "name": "Frontend Engineer — H1",
        "status": "done",
        "autonomy_level": "assisted",
        "criteria": {"titles": ["Frontend Engineer"], "skills": ["React"]},
        "steps": [{"channel": "email", "delay_days": 0}],
    },
    {
        "name": "ML Research Scientist",
        "status": "draft",
        "autonomy_level": "assisted",
        "criteria": {"titles": ["ML Engineer"], "skills": ["PyTorch", "LLMs"]},
        "steps": [{"channel": "email", "delay_days": 0}],
    },
]
SALES_CAMPAIGNS: list[CampaignSpec] = [
    {
        "name": "Enterprise Outbound — Q3",
        "status": "active",
        "autonomy_level": "assisted",
        "criteria": {
            "titles": ["VP of Sales", "Chief Revenue Officer"],
            "skills": ["Salesforce", "Enterprise"],
            "locations": ["EU"],
        },
        "steps": [
            {"channel": "email", "delay_days": 0, "subject": "Cutting {company}'s SDR ramp"},
            {"channel": "linkedin", "delay_days": 2},
            {"channel": "email", "delay_days": 4},
        ],
    },
    {
        "name": "RevOps Expansion",
        "status": "active",
        "autonomy_level": "full",
        "criteria": {"titles": ["Head of RevOps"], "skills": ["HubSpot"]},
        "steps": [{"channel": "email", "delay_days": 0}, {"channel": "linkedin", "delay_days": 3}],
    },
    {
        "name": "Mid-Market Pilot",
        "status": "done",
        "autonomy_level": "assisted",
        "criteria": {"titles": ["Director of Demand Gen"]},
        "steps": [{"channel": "email", "delay_days": 0}],
    },
]
PARTNER_CAMPAIGNS: list[CampaignSpec] = [
    {
        "name": "Agency Partner Program",
        "status": "active",
        "autonomy_level": "assisted",
        "criteria": {
            "titles": ["Head of Partnerships", "Director of Business Development"],
            "skills": ["Channel"],
        },
        "steps": [
            {"channel": "email", "delay_days": 0, "subject": "{company} × us — partnership?"},
            {"channel": "linkedin", "delay_days": 3},
            {"channel": "email", "delay_days": 6},
        ],
    },
    {
        "name": "Integration Partners",
        "status": "active",
        "autonomy_level": "assisted",
        "criteria": {"titles": ["Ecosystem Lead", "VP Strategic Alliances"]},
        "steps": [{"channel": "email", "delay_days": 0}, {"channel": "linkedin", "delay_days": 4}],
    },
    {
        "name": "Reseller Outreach",
        "status": "draft",
        "autonomy_level": "assisted",
        "criteria": {"titles": ["Founder & CEO"]},
        "steps": [{"channel": "email", "delay_days": 0}],
    },
]


async def _reset(session: AsyncSession) -> None:
    org = (
        await session.execute(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
    ).scalar_one_or_none()
    if org is None:
        return
    # Users are global — the org cascade doesn't reach them, so drop this org's members by hand.
    members = (
        (
            await session.execute(
                select(User)
                .join(Membership, Membership.user_id == User.id)
                .where(Membership.organization_id == org.id)
            )
        )
        .scalars()
        .all()
    )
    for member in members:
        await session.delete(member)
    await session.delete(org)  # FKs are ON DELETE CASCADE
    await session.flush()


async def _org_and_admin(session: AsyncSession) -> tuple[Organization, User]:
    # Start on the free plan (200 credits) so the seeded usage sits over-limit out of the box and
    # each simulated upgrade (see tests/seed/billing_sim.py) visibly lifts the allowance.
    org = Organization(name="Acme Talent", slug=DEMO_ORG_SLUG, plan="free")
    session.add(org)
    await session.flush()
    s = get_settings()
    admin = User(
        email=s.demo_admin_email,
        name="Avery Brooks",
        first_name="Avery",
        last_name="Brooks",
        username="avery",
        password_hash=hash_password(s.demo_password),
        email_verified_at=datetime.now(UTC),  # the demo account signs in without a mail round-trip
        profile_completed_at=datetime.now(UTC),  # ...and is past signup, so the API isn't gated
    )
    session.add(admin)
    await session.flush()
    session.add(Membership(user_id=admin.id, organization_id=org.id, role=MembershipRole.org_admin))
    await session.flush()
    return org, admin


async def _seed_team(
    session: AsyncSession, *, org: Organization, admin: User
) -> tuple[list[str], dict[str, User]]:
    """Teammates + their channel connections. Returns the audit actor ids and the team by name."""
    team = [
        ("Dana Okafor", "dana@acme.demo", MembershipRole.member),
        ("Riley Walsh", "riley@acme.demo", MembershipRole.member),
        ("Sam Patel", "compliance@acme.demo", MembershipRole.compliance),
    ]
    users: dict[str, User] = {}
    for name, email, role in team:
        user = User(
            email=email,
            name=name,
            email_verified_at=datetime.now(UTC),
            profile_completed_at=datetime.now(UTC),
        )
        session.add(user)
        await session.flush()
        session.add(Membership(user_id=user.id, organization_id=org.id, role=role))
        users[name] = user
    await session.flush()

    # Seats, deliberately with no `external_id`: that field means "a real provider account is
    # authorised behind this", and the app takes it at its word — `deliver_outbound` sends to
    # whatever account id it finds. Seeding fake ones meant a demo install with real Unipile keys
    # posted to Unipile with an account that doesn't exist and every send failed. Unlinked seats
    # show honestly as "can't send" in Settings, and email falls through to SMTP (Mailpit).
    session.add_all(
        [
            Connection(
                organization_id=org.id,
                user_id=admin.id,
                provider=ConnectionProvider.gmail,
                seat_type=SeatType.email,
                status=ConnectionStatus.ok,
                capabilities={"send": True},
            ),
            Connection(
                organization_id=org.id,
                user_id=admin.id,
                provider=ConnectionProvider.linkedin,
                seat_type=SeatType.recruiter,
                status=ConnectionStatus.needs_reauth,
                capabilities={"daily_cap": 150},
            ),
            Connection(
                organization_id=org.id,
                user_id=users["Riley Walsh"].id,
                provider=ConnectionProvider.linkedin,
                seat_type=SeatType.sales_nav,
                status=ConnectionStatus.ok,
                capabilities={"daily_cap": 100},
            ),
            Connection(
                organization_id=org.id,
                user_id=users["Dana Okafor"].id,
                provider=ConnectionProvider.graph,
                seat_type=SeatType.email,
                status=ConnectionStatus.ok,
                capabilities={"send": True},
            ),
        ]
    )
    await session.flush()
    return [admin.id, users["Dana Okafor"].id, users["Riley Walsh"].id], users


async def _assign(
    session: AsyncSession,
    ws: Workspace,
    campaign: Campaign,
    contacts: list[Contact],
    kind: str,
    now: datetime,
) -> None:
    mode = campaign.status.value
    plan = states_for(len(contacts), mode)
    if not plan:
        return
    scored = []
    for c in contacts:
        score, rationale = evaluate(c, campaign.criteria or {})
        scored.append((c, score, rationale))
    scored.sort(key=lambda x: x[1], reverse=True)

    campaign_age = 70 if mode == "done" else 18
    pending: list[tuple[Enrollment, Contact, str, bool, float]] = []
    for idx, (c, score, rationale) in enumerate(scored):
        state, rp = plan[idx]
        start_day = (60 if mode == "done" else 14) - (idx % 10) * 0.8
        enr = Enrollment(
            workspace_id=ws.id,
            campaign_id=campaign.id,
            contact_id=c.id,
            state=EnrollmentState(state),
            score=score,
            score_rationale=rationale,
            current_step=0 if state == "proposed" else (1 if state == "scheduled" else 2),
            next_run_at=(now + timedelta(days=1 + (_stable_hash(c.full_name) % 3)))
            if state == "scheduled"
            else None,
            outcome="interested"
            if state == "handed_off"
            else "opted_out"
            if state == "opted_out"
            else None,
            reply_pending=rp,
            created_at=now - timedelta(days=campaign_age - idx * 0.3),
            updated_at=now - timedelta(days=max(0.2, start_day - 5)),
        )
        session.add(enr)
        pending.append((enr, c, state, rp, start_day))
    await session.flush()

    for enr, c, state, rp, start_day in pending:
        cdict = {
            "full_name": c.full_name,
            "company": c.company,
            "title": c.title,
            "skills": c.skills,
        }
        for m in build_messages(
            state=state,
            reply_pending=rp,
            sequence=campaign.sequence,
            contact=cdict,
            kind=kind,
            start_day=start_day,
            now=now,
        ):
            session.add(
                Message(
                    workspace_id=ws.id,
                    enrollment_id=enr.id,
                    direction=MessageDirection(m["direction"]),
                    channel=Channel(m["channel"]),
                    status=MessageStatus(m["status"]),
                    subject=m["subject"],
                    body=m["body"],
                    sent_at=m["sent_at"],
                    scheduled_at=m["scheduled_at"],
                    created_at=m["created_at"],
                    origin=m["origin"],
                )
            )
    await session.flush()


async def _seed_workspace(
    session: AsyncSession,
    ws: Workspace,
    kind: str,
    specs: list[CampaignSpec],
    contact_count: int,
    now: datetime,
    rng: random.Random,
    *,
    owner: User,
) -> None:
    contacts = [Contact(**cd) for cd in make_contacts(ws.id, kind, contact_count, rng=rng)]
    session.add_all(contacts)
    await session.flush()

    from_email = "recruiter@acme.com" if kind == "eng" else "gtm@acme.com"
    campaigns: list[Campaign] = []
    for i, spec in enumerate(specs):
        campaigns.append(
            Campaign(
                workspace_id=ws.id,
                name=spec["name"],
                status=CampaignStatus(spec["status"]),
                autonomy_level=AutonomyLevel(spec["autonomy_level"]),
                from_email=from_email,
                created_by_user_id=owner.id,
                criteria=spec["criteria"],
                sequence=[
                    {
                        "channel": s["channel"],
                        "delay_days": s["delay_days"],
                        # Fall back to a sensible touchpoint so no campaign renders an empty step.
                        "subject": s.get("subject")
                        or ("Quick question, {first}" if s["channel"] == "email" else ""),
                        "body": s.get("body")
                        or (
                            "Came across your work at {company} — open to a quick chat?"
                            if s["channel"] == "email"
                            else "Following up here, {first} — still worth a conversation?"
                        ),
                    }
                    for s in spec["steps"]
                ],
                created_at=now - timedelta(days=90 if spec["status"] == "done" else 21 - i * 2),
            )
        )
    session.add_all(campaigns)
    await session.flush()

    primary = next((c for c in campaigns if c.status == CampaignStatus.active), campaigns[0])
    await _assign(session, ws, primary, contacts, kind, now)
    secondary = next(
        (c for c in campaigns if c.status == CampaignStatus.active and c is not primary), None
    )
    if secondary:
        await _assign(session, ws, secondary, contacts[: max(1, len(contacts) // 2)], kind, now)
    for i, c in enumerate(campaigns):
        if c is primary or c is secondary or c.status == CampaignStatus.draft:
            continue
        start = (i * 4) % len(contacts)
        await _assign(session, ws, c, contacts[start : start + 5], kind, now)

    await _seed_agent_runs(session, ws, campaigns, contacts, now, rng)


async def _generate_audit(
    session: AsyncSession, org_id: str, ws_ids: list[str], actor_ids: list[str], now: datetime
) -> None:
    """Synthesize an audit trail from the seeded messages + enrollments, across actors and time."""
    messages = (
        (await session.execute(select(Message).where(Message.workspace_id.in_(ws_ids))))
        .scalars()
        .all()
    )
    enrollments = (
        (await session.execute(select(Enrollment).where(Enrollment.workspace_id.in_(ws_ids))))
        .scalars()
        .all()
    )

    def actor(seed: int) -> str:
        return actor_ids[seed % len(actor_ids)]

    events: list[AuditEvent] = []
    for m in messages:
        if m.status == MessageStatus.sent and m.direction == MessageDirection.outbound:
            events.append(
                AuditEvent(
                    organization_id=org_id,
                    action="message.approved",
                    summary="Approved a drafted message",
                    target_type="message",
                    target_id=m.id,
                    actor_user_id=actor(_stable_hash(m.id)),
                    workspace_id=m.workspace_id,
                    created_at=m.sent_at or m.created_at,
                )
            )
        elif m.direction == MessageDirection.inbound:
            events.append(
                AuditEvent(
                    organization_id=org_id,
                    action="reply.received",
                    summary="Inbound reply received",
                    target_type="enrollment",
                    target_id=m.enrollment_id,
                    actor_user_id=None,
                    workspace_id=m.workspace_id,
                    created_at=m.created_at,
                )
            )
    for e in enrollments:
        if e.state == EnrollmentState.handed_off:
            events.append(
                AuditEvent(
                    organization_id=org_id,
                    action="enrollment.handed_off",
                    summary="Handed off a candidate",
                    target_type="enrollment",
                    target_id=e.id,
                    actor_user_id=actor(_stable_hash(e.id)),
                    workspace_id=e.workspace_id,
                    created_at=e.updated_at,
                )
            )
        elif e.state == EnrollmentState.opted_out:
            events.append(
                AuditEvent(
                    organization_id=org_id,
                    action="enrollment.opted_out",
                    summary="Marked a candidate not interested",
                    target_type="enrollment",
                    target_id=e.id,
                    actor_user_id=actor(_stable_hash(e.id)),
                    workspace_id=e.workspace_id,
                    created_at=e.updated_at,
                )
            )
    events += [
        AuditEvent(
            organization_id=org_id,
            action="auth.login",
            summary="Signed in",
            actor_user_id=actor_ids[0],
            workspace_id=None,
            created_at=now - timedelta(hours=2),
        ),
        AuditEvent(
            organization_id=org_id,
            action="connection.connected",
            summary="Connected Gmail seat",
            target_type="connection",
            actor_user_id=actor_ids[0],
            workspace_id=None,
            created_at=now - timedelta(days=9),
        ),
        AuditEvent(
            organization_id=org_id,
            action="member.invited",
            summary="Invited Riley Walsh",
            target_type="user",
            actor_user_id=actor_ids[0],
            workspace_id=None,
            created_at=now - timedelta(days=20),
        ),
    ]
    events.sort(key=lambda ev: ev.created_at, reverse=True)
    session.add_all(events[:80])
    await session.flush()


async def _summary(
    session: AsyncSession, org: Organization, ws_ids: list[str], credits: CreditStatus
) -> DemoSummary:
    rows = (
        await session.execute(
            select(Enrollment.state, func.count())
            .where(Enrollment.workspace_id.in_(ws_ids))
            .group_by(Enrollment.state)
        )
    ).all()
    by_state = {str(getattr(state, "value", state)): count for state, count in rows}
    return {
        "org_id": org.id,
        "org_slug": org.slug,
        "workspaces": len(ws_ids),
        "enrollments_by_state": by_state,
        "credits": {
            "emails": credits.emails,
            "linkedin_dms": credits.linkedin_dms,
            "inmails": credits.inmails,
            "sourced": credits.sourced,
            "used": credits.used,
            "allowance": credits.allowance,
            "pct": credits.pct,
        },
    }


async def _seed_agent_runs(
    session: AsyncSession,
    ws: Workspace,
    campaigns: list[Campaign],
    contacts: list[Contact],
    now: datetime,
    rng: random.Random,
) -> None:
    """Synthesize agent-run traces (design / sourcing / review / reply) behind the seeded campaigns,
    so the cockpit run feed + activity view show the agents having actually done the work.
    """
    Block = tuple[str, str | None, JsonObject]
    planned: list[tuple[AgentRun, list[Block]]] = []

    def plan(
        c: Campaign,
        *,
        role: AgentRole,
        trigger: str,
        summary: str,
        tokens: int,
        when: datetime,
        blocks: list[Block],
    ) -> None:
        run = AgentRun(
            workspace_id=ws.id,
            campaign_id=c.id,
            role=role,
            trigger=trigger,
            status="done",
            summary=summary,
            tokens=tokens,
            started_at=when,
            ended_at=when + timedelta(seconds=rng.randint(4, 22)),
            created_at=when,
        )
        planned.append((run, blocks))

    names = [c.full_name for c in contacts if c.full_name]
    for c in campaigns:
        titles = c.criteria.get("titles") or []
        skills = c.criteria.get("skills") or []
        base = c.created_at or now
        plan(
            c,
            role=AgentRole.strategy,
            trigger="cold_start",
            tokens=rng.randint(1500, 2200),
            when=base + timedelta(hours=1),
            summary="Designed the campaign: set the audience and a multi-step sequence.",
            blocks=[
                (
                    "thought",
                    None,
                    {"text": "Sizing the audience, then setting targeting + sequence."},
                ),
                ("tool_call", "estimate_audience", {"titles": titles}),
                ("result", "estimate_audience", {"estimate": rng.randint(14, 42)}),
                ("tool_call", "set_audience", {"titles": titles, "skills": skills}),
                ("result", "set_audience", {"applied": True}),
                ("tool_call", "set_sequence", {"steps": len(c.sequence or [])}),
                ("result", "set_sequence", {"applied": True}),
                ("thought", None, {"text": "Designed — audience set and sequence drafted."}),
            ],
        )
        if c.status == CampaignStatus.draft:
            continue
        for day in (1, 4, 8):
            when = base + timedelta(days=day)
            if when > now:
                break
            found, imported = rng.randint(18, 40), rng.randint(4, 12)
            plan(
                c,
                role=AgentRole.sourcing,
                trigger="source_due",
                tokens=rng.randint(2000, 3000),
                when=when,
                summary=f"Sourced {found} candidates; imported {imported} strong matches.",
                blocks=[
                    ("tool_call", "search", {"limit": 25}),
                    ("result", "search", {"found": found}),
                    ("tool_call", "import", {"ids": [f"h{i}" for i in range(imported)]}),
                    ("result", "import", {"imported": imported, "enrolled": imported}),
                    ("thought", None, {"text": f"Imported {imported} strong matches."}),
                ],
            )
        plan(
            c,
            role=AgentRole.strategy,
            trigger="review",
            tokens=rng.randint(900, 1400),
            when=now - timedelta(days=rng.randint(1, 3), hours=rng.randint(0, 10)),
            summary="Reviewed the funnel and tightened the audience to lift reply rate.",
            blocks=[
                ("tool_call", "read_funnel", {}),
                (
                    "result",
                    "read_funnel",
                    {"sourced": rng.randint(8, 20), "replied": rng.randint(1, 6)},
                ),
                ("tool_call", "set_audience", {"seniorities": ["senior", "staff"]}),
                ("result", "set_audience", {"applied": True}),
                ("thought", None, {"text": "Tightened the audience toward stronger fits."}),
            ],
        )
        if c.status == CampaignStatus.active and names:
            for _ in range(rng.randint(1, 2)):
                who = rng.choice(names)
                plan(
                    c,
                    role=AgentRole.outreach,
                    trigger="reply",
                    tokens=rng.randint(600, 1000),
                    when=now - timedelta(hours=rng.randint(2, 30)),
                    summary=f"Handled {who}'s reply — answered and proposed a call.",
                    blocks=[
                        (
                            "thought",
                            None,
                            {"text": f"{who} asked about the role — answer + propose a call."},
                        ),
                        (
                            "tool_call",
                            "reply",
                            {"text": "Happy to share more — would a quick call work?"},
                        ),
                        ("result", "reply", {"sent": True}),
                    ],
                )

    runs = [r for r, _ in planned]
    session.add_all(runs)
    await session.flush()
    steps = [
        AgentStep(run_id=run.id, seq=i, kind=kind, tool_name=tool, content=content)
        for run, blocks in planned
        for i, (kind, tool, content) in enumerate(blocks)
    ]
    session.add_all(steps)
    await session.flush()


# --- current-period activity (drives the pooled credit meter) ----------------
# Credit usage is *derived* from in-period source rows (Message.sent_at, Enrollment.created_at), so
# the historically-dated narrative activity above reads as zero usage at the start of a period. Seed
# a known batch of *this-period* activity so the meter reflects real usage and the derived calc is
# verifiable end-to-end:  used = emails + inmails*2 + sourced = 600 + 100*2 + 200 = 1000 credits.
_PERIOD_SOURCED = 200  # enrollments created this period
_PERIOD_EMAILS = 600  # emails sent this period       (1 credit each)
_PERIOD_INMAILS = 100  # LinkedIn InMails this period  (2 credits each)


def _split(total: int, parts: int) -> list[int]:
    """Split `total` into `parts` near-equal integers that sum back to `total`."""
    base, extra = divmod(total, parts)
    return [base + (1 if i < extra else 0) for i in range(parts)]


def _in_period(now: datetime, i: int, n: int) -> datetime:
    """A timestamp inside the current usage period, spread across [period_start, now]."""
    start = period_start(now)
    return min(now, start + (now - start) * (i / n if n else 0.0))


async def _seed_current_period_activity(
    session: AsyncSession,
    *,
    verticals: list[tuple[Workspace, str]],
    now: datetime,
    rng: random.Random,
) -> None:
    """Fresh in-period sourced enrollments + sent messages (real names, spread across the verticals)
    so the account credit meter shows real usage and the derived calc is testable."""
    enrollments: list[Enrollment] = []
    for (ws, kind), share in zip(verticals, _split(_PERIOD_SOURCED, len(verticals)), strict=True):
        campaign = (
            await session.execute(
                select(Campaign)
                .where(Campaign.workspace_id == ws.id, Campaign.status == CampaignStatus.active)
                .limit(1)
            )
        ).scalar_one_or_none()
        if campaign is None:
            continue
        contacts = [Contact(**cd) for cd in make_contacts(ws.id, kind, share, rng=rng)]
        session.add_all(contacts)
        await session.flush()
        for c in contacts:
            score, rationale = evaluate(c, campaign.criteria or {})
            enrollments.append(
                Enrollment(
                    workspace_id=ws.id,
                    campaign_id=campaign.id,
                    contact_id=c.id,
                    state=EnrollmentState.active,
                    score=score,
                    score_rationale=rationale,
                    current_step=1,
                    created_at=_in_period(now, len(enrollments), _PERIOD_SOURCED),
                )
            )
    session.add_all(enrollments)
    await session.flush()
    if not enrollments:
        return

    def _sent(channel: Channel, i: int, total: int) -> Message:
        enr = enrollments[i % len(enrollments)]
        ts = _in_period(now, i, total)
        return Message(
            workspace_id=enr.workspace_id,
            enrollment_id=enr.id,
            direction=MessageDirection.outbound,
            channel=channel,
            status=MessageStatus.sent,
            subject="Quick question about a role" if channel == Channel.email else None,
            body="Reaching out about a role that lines up with your background.",
            sent_at=ts,
            created_at=ts,
        )

    session.add_all(
        [_sent(Channel.email, i, _PERIOD_EMAILS) for i in range(_PERIOD_EMAILS)]
        + [_sent(Channel.linkedin, i, _PERIOD_INMAILS) for i in range(_PERIOD_INMAILS)]
    )
    await session.flush()


async def seed_demo(
    session: AsyncSession, *, reset: bool = True, now: datetime | None = None, rng_seed: int = 7
) -> DemoSummary:
    """Build the three-vertical demo dataset for org 'Acme Talent'. Returns a small summary."""
    rng = random.Random(rng_seed)
    now = now or datetime.now(UTC)
    if reset:
        await _reset(session)

    org, admin = await _org_and_admin(session)
    actor_ids, team = await _seed_team(session, org=org, admin=admin)

    ws_recruit = Workspace(organization_id=org.id, name="Recruiting", kind=WorkspaceKind.team)
    ws_sales = Workspace(
        organization_id=org.id,
        name="Enterprise Sales",
        kind=WorkspaceKind.team,
        settings={"vertical": "sales"},
    )
    ws_partner = Workspace(organization_id=org.id, name="Partnerships", kind=WorkspaceKind.team)
    session.add_all([ws_recruit, ws_sales, ws_partner])
    await session.flush()

    # Plain members reach only the workspaces they're granted; the admin and compliance see all.
    session.add_all(
        [SpaceGrant(user_id=team["Dana Okafor"].id, workspace_id=ws_recruit.id)]
        + [
            SpaceGrant(user_id=team["Riley Walsh"].id, workspace_id=ws.id, role=SpaceRole.admin)
            for ws in (ws_recruit, ws_sales, ws_partner)
        ]
    )
    await session.flush()

    await _seed_workspace(session, ws_recruit, "eng", RECRUIT_CAMPAIGNS, 18, now, rng, owner=admin)
    await _seed_workspace(session, ws_sales, "sales", SALES_CAMPAIGNS, 16, now, rng, owner=admin)
    await _seed_workspace(
        session, ws_partner, "partner", PARTNER_CAMPAIGNS, 14, now, rng, owner=admin
    )

    ws_ids = [ws_recruit.id, ws_sales.id, ws_partner.id]
    await _generate_audit(session, org.id, ws_ids, actor_ids, now)
    await _seed_current_period_activity(
        session,
        verticals=[(ws_recruit, "eng"), (ws_sales, "sales"), (ws_partner, "partner")],
        now=now,
        rng=rng,
    )
    status = await credit_status(session, organization_id=org.id, plan=org.plan, now=now)
    return await _summary(session, org, ws_ids, status)
