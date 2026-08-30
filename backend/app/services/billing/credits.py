"""Account-level usage credits — one monthly pool the three metered actions draw from.

An account (organization) gets a monthly credit allowance by plan. Messages sent (email, LinkedIn
DM, LinkedIn InMail) and candidates sourced each consume credits at a weight. Usage is *derived*
from the source rows (`Message.sent_at`, `Enrollment.created_at`) — one source of truth, with no
separate counter to drift. Enforcement is soft for now: we surface usage and how far over the
allowance it is, but overage is allowed (reconciled at billing). Swap `period_start` to the Stripe
billing cycle once subscriptions land; everything else flows from the plan on `Organization`.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, Enrollment, Message, MessageDirection, MessageStatus, Workspace

# Credits one action consumes — tunable. InMails are scarcer (they spend the seat's own finite
# LinkedIn credits); sourcing hits paid people-data providers; an email or an ordinary LinkedIn DM
# is cheap. A DM is *not* an InMail: only a send that actually went out with the InMail flag costs
# the premium weight.
CREDIT_WEIGHTS = {"email": 1, "linkedin": 1, "inmail": 2, "sourced": 1}

# Monthly credit allowance by plan. "trial" is free-tier (not a Pro-level giveaway); demo / unknown
# fall back to a generous default (so the seeded demo isn't throttled).
PLAN_CREDITS = {"free": 200, "trial": 200, "pro": 5_000, "premium": 25_000}
_DEFAULT_CREDITS = 5_000


def monthly_allowance(plan: str) -> int:
    return PLAN_CREDITS.get(plan.lower(), _DEFAULT_CREDITS)


def period_start(now: datetime) -> datetime:
    """Start of the current usage period — the calendar month (UTC) for now; the Stripe billing
    cycle once subscriptions land."""
    return datetime(now.year, now.month, 1, tzinfo=UTC)


@dataclass(frozen=True)
class CreditStatus:
    emails: int  # emails sent this period
    linkedin_dms: int  # ordinary LinkedIn messages sent this period
    inmails: int  # LinkedIn InMails sent this period
    sourced: int  # candidates sourced (enrollments created) this period
    used: int  # weighted credits consumed
    allowance: int  # the plan's monthly credits
    period_start: datetime

    @property
    def over(self) -> bool:
        return self.used > self.allowance

    @property
    def pct(self) -> int:
        return round(100 * self.used / self.allowance) if self.allowance else 0


async def credit_status(
    session: AsyncSession,
    *,
    organization_id: str,
    plan: str,
    now: datetime,
    period_start_at: datetime | None = None,
) -> CreditStatus:
    """The account's pooled credit usage for the current period, across all its workspaces. When the
    org has a Stripe billing window (`period_start_at`), usage resets on that cycle; else the
    calendar month."""
    start = period_start_at or period_start(now)
    sent = (
        (
            await session.execute(
                select(Message.channel, Message.is_inmail, func.count())
                .select_from(Message)
                .join(Workspace, Message.workspace_id == Workspace.id)
                .where(
                    Workspace.organization_id == organization_id,
                    Message.direction == MessageDirection.outbound,
                    Message.status == MessageStatus.sent,
                    Message.sent_at >= start,
                )
                .group_by(Message.channel, Message.is_inmail)
            )
        )
        .tuples()
        .all()
    )
    # Keyed on (channel, is_inmail): a LinkedIn row is an InMail only when the transport sent it
    # as one. Everything on email is a plain message whatever the flag says.
    by_kind: dict[tuple[Channel, bool], int] = {(ch, bool(im)): int(n) for ch, im, n in sent}
    emails = sum(n for (ch, _), n in by_kind.items() if ch == Channel.email)
    linkedin_dms = by_kind.get((Channel.linkedin, False), 0)
    inmails = by_kind.get((Channel.linkedin, True), 0)
    sourced = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Enrollment)
                .join(Workspace, Enrollment.workspace_id == Workspace.id)
                .where(
                    Workspace.organization_id == organization_id,
                    Enrollment.created_at >= start,
                )
            )
        ).scalar_one()
    )
    used = (
        emails * CREDIT_WEIGHTS["email"]
        + linkedin_dms * CREDIT_WEIGHTS["linkedin"]
        + inmails * CREDIT_WEIGHTS["inmail"]
        + sourced * CREDIT_WEIGHTS["sourced"]
    )
    return CreditStatus(
        emails=emails,
        linkedin_dms=linkedin_dms,
        inmails=inmails,
        sourced=sourced,
        used=used,
        allowance=monthly_allowance(plan),
        period_start=start,
    )
