import pytest
from PIL import Image, ImageDraw

from planeoverme.flight import Airport, Flight
from planeoverme.render.screens import SCREENS, Scene, render, short_type
from planeoverme.rules import EMERGENCY, WATCHLIST

CFG = {
    "altitude_unit": "ft", "speed_unit": "kmh", "clock_24h": True, "logos": False,
    "screens": ["full", "who", "altitude", "distance", "speed"],
    "screen_seconds": {"full": 10, "who": 7, "altitude": 4, "distance": 4, "speed": 4},
    "airline_names": {"TVF": "TRANSAVIA"},
}


def flight(**kw) -> Flight:
    f = Flight(
        hex="a75da8", callsign="DAL290", lat=0, lon=0, altitude_ft=2775, on_ground=False,
        ground_speed_kt=160, vertical_rate_fpm=-800, track_deg=0, squawk="1000",
        emergency=None, distance_nm=15, bearing_deg=20, type_code="A359",
        type_manufacturer="Airbus", type_model="A-350-900 XWB",
        airline_icao="DAL", airline_iata="DL",
        origin=Airport("KLAX", "LAX", "Los Angeles"), destination=Airport("LFPG", "CDG", "Paris"),
    )
    for k, v in kw.items():
        setattr(f, k, v)
    return f


@pytest.mark.parametrize("model,manufacturer,expected", [
    ("A-350-900 XWB", "Airbus", "A350"),
    ("A-320neo", "Airbus", "A320neo"),
    ("787-9 Dreamliner", "Boeing", "B787"),
    ("190", "Embraer", "E190"),
    ("787-9 BBJ", "Boeing", "B787"),
    ("737 MAX 8", "Boeing", "B737 MAX"),
    ("A-321neo LR", "Airbus", "A321neo"),
])
def test_short_type(model, manufacturer, expected):
    assert short_type(flight(type_model=model, type_manufacturer=manufacturer)) == expected


@pytest.mark.parametrize("variant", [
    {},
    {"origin": None, "destination": None},
    {"altitude_ft": None, "ground_speed_kt": None, "type_model": None, "type_code": None},
    {"altitude_ft": 45000, "callsign": "SAMUIDF", "airline_iata": None},
])
def test_every_screen_draws(variant):
    f = flight(**variant)
    for t in range(0, 20):  # walk through the whole rotation
        img = render(Scene(f, shown_since=0), CFG, now=float(t))
        assert img.size == (64, 32)
        assert img.getbbox() is not None, f"blank frame at t={t}"


def test_emergency_and_watchlist_and_idle():
    render(Scene(flight(squawk="7700", emergency="emergency"), tier=EMERGENCY), CFG, now=8.0)
    render(Scene(flight(), tier=WATCHLIST), CFG, now=1.0)
    render(Scene(None, planes_nearby=0, source_ok=False), CFG, now=1.0)


def test_screen_names_match_settings():
    assert set(CFG["screens"]) <= set(SCREENS)


def test_full_screen_is_still_with_short_name():
    cfg = {**CFG, "screens": ["full"]}
    f = flight(type_name="Airbus A350-900", airline_name="Air France")
    # Everything except the distance (which rolls when long) stays still.
    frames = {render(Scene(f, shown_since=0), cfg, now=t / 2).crop((0, 0, 64, 24)).tobytes()
              for t in range(60)}
    assert len(frames) == 1


def test_long_airline_name_rolls_and_nothing_else_moves():
    cfg = {**CFG, "screens": ["full"], "scroll_speed": 10, "logo_size": 7}
    f = flight(type_name="Airbus A350-900", airline_name="El Al Israel Airlines")
    frames = [render(Scene(f, shown_since=0), cfg, now=t / 2) for t in range(40)]
    assert len({i.crop((0, 0, 64, 17)).tobytes() for i in frames}) == 1    # top still
    assert len({i.crop((0, 17, 64, 24)).tobytes() for i in frames}) > 1     # name rolls
    # Continuous: it is already moving in the first second.
    assert len({i.crop((0, 17, 64, 24)).tobytes() for i in frames[:3]}) > 1


@pytest.mark.parametrize("type_name,expected", [
    ("Airbus A350-900", "A350-900"),
    ("Boeing 787-9", "B787-9"),
    ("Boeing 737 MAX 8", "B737 MAX 8"),
    ("Embraer E190", "E190"),
    ("ATR 72-600", "ATR 72-600"),
    ("Bombardier CRJ900", "CRJ900"),
])
def test_model_number(type_name, expected):
    from planeoverme.render.screens import model_number
    assert model_number(flight(type_name=type_name)) == expected


def test_tiles_can_be_turned_off():
    from planeoverme.render.screens import _tile
    on = render(Scene(flight(), shown_since=0), {**CFG, "screens": ["full"]}, now=0.0)
    off = render(Scene(flight(), shown_since=0), {**CFG, "screens": ["full"], "tiles": False}, now=0.0)
    tile = _tile(CFG)[0]
    assert tile in {c for _, c in on.getcolors(4096)}
    assert tile not in {c for _, c in off.getcolors(4096)}



