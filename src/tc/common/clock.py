"""Time helpers. Everything is stored in UTC; the "day" of a partition is the Europe/Paris date."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_utc(epoch_s: int | float) -> datetime:
    return datetime.fromtimestamp(epoch_s, UTC)


def paris_date(ts: datetime) -> date:
    return ts.astimezone(PARIS).date()


def paris_hour(ts: datetime) -> int:
    return ts.astimezone(PARIS).hour


def hour_start(ts: datetime) -> datetime:
    """Start of the UTC hour containing ts."""
    return ts.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def hour_stamp(ts: datetime) -> str:
    """Unambiguous hour label, e.g. 20261116T09Z (a bare HH repeats on DST change days)."""
    return hour_start(ts).strftime("%Y%m%dT%HZ")


def day_length_s(day: date) -> int:
    """Seconds in a Paris calendar day (82800 / 86400 / 90000 around DST changes)."""
    start = datetime(day.year, day.month, day.day, tzinfo=PARIS)
    nxt = day + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=PARIS)
    return int((end.astimezone(UTC) - start.astimezone(UTC)).total_seconds())


class Ticker:
    """Fixed-rate ticks on a monotonic clock: no drift, and no burst after a long overrun.

    next_wait() returns how long to sleep until the next tick. If an iteration overran one or
    more ticks, those are skipped (and counted) instead of being fired back to back.
    """

    def __init__(self, period_s: float, monotonic: Callable[[], float] = time.monotonic):
        self.period = period_s
        self._mono = monotonic
        self._next = monotonic()
        self.skipped = 0

    def next_wait(self) -> float:
        self._next += self.period
        now = self._mono()
        if self._next <= now:
            missed = int((now - self._next) // self.period) + 1
            self.skipped += missed
            self._next += missed * self.period
        return max(0.0, self._next - now)
