"""Turn raw adsb.lol aircraft dicts into enriched Flight objects."""

from __future__ import annotations

import math
import time
from functools import lru_cache
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Optional

import requests

from .compat import tomllib
from . import lookup
from .settings import DATA_DIR
from .sources import USER_AGENT

EMERGENCY_SQUAWKS = {"7500": "hijack", "7600": "radio failure", "7700": "emergency"}
ROUTE_URL = "https://vrs-standing-data.adsb.lol/routes/{prefix}/{callsign}.json"
ROUTE_CACHE_SECONDS = 6 * 3600


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

EARTH_RADIUS_NM = 3440.065


def distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_NM * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compass bearing from point 1 to point 2 (0 = north, 90 = east)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


# ---------------------------------------------------------------------------
# Flight
# ---------------------------------------------------------------------------

@dataclass
class Airport:
    icao: Optional[str]
    iata: Optional[str]
    city: Optional[str]
    name: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None

    @property
    def code(self) -> str:
        return self.iata or self.icao or "?"


@dataclass
class Flight:
    hex: str
    callsign: Optional[str]
    lat: float
    lon: float
    altitude_ft: Optional[int]          # None when on the ground or unknown
    on_ground: bool
    ground_speed_kt: Optional[float]
    vertical_rate_fpm: Optional[int]
    track_deg: Optional[float]
    squawk: Optional[str]
    emergency: Optional[str]            # None, or a short description
    distance_nm: float
    bearing_deg: float                  # from home to the plane
    registration: Optional[str] = None
    type_code: Optional[str] = None     # ICAO type, e.g. A359
    type_name: Optional[str] = None     # e.g. Airbus A-350-900 XWB
    type_manufacturer: Optional[str] = None
    type_model: Optional[str] = None    # e.g. A-350-900 XWB
    airline_icao: Optional[str] = None
    airline_iata: Optional[str] = None
    airline_name: Optional[str] = None
    origin: Optional[Airport] = None
    destination: Optional[Airport] = None
    route_source: Optional[str] = None  # "online", "offline", or None
    seen_s: Optional[float] = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def distance_km(self) -> float:
        return self.distance_nm * 1.852

    @property
    def flight_number(self) -> Optional[str]:
        """What to call the flight on screen.

        Planes broadcast an ATC callsign, not the ticket flight number. When the
        part after the airline code is all digits it is usually the real flight
        number, so AFR740 -> AF740. Letter codes (SAS34L, AFR46TD) have no free
        mapping to the real number, so the callsign is shown as broadcast.
        """
        if not self.callsign:
            return None
        icao, iata = self.airline_icao, self.airline_iata
        if icao and iata and self.callsign.startswith(icao):
            suffix = self.callsign[len(icao):]
            if suffix.isdigit():
                return iata + suffix
        return self.callsign

    @property
    def vertical_trend(self) -> str:
        rate = self.vertical_rate_fpm
        if rate is None or abs(rate) < 300:
            return "level"
        return "climbing" if rate > 0 else "descending"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw")
        d.update(
            distance_km=round(self.distance_km, 2),
            flight_number=self.flight_number,
            vertical_trend=self.vertical_trend,
        )
        return d


def _num(value: Any) -> Optional[float]:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def predict(f: Flight, seconds: float, home_lat: float, home_lon: float) -> Flight:
    """Where the plane probably is `seconds` after it was reported: moved
    along its track at its ground speed, and up or down at its vertical rate.
    Used between API updates so altitude and distance keep moving."""
    if seconds <= 0 or f.on_ground:
        return f
    lat, lon, alt = f.lat, f.lon, f.altitude_ft
    if f.ground_speed_kt and f.track_deg is not None:
        nm = f.ground_speed_kt * seconds / 3600
        rad = math.radians(f.track_deg)
        lat += nm * math.cos(rad) / 60
        lon += nm * math.sin(rad) / (60 * max(0.01, math.cos(math.radians(lat))))
    if alt is not None and f.vertical_rate_fpm:
        alt = max(0, round(alt + f.vertical_rate_fpm * seconds / 60))
    return replace(
        f, lat=lat, lon=lon, altitude_ft=alt,
        distance_nm=distance_nm(home_lat, home_lon, lat, lon),
        bearing_deg=bearing_deg(home_lat, home_lon, lat, lon),
    )


