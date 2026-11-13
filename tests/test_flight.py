import pytest

from planeoverme.flight import Flight


def flight(callsign, icao="AFR", iata="AF") -> Flight:
    return Flight(
        hex="abc", callsign=callsign, lat=0, lon=0, altitude_ft=1000, on_ground=False,
        ground_speed_kt=200, vertical_rate_fpm=0, track_deg=0, squawk=None, emergency=None,
        distance_nm=1, bearing_deg=0, airline_icao=icao, airline_iata=iata,
    )


@pytest.mark.parametrize("callsign,icao,iata,expected", [
    ("AFR740", "AFR", "AF", "AF740"),       # digits only: real flight number
    ("AFR006", "AFR", "AF", "AF006"),       # zeros kept as broadcast
    ("SAS34L", "SAS", "SK", "SAS34L"),      # letters: callsign as broadcast
    ("AFR46TD", "AFR", "AF", "AFR46TD"),
    ("EZY12AB", "EZY", "U2", "EZY12AB"),
    ("FHTRI", None, None, "FHTRI"),         # private plane, no airline
])
def test_flight_number(callsign, icao, iata, expected):
    assert flight(callsign, icao, iata).flight_number == expected


def test_speed_falls_back_to_true_airspeed():
    from planeoverme.flight import parse_aircraft
    base = {"hex": "abc", "lat": 48.9, "lon": 2.5, "alt_baro": 10000}
    assert parse_aircraft({**base, "gs": 300, "tas": 280}, 48.9, 2.5).ground_speed_kt == 300
    assert parse_aircraft({**base, "tas": 280}, 48.9, 2.5).ground_speed_kt == 280
    assert parse_aircraft(base, 48.9, 2.5).ground_speed_kt is None


def test_aircraft_names_file_is_valid():
    from planeoverme.compat import tomllib
    from planeoverme.settings import DATA_DIR
    names = tomllib.load((DATA_DIR / "aircraft_names.toml").open("rb"))
    assert {"A320", "B738", "E190", "DH8D", "E145"} <= set(names)
    assert not {"C56X", "E35L", "GLF6", "FA7X", "CL60"} & set(names)   # business jets
