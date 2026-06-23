"""Send governor — enforces daily caps, business-hours windows, and account warmup.

`can_send_now` returns `(allowed, retry_at)`. Daily caps are enforced by default (counting today's
sent messages per channel against the workspace's configured cap). Business-hours windows and
warmup ramp are opt-in via workspace settings, so existing flows/tests aren't throttled until a
workspace turns them on.
"""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import JsonObject
from app.models import Channel, Message, MessageDirection, MessageStatus, Workspace

_DEFAULT_CAP = {Channel.email: 120, Channel.linkedin: 80}
_WARMUP_DAYS = 14
_MIN_WARMUP_CAP = 5


def _as_int(value: object, default: int) -> int:
    """Coerce a JSONB setting (str/number/bool) to int, mirroring the prior `int(...)` call."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float | str):
        return int(value)
    return default


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
    session: AsyncSession, *, workspace_id: str, channel: Channel, now: datetime
) -> tuple[bool, datetime | None]:
    ws = await session.get(Workspace, workspace_id)
    settings: JsonObject = (ws.settings if ws else {}) or {}

    # Business-hours window (opt-in).
    if settings.get("sending_window_enabled"):
        start_h = _as_int(settings.get("send_window_start", 8), 8)
        end_h = _as_int(settings.get("send_window_end", 18), 18)
        weekdays_only = bool(settings.get("send_weekdays_only", True))
        in_days = now.weekday() < 5 if weekdays_only else True
        if not (in_days and start_h <= now.hour < end_h):
            return False, _next_window_start(now, start_h, weekdays_only)

    # Daily cap (enforced) + warmup ramp (opt-in).
    cap_key = "daily_cap_linkedin" if channel == Channel.linkedin else "daily_cap_email"
    default_cap = _DEFAULT_CAP.get(channel, 120)
    cap = _as_int(settings.get(cap_key, default_cap), default_cap)
    if settings.get("warmup_enabled") and ws is not None:
        age_days = max(0, (now - ws.created_at).days)
        cap = max(_MIN_WARMUP_CAP, int(cap * min(1.0, (age_days + 1) / _WARMUP_DAYS)))

    sent_today = (
        await session.execute(
            select(func.count())
            .select_from(Message)
            .where(
                Message.workspace_id == workspace_id,
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
