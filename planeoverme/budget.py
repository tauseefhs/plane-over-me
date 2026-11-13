"""Pace API requests so a daily allowance lasts all day.

adsb.lol allows 500 requests per day (plus 60 per minute), counted per UTC
day. The budget spreads what is left over the active (non-quiet) time until
the next reset, never asking more often than the configured minimum interval.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

DAY_S = 86400


def next_utc_midnight(now: float) -> float:
    day = int(now // DAY_S) * DAY_S
    return day + DAY_S


def parse_quiet_hours(text: str) -> Optional[tuple[int, int]]:
    """'23:00-07:00' -> (1380, 420) in minutes after midnight, or None."""
    text = (text or "").strip()
    if not text:
        return None
    start, end = (part.strip() for part in text.split("-"))
    to_min = lambda hm: int(hm.split(":")[0]) * 60 + int(hm.split(":")[1])
    return to_min(start), to_min(end)


@dataclass
class Budget:
    daily_limit: int = 500
    min_interval_s: float = 15
    quiet: Optional[tuple[int, int]] = None   # local minutes, may wrap midnight
    reserve: int = 20                         # requests kept back for safety

    def is_quiet(self, ts: float) -> bool:
        if not self.quiet:
            return False
        t = dt.datetime.fromtimestamp(ts)
        m = t.hour * 60 + t.minute
        start, end = self.quiet
        return start <= m < end if start < end else (m >= start or m < end)

    def quiet_ends_in(self, ts: float) -> float:
        """Seconds until quiet hours end (0 if not quiet now)."""
        if not self.is_quiet(ts):
            return 0.0
        step, waited = 60.0, 0.0
        while self.is_quiet(ts + waited) and waited < DAY_S:
            waited += step
        return waited

    def active_seconds(self, start: float, end: float) -> float:
        """Seconds between start and end that are outside quiet hours
        (minute resolution, plenty for planning)."""
        if not self.quiet:
            return max(0.0, end - start)
        total, t = 0.0, start
        while t < end:
            step = min(60.0, end - t)
            if not self.is_quiet(t):
                total += step
            t += step
        return total

    def interval(self, now: float, remaining: int) -> float:
        """Seconds to wait before the next request, given how many requests
        are left until the next daily reset."""
        usable = remaining - self.reserve
        reset = next_utc_midnight(now)
        if usable <= 0:
            return reset - now + 5          # nothing left: wait for the reset
        active = self.active_seconds(now, reset)
        return max(self.min_interval_s, active / usable)
