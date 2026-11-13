"""Which planes are allowed on screen, and in what order.

Priority tiers (lower wins):
    0  emergency squawk
    1  watchlist (airline, type or registration you listed)
    2  everything else that passes the rules
Within a tier, the closest plane wins, then the lowest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .flight import Flight, airliner_types

EMERGENCY, WATCHLIST, NORMAL = 0, 1, 2
TIER_NAMES = {EMERGENCY: "emergency", WATCHLIST: "watchlist", NORMAL: "normal"}


@dataclass
class Verdict:
    eligible: bool
    tier: int = NORMAL
    reasons: list[str] = field(default_factory=list)  # why it was rejected
    watch_match: str | None = None                    # what matched the watchlist

    @property
    def tier_name(self) -> str:
        return TIER_NAMES[self.tier]


def _norm_reg(reg: str | None) -> str:
    return (reg or "").replace("-", "").upper()


class Rules:
    def __init__(self, settings: dict[str, Any]):
        r = settings["rules"]
        self.max_distance_km = r["max_distance_km"]
        self.max_altitude_ft = r["max_altitude_ft"] or None
        self.airlines_only = r["airlines_only"]
        # Airline callsigns alone let business-jet operators through (NetJets,
        # VistaJet): also require an airliner type when the type is known.
        self.airliners_only = r.get("airliners_only", True)
        self.exclude_airlines = {a.upper() for a in r.get("exclude_airlines", [])}
        self.max_position_age_s = r["max_position_age_s"]

        w = r["watchlist"]
        self.watch_distance_km = w["max_distance_km"]
        self.watch_airlines = {a.upper() for a in w["airlines"]}
        self.watch_types = {t.upper() for t in w["types"]}
        self.watch_regs = {_norm_reg(x) for x in w["registrations"]}

        self.emergency_distance_km = r["emergency"]["max_distance_km"]

    def watch_match(self, f: Flight) -> str | None:
        if f.airline_icao and f.airline_icao in self.watch_airlines:
            return f"airline {f.airline_icao}"
        if f.type_code and f.type_code.upper() in self.watch_types:
            return f"type {f.type_code}"
        if f.registration and _norm_reg(f.registration) in self.watch_regs:
            return f"registration {f.registration}"
        return None

    def evaluate(self, f: Flight) -> Verdict:
        """Needs enrich_basic() to have run (airline and type are used)."""
        if f.on_ground:
            return Verdict(False, reasons=["on the ground"])
        if f.seen_s is not None and f.seen_s > self.max_position_age_s:
            return Verdict(False, reasons=[f"position {f.seen_s:.0f}s old"])

        if f.emergency and f.distance_km <= self.emergency_distance_km:
            return Verdict(True, EMERGENCY)

        # Watchlist planes have their own distance and skip the altitude and
        # airlines-only limits: an A380 cruising overhead is worth seeing.
        match = self.watch_match(f)
        if match and f.distance_km <= self.watch_distance_km:
            return Verdict(True, WATCHLIST, watch_match=match)

        reasons = []
        if f.distance_km > self.max_distance_km:
            reasons.append(f"{f.distance_km:.0f} km away (max {self.max_distance_km})")
        if self.max_altitude_ft and f.altitude_ft and f.altitude_ft > self.max_altitude_ft:
            reasons.append(f"{f.altitude_ft} ft high (max {self.max_altitude_ft})")
        if self.airlines_only and not f.airline_iata:
            reasons.append("not an airline flight")
        airliners = airliner_types()
        # If the airliner list failed to load, skip this check rather than
        # rejecting every plane.
        if self.airliners_only and airliners and f.type_code and f.type_code.upper() not in airliners:
            reasons.append(f"not an airliner ({f.type_name or f.type_code})")
        if f.airline_icao and f.airline_icao.upper() in self.exclude_airlines:
            reasons.append(f"excluded airline ({f.airline_icao})")
        return Verdict(not reasons, NORMAL, reasons, match)


def sort_key(f: Flight, v: Verdict) -> tuple:
    return (v.tier, round(f.distance_km, 1), f.altitude_ft or 0)
