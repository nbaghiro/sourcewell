"""Send governor — enforces daily caps, business-hours windows, and account warmup.

`can_send_now` returns `(allowed, retry_at)`. Every knob is read through the policy chain
(`app.core.policy`), so a partner, an organization, a workspace or the campaign itself can set it.
Daily caps are enforced by default; the business-hours window and the warmup ramp are opt-in, so
existing flows aren't throttled until someone turns them on.
"""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import policy as policy_chain
from app.models import Campaign, Channel, Message, MessageDirection, MessageStatus, Workspace

_WARMUP_DAYS = 14
_MIN_WARMUP_CAP = 5


def _start_of_day(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _next_window_start(now: datetime, start_h: int, weekdays_only: bool) -> datetime:
    candidate = now.replace(hour=start_h, minute=0, second=0, microsecond=0)
    if now.hour >= start_h:
        candidate += timedelta(days=1)
    if weekdays_only:
        while candidate.weekday() >= 5:  # Sat/Sun
            candidate += timedelta(days=1)
    return candidate


async def can_send_now(
    session: AsyncSession, *, campaign: Campaign, channel: Channel, now: datetime
) -> tuple[bool, datetime | None]:
    policy = await policy_chain.for_campaign(session, campaign=campaign)

    if policy.get_bool("sending_window_enabled"):
        start_h = policy.get_int("send_window_start")
        end_h = policy.get_int("send_window_end")
        weekdays_only = policy.get_bool("send_weekdays_only")
        in_days = now.weekday() < 5 if weekdays_only else True
        if not (in_days and start_h <= now.hour < end_h):
            return False, _next_window_start(now, start_h, weekdays_only)

    cap = policy.get_int("daily_cap_linkedin" if channel == Channel.linkedin else "daily_cap_email")
    if policy.get_bool("warmup_enabled"):
        workspace = await session.get(Workspace, campaign.workspace_id)
        if workspace is not None:
            age_days = max(0, (now - workspace.created_at).days)
            cap = max(_MIN_WARMUP_CAP, int(cap * min(1.0, (age_days + 1) / _WARMUP_DAYS)))

    sent_today = (
        await session.execute(
            select(func.count())
            .select_from(Message)
            .where(
                Message.workspace_id == campaign.workspace_id,
                Message.channel == channel,
                Message.direction == MessageDirection.outbound,
                Message.status == MessageStatus.sent,
                Message.sent_at >= _start_of_day(now),
            )
        )
    ).scalar_one()
    if sent_today >= cap:
        return False, _start_of_day(now) + timedelta(days=1)
    return True, None
