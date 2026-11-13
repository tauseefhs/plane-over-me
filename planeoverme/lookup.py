"""Reference data lookups (airlines, airports, aircraft, types, routes).

The raw CSVs in data/standing-data are compiled once into data/lookup.db
(run `python -m planeoverme build-db`). SQLite keeps startup fast and memory
low on the Pi, with no pandas needed.
"""

from __future__ import annotations

import csv
import math
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Optional

from .settings import LOOKUP_DB, STANDING_DATA_DIR

SCHEMA = """
CREATE TABLE airlines (
    icao TEXT, iata TEXT, code TEXT, name TEXT
);
CREATE INDEX airlines_icao ON airlines (icao);

CREATE TABLE airports (
    icao TEXT PRIMARY KEY, iata TEXT, name TEXT, city TEXT,
    country TEXT, lat REAL, lon REAL
);
CREATE INDEX airports_iata ON airports (iata);

CREATE TABLE model_types (
    icao TEXT PRIMARY KEY, manufacturer TEXT, model TEXT,
    engines TEXT, species TEXT, wake TEXT
);

CREATE TABLE aircraft (
    hex TEXT PRIMARY KEY, registration TEXT, type TEXT,
    manufacturer TEXT, model TEXT, operator TEXT, airline_code TEXT
);

CREATE TABLE routes (
    callsign TEXT PRIMARY KEY, airport_codes TEXT
);
"""


def _rows(folder: Path) -> Iterator[dict[str, str]]:
    for path in sorted(folder.rglob("*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as f:
            yield from csv.DictReader(f)


def _clean(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    return value or None


# Business-jet and VIP variants share type codes with airliners (a B789 row
# can say "787-9 BBJ"). These words make a row less likely to be chosen.
_VIP_WORDS = ("BBJ", "ACJ", "PRESTIGE", "LINEAGE", "CHALLENGER", "VIP", "(")


def _model_score(r: dict[str, str]) -> tuple[int, int]:
    """Lower is better: fewest VIP words, then the shortest plain name."""
    model = (r.get("Model") or "").upper()
    return (sum(w in model for w in _VIP_WORDS), len(model))


def build_db(db_path: Path = LOOKUP_DB) -> dict[str, int]:
    """Compile the standing-data CSVs into one SQLite file. Returns row counts."""
    tmp = db_path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    root = STANDING_DATA_DIR

    con.executemany(
        "INSERT INTO airlines VALUES (?, ?, ?, ?)",
        (
            (_clean(r["ICAO"]), _clean(r["IATA"]), _clean(r["Code"]), _clean(r["Name"]))
            for r in _rows(root / "airlines" / "schema-01")
        ),
    )
    con.executemany(
        "INSERT OR IGNORE INTO airports VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            (
                _clean(r["ICAO"]) or _clean(r["Code"]), _clean(r["IATA"]),
                _clean(r["Name"]), _clean(r["Location"]), _clean(r["CountryISO2"]),
                float(r["Latitude"] or 0), float(r["Longitude"] or 0),
            )
            for r in _rows(root / "airports" / "schema-01")
        ),
    )
    best: dict[str, dict[str, str]] = {}
    for r in _rows(root / "model-type" / "schema-01"):
        icao = _clean(r["ICAO"])
        if icao and (icao not in best or _model_score(r) < _model_score(best[icao])):
            best[icao] = r
    con.executemany(
        "INSERT INTO model_types VALUES (?, ?, ?, ?, ?, ?)",
        (
            (
                icao, _clean(r["Manufacturer"]), _clean(r["Model"]),
                _clean(r["Engines"]), _clean(r["SpeciesCode"]),
                _clean(r["WakeTurbulenceCode"]),
            )
            for icao, r in best.items()
        ),
    )
    con.executemany(
        "INSERT OR IGNORE INTO aircraft VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            (
                (_clean(r["ICAO"]) or "").lower(), _clean(r["Registration"]),
                _clean(r["ModelICAO"]), _clean(r["Manufacturer"]), _clean(r["Model"]),
                _clean(r["Operator"]), _clean(r["AirlineCode"]),
            )
            for r in _rows(root / "aircraft" / "schema-01")
        ),
    )
    con.executemany(
        "INSERT OR IGNORE INTO routes VALUES (?, ?)",
        (
            (_clean(r["Callsign"]), _clean(r["AirportCodes"]))
            for r in _rows(root / "routes" / "schema-01")
        ),
    )
    con.commit()
    counts = {
        t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("airlines", "airports", "model_types", "aircraft", "routes")
    }
    con.close()
    tmp.replace(db_path)
    return counts


_connection: Optional[sqlite3.Connection] = None


def _con() -> Optional[sqlite3.Connection]:
    """The lookup database, or None if it hasn't been downloaded yet. Without
    it the display still works, just without airline names, types and routes."""
    global _connection
    if _connection is None:
        if not LOOKUP_DB.exists():
            return None
        _connection = sqlite3.connect(f"file:{LOOKUP_DB}?mode=ro", uri=True,
                                      check_same_thread=False)
        _connection.row_factory = sqlite3.Row
    return _connection


def _one(sql: str, *args: object) -> Optional[dict]:
    con = _con()
    if con is None:
        return None
    row = con.execute(sql, args).fetchone()
    return dict(row) if row else None


@lru_cache(maxsize=4096)
def airline_by_icao(icao: str) -> Optional[dict]:
    """Prefer a row with an IATA code (a real airline, not just a callsign owner)."""
    return _one(
        "SELECT * FROM airlines WHERE icao = ? ORDER BY iata IS NULL LIMIT 1", icao
    )


@lru_cache(maxsize=4096)
def airport(code: str) -> Optional[dict]:
    """Look up by ICAO (LFPG) or IATA (CDG)."""
    if len(code) == 4:
        return _one("SELECT * FROM airports WHERE icao = ?", code)
    return _one("SELECT * FROM airports WHERE iata = ? LIMIT 1", code)


@lru_cache(maxsize=4096)
def model_type(icao_type: str) -> Optional[dict]:
    return _one("SELECT * FROM model_types WHERE icao = ?", icao_type)


@lru_cache(maxsize=4096)
def aircraft(hex_code: str) -> Optional[dict]:
    return _one("SELECT * FROM aircraft WHERE hex = ?", hex_code.lower())


@lru_cache(maxsize=4096)
def route_codes(callsign: str) -> Optional[list[str]]:
    """Offline route: list of ICAO airport codes, e.g. ['LFPG', 'KJFK']."""
    row = _one("SELECT airport_codes FROM routes WHERE callsign = ?", callsign)
    if not row or not row["airport_codes"]:
        return None
    return row["airport_codes"].split("-")


def airports_near(lat: float, lon: float, km: float) -> list[dict]:
    """Airports with an IATA code inside a rough box around a point
    (callers filter by exact distance)."""
    dlat = km / 111.0
    dlon = km / (111.0 * max(0.1, math.cos(math.radians(lat))))
    con = _con()
    if con is None:
        return []
    rows = con.execute(
        "SELECT * FROM airports WHERE iata IS NOT NULL "
        "AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
        (lat - dlat, lat + dlat, lon - dlon, lon + dlon),
    ).fetchall()
    return [dict(r) for r in rows if "heli" not in (r["name"] or "").lower()]
