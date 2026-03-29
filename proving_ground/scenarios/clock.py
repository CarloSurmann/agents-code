"""Simulated Clock — Controls time progression in the proving ground.

The clock is injected into mock providers so that time-dependent calculations
(days_overdue, watch_sent_folder since, etc.) use simulated time instead of
the real system clock.

The real system clock is never monkeypatched — the agent loop, tracing,
and Anthropic SDK all use real time. Only mock providers consult this clock.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


class SimulatedClock:
    """A controllable clock for test scenarios.

    Usage:
        clock = SimulatedClock(start=date(2026, 3, 15))
        clock.today()           # date(2026, 3, 15)
        clock.advance(days=7)
        clock.today()           # date(2026, 3, 22)
    """

    def __init__(self, start: date | datetime | None = None):
        if start is None:
            self._now = datetime.now(timezone.utc)
        elif isinstance(start, datetime):
            self._now = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
        else:
            self._now = datetime(start.year, start.month, start.day, 9, 0, 0, tzinfo=timezone.utc)
        self._frozen = False

    def now(self) -> datetime:
        """Current simulated datetime (UTC)."""
        return self._now

    def today(self) -> date:
        """Current simulated date."""
        return self._now.date()

    def advance(self, days: int = 0, hours: int = 0, minutes: int = 0, **kwargs) -> None:
        """Move time forward by the specified duration."""
        self._now += timedelta(days=days, hours=hours, minutes=minutes, **kwargs)

    def set(self, when: date | datetime) -> None:
        """Jump to a specific point in time."""
        if isinstance(when, datetime):
            self._now = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        else:
            self._now = datetime(when.year, when.month, when.day, 9, 0, 0, tzinfo=timezone.utc)

    def freeze(self) -> None:
        """Freeze the clock (advance/set become no-ops). Useful for deterministic snapshots."""
        self._frozen = True

    def unfreeze(self) -> None:
        """Resume normal clock operation."""
        self._frozen = False

    def __repr__(self) -> str:
        return f"SimulatedClock({self._now.isoformat()})"
