"""Decides which plane is on screen over time.

Rules (see [timing] in settings.toml):
- A plane stays on screen at least min_display_s while it still qualifies.
- A higher tier (emergency > watchlist > normal) can take over at any time.
- After min_display_s, switch to the best plane if it is a different one.
- After max_display_s, switch to the next best plane if there is one
  (emergencies stay as long as they qualify).
- A plane that leaves the rules (too far, too high) is dropped right away.
  If it just drops out of the data, keep it for missing_grace_s.
- A plane that was shown is on cooldown for cooldown_min (emergencies ignore it).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from .flight import Flight
from .rules import EMERGENCY, Verdict, sort_key


@dataclass
class Selection:
    flight: Flight
    verdict: Verdict
    shown_since: float
    last_ok: float        # last time it was in the data and eligible
    why: str              # human-readable reason it was picked


class Tracker:
    def __init__(self, settings: dict[str, Any]):
        t = settings["timing"]
        self.min_display_s = t["min_display_s"]
        self.max_display_s = t.get("max_display_s") or None
        self.cooldown_s = t["cooldown_min"] * 60
        self.grace_s = t["missing_grace_s"]
        self.current: Optional[Selection] = None
        self.cooldown: dict[str, float] = {}   # hex -> time the cooldown ends
        self.history: list[dict[str, Any]] = []  # recent switches, newest first

    def in_cooldown(self, hex_code: str, now: float) -> bool:
        return self.cooldown.get(hex_code, 0) > now

    def _retire(self, now: float, reason: str) -> None:
        cur = self.current
        if cur:
            self.cooldown[cur.flight.hex] = now + self.cooldown_s
            self.history.insert(0, {
                "hex": cur.flight.hex, "callsign": cur.flight.callsign,
                "shown_s": round(now - cur.shown_since), "ended": reason, "at": now,
            })
            del self.history[20:]
        self.current = None

    def update(self, evaluated: list[tuple[Flight, Verdict]],
               now: Optional[float] = None) -> Optional[Selection]:
        now = time.time() if now is None else now
        self.cooldown = {h: t for h, t in self.cooldown.items() if t > now}

        eligible = {f.hex: (f, v) for f, v in evaluated if v.eligible}
        present = {f.hex for f, _ in evaluated}
        cur = self.current

        # Refresh the current plane with its latest position, or let it go.
        if cur:
            if cur.flight.hex in eligible:
                cur.flight, cur.verdict = eligible[cur.flight.hex]
                cur.last_ok = now
            elif cur.flight.hex in present:
                self._retire(now, "no longer matches the rules")
                cur = None
            elif now - cur.last_ok > self.grace_s:
                self._retire(now, "lost from the data")
                cur = None

        candidates = sorted(
            (fv for h, fv in eligible.items()
             if fv[1].tier == EMERGENCY or not self.in_cooldown(h, now)
             or (cur and h == cur.flight.hex)),
            key=lambda fv: sort_key(*fv),
        )
        if not candidates:
            return self.current

        best_f, best_v = candidates[0]
        if cur:
            shown_for = now - cur.shown_since
            others = [fv for fv in candidates if fv[0].hex != cur.flight.hex]
            time_up = (self.max_display_s and shown_for >= self.max_display_s
                       and cur.verdict.tier != EMERGENCY and others)
            if time_up:
                # Its turn is over: hand over to the best of the others.
                best_f, best_v = others[0]
            elif best_f.hex == cur.flight.hex:
                return cur
            elif best_v.tier >= cur.verdict.tier and shown_for < self.min_display_s:
                return cur
            reason = "time up" if time_up else "replaced"
            self._retire(now, f"{reason}, now {best_f.callsign or best_f.hex}")

        if best_v.tier == EMERGENCY:
            why = f"emergency ({best_f.emergency})"
        elif best_v.watch_match:
            why = f"watchlist: {best_v.watch_match}"
        else:
            why = "closest"
        self.current = Selection(best_f, best_v, now, now, why)
        return self.current