def parse_aircraft(ac: dict[str, Any], home_lat: float, home_lon: float) -> Optional[Flight]:
    """Build a Flight from one raw aircraft dict. Returns None if it has no position."""
    lat, lon = _num(ac.get("lat")), _num(ac.get("lon"))
    if lat is None or lon is None:
        return None

    alt = ac.get("alt_baro")
    on_ground = alt == "ground"
    altitude = int(alt) if _num(alt) is not None else None

    squawk = ac.get("squawk")
    emergency = ac.get("emergency")
    if emergency in (None, "", "none"):
        emergency = EMERGENCY_SQUAWKS.get(squawk)

    rate = _num(ac.get("baro_rate"))
    if rate is None:
        rate = _num(ac.get("geom_rate"))

    return Flight(
        hex=ac.get("hex", "").lower(),
        callsign=(ac.get("flight") or "").strip().upper() or None,
        lat=lat,
        lon=lon,
        altitude_ft=altitude,
        on_ground=on_ground,
        # Ground speed; if missing, true airspeed is a close stand-in.
        ground_speed_kt=_num(ac.get("gs")) if _num(ac.get("gs")) is not None else _num(ac.get("tas")),
        vertical_rate_fpm=int(rate) if rate is not None else None,
        track_deg=_num(ac.get("track")),
        squawk=squawk,
        emergency=emergency,
        distance_nm=distance_nm(home_lat, home_lon, lat, lon),
        bearing_deg=bearing_deg(home_lat, home_lon, lat, lon),
        registration=ac.get("r"),
        type_code=ac.get("t"),
        seen_s=_num(ac.get("seen")),
        raw=ac,
    )


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _aircraft_names() -> dict[str, str]:
    path = DATA_DIR / "aircraft_names.toml"
    try:
        with path.open("rb") as fh:
            return {k.upper(): v for k, v in tomllib.load(fh).items()}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        import logging
        logging.getLogger("planeoverme").error("could not read %s: %s", path, exc)
        return {}


def airliner_types() -> set[str]:
    """ICAO type codes counted as airliners: the ones in aircraft_names.toml."""
    return set(_aircraft_names())


def enrich_basic(f: Flight) -> Flight:
    """Airline and aircraft details. Local lookups only, so it is fast."""
    record = lookup.aircraft(f.hex) if f.hex else None
    if record:
        f.registration = f.registration or record["registration"]
        f.type_code = f.type_code or record["type"]

    if f.type_code:
        mt = lookup.model_type(f.type_code)
        if mt:
            f.type_manufacturer, f.type_model = mt["manufacturer"], mt["model"]
    if not f.type_model and record and record["model"]:
        f.type_manufacturer, f.type_model = record["manufacturer"], record["model"]
    if f.type_model:
        f.type_name = " ".join(p for p in (f.type_manufacturer, f.type_model) if p)
    if f.type_code and f.type_code.upper() in _aircraft_names():
        f.type_name = _aircraft_names()[f.type_code.upper()]

    # Airline callsigns are 3 letters + a flight number (AFR740, EZY12AB).
    cs = f.callsign or ""
    if len(cs) > 3 and cs[:3].isalpha() and cs[3].isdigit():
        airline = lookup.airline_by_icao(cs[:3])
        if airline:
            f.airline_icao = airline["icao"]
            f.airline_iata = airline["iata"]
            f.airline_name = airline["name"]
    return f


_route_cache: dict[str, tuple[float, Optional[list[Airport]]]] = {}
_http = requests.Session()
_http.headers["User-Agent"] = USER_AGENT


def _airport_from_lookup(icao: str) -> Airport:
    a = lookup.airport(icao)
    if not a:
        return Airport(icao=icao, iata=None, city=None)
    return Airport(a["icao"], a["iata"], a["city"], a["name"], a["lat"], a["lon"])


def _route_online(callsign: str, timeout: float) -> Optional[list[Airport]]:
    url = ROUTE_URL.format(prefix=callsign[:2], callsign=callsign)
    resp = _http.get(url, timeout=timeout)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    data = resp.json()
    return [
        Airport(a.get("icao"), a.get("iata"), a.get("location"), a.get("name"),
                a.get("lat"), a.get("lon"))
        for a in data.get("_airports") or []
    ]


def _pick_leg(stops: list[Airport], lat: float, lon: float) -> tuple[Airport, Airport]:
    """For multi-stop routes (CDG-NKC-CKY), pick the leg the plane is on now:
    the one where going via the plane adds the least extra distance."""
    best, best_detour = (stops[0], stops[-1]), math.inf
    for a, b in zip(stops, stops[1:]):
        if None in (a.lat, a.lon, b.lat, b.lon):
            continue
        detour = (distance_nm(a.lat, a.lon, lat, lon) + distance_nm(lat, lon, b.lat, b.lon)
                  - distance_nm(a.lat, a.lon, b.lat, b.lon))
        if detour < best_detour:
            best, best_detour = (a, b), detour
    return best


def enrich_route(f: Flight, online: bool = True, timeout: float = 5) -> Flight:
    """Origin and destination. Tries adsb.lol's route files, then the offline copy.
    Network calls are cached per callsign, so call this only for planes you show."""
    if not f.callsign:
        return f

    stops: Optional[list[Airport]] = None
    source = None
    cached = _route_cache.get(f.callsign)
    if cached and time.time() - cached[0] < ROUTE_CACHE_SECONDS:
        stops, source = cached[1], "online"
    elif online:
        try:
            stops = _route_online(f.callsign, timeout)
            _route_cache[f.callsign] = (time.time(), stops)
            source = "online"
        except (requests.RequestException, ValueError):
            stops = None

    if not stops:
        codes = lookup.route_codes(f.callsign)
        if codes:
            stops, source = [_airport_from_lookup(c) for c in codes], "offline"

    if stops and len(stops) >= 2:
        f.origin, f.destination = _pick_leg(stops, f.lat, f.lon)
        f.route_source = source
    return f
