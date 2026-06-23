"""Deterministic test fakes for IO-free tests (clock now; LLM/channels/sources as those land)."""

from datetime import UTC, datetime, timedelta


class FakeClock:
    """An injectable clock for time-dependent logic (e.g. the runtime scheduler)."""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs: float) -> None:
        self._now += timedelta(**kwargs)


__all__ = ["FakeClock"]