def test_wide_letters_get_wide_cells():
    from planeoverme.render.font import SMALL
    # M and W keep their 5-pixel shapes, so they never look like H or N.
    assert SMALL.cells_text_width("KMH") == 3 + 1 + 5 + 1 + 3
    assert SMALL.cells_text_width("HHH") == 11


@pytest.mark.parametrize("variant", [
    {"origin": Airport("LEMD", "MAD", "Madrid"), "destination": Airport("EPWA", "WAW", "Warsaw")},
    {"callsign": "WMT1234", "airline_icao": None},
    {"airline_name": "Qatar Airways", "airline_icao": "QTR"},
])
def test_full_screen_fits_wide_text(variant):
    img = render(Scene(flight(**variant), shown_since=0), {**CFG, "screens": ["full"]}, now=0.0)
    assert img.size == (64, 32)


@pytest.mark.parametrize("km,bearing,expected", [
    (6.6, 315, "7KM AWAY"), (14.7, 143, "15KM AWAY"), (0.4, 10, "<1KM AWAY"),
    (40, 270, "40KM AWAY"),
])
def test_distance_label(km, bearing, expected):
    from planeoverme.render.screens import distance_label
    assert distance_label(flight(distance_nm=km / 1.852, bearing_deg=bearing)) == expected


@pytest.mark.parametrize("alt,km", [(24975, 40), (45000, 44.6), (950, 0.3), (12000, 7)])
def test_bottom_line_is_still_and_fits(alt, km):
    """Longest altitude + distance fit side by side, so nothing rolls."""
    from planeoverme.render.font import SMALL
    from planeoverme.render.screens import distance_label
    f = flight(altitude_ft=alt, distance_nm=km / 1.852)
    assert SMALL.cells_text_width("↑" + str(alt)) + 1 + SMALL.cells_text_width(distance_label(f)) <= 64
    cfg = {**CFG, "screens": ["full"], "logo_size": 24, "scroll_speed": 10}
    frames = {render(Scene(f, shown_since=0), cfg, now=t / 4).crop((0, 26, 64, 31)).tobytes()
              for t in range(40)}
    assert len(frames) == 1


def test_logo16_layout(monkeypatch):
    from planeoverme.render import screens
    blue = Image.new("RGB", (16, 16), (0, 0, 255))
    monkeypatch.setattr(screens, "load_logo",
                        lambda icao, size=7: blue if icao == "DAL" and size == 16 else None)
    cfg = {**CFG, "screens": ["full"], "logos": True, "tiles": False, "logo_size": 16}
    img = render(Scene(flight(airline_name="Delta Air Lines"), shown_since=0), cfg, now=0.0)
    assert img.crop((0, 0, 16, 16)).getcolors() == [(256, (0, 0, 255))]
    # Text sits to the right of the logo on the top three lines...
    assert img.crop((18, 0, 64, 17)).getbbox() is not None
    # ...and the name and altitude/distance lines use the full width.
    assert img.crop((0, 19, 8, 24)).getbbox() is not None
    assert img.crop((0, 26, 8, 31)).getbbox() is not None
    # Without a logo, the top lines start at the left edge.
    no_logo = render(Scene(flight(airline_icao="XYZ"), shown_since=0), cfg, now=0.0)
    assert no_logo.crop((0, 0, 8, 5)).getbbox() is not None


@pytest.mark.parametrize("size", [16, 20, 24])
def test_big_logo_lines_never_overlap(size, monkeypatch):
    """Every text line must stay in its own rows (no line drawn over another)."""
    from planeoverme.render import screens
    from planeoverme.render.font import SMALL
    calls = []
    real = SMALL.draw_cells
    monkeypatch.setattr(SMALL, "draw_cells", lambda d, xy, *a, **k: (calls.append(xy), real(d, xy, *a, **k)))
    cfg = {**CFG, "screens": ["full"], "logos": False, "tiles": False, "logo_size": size}
    render(Scene(flight(airline_name="Air France"), shown_since=0), cfg, now=0.0)
    rows = sorted({y for _, y in calls})
    assert all(b - a >= SMALL.height + 1 for a, b in zip(rows, rows[1:])), rows
    assert rows[-1] + SMALL.height <= 32


@pytest.mark.parametrize("size", [24, 7])
def test_text_that_does_not_fit_rolls(size):
    """Long text on any line rolls instead of being cut."""
    cfg = {**CFG, "screens": ["full"], "logos": False, "logo_size": size, "scroll_speed": 10}
    f = flight(type_name="Airbus A330-900NEO", airline_name="Commandement Du Transport Aerien",
               callsign="SAMUIDF7", airline_icao=None,
               origin=Airport("LEMD", "MADRID", "Madrid"), destination=Airport("EPWA", "WARSAW", "Warsaw"))
    frames = [render(Scene(f, shown_since=0), cfg, now=t / 2) for t in range(0, 80)]
    assert len({fr.tobytes() for fr in frames}) > 10          # rolls continuously
