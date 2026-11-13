"""Draw 64x32 frames. Everything here is plain Pillow, so the same frames go
to the browser simulator on the Mac and to the real LED panel on the Pi.

Design rule: one idea per screen, big text, few colors.
    white  the main thing on the screen
    amber  the second thing (route, or what the big number means)
    grey   supporting detail
    red    emergencies only
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional

from PIL import Image, ImageDraw

from ..flight import Flight
from ..rules import EMERGENCY, NORMAL, WATCHLIST
from .font import BIG, SMALL

WIDTH, HEIGHT = 64, 32

WHITE = (255, 255, 255)
AMBER = (255, 160, 0)
GREY = (175, 175, 175)
CYAN = (0, 190, 255)
DIM = (95, 95, 95)
RED = (255, 30, 30)
GOLD = (255, 200, 0)

# The single-screen layout is styled like an airport departures board:
# yellow for what matters most, warm white for the rest, and every character
# on its own dim tile, like the flaps of a split-flap board.
KEY = (255, 196, 0)
DETAIL = (255, 244, 220)
TILE = ((70, 62, 45), (48, 42, 30))   # top half, bottom half, at 100%


def _tile(cfg: dict[str, Any]):
    """Tile colors scaled by display.tile_brightness (percent, 0 = off)."""
    if not cfg.get("tiles", True):
        return None
    pct = cfg.get("tile_brightness", 100) / 100
    if pct <= 0:
        return None
    return tuple(tuple(round(c * pct) for c in half) for half in TILE)


COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
TREND_WORDS = {"climbing": "UP", "descending": "DOWN", "level": "LEVEL"}


@dataclass
class Scene:
    """Everything a frame needs. Built by the app from the tracker state."""
    flight: Optional[Flight] = None
    tier: int = NORMAL
    shown_since: float = 0.0
    planes_nearby: int = 0
    source_ok: bool = True
    status: Optional[str] = None      # message for the clock screen
    brightness: float = 1.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _center_x(font, text: str, scale: int = 1) -> int:
    return max(0, (WIDTH - font.width(text, scale)) // 2)


def _centered(d: ImageDraw.ImageDraw, font, y: int, text: str, color, scale: int = 1) -> None:
    font.draw(d, (_center_x(font, text, scale), y), text, color, scale)


def _big_number(d: ImageDraw.ImageDraw, value: str, caption: str) -> None:
    """A large number with a one-line caption under it."""
    scale = 2 if BIG.width(value, 2) <= WIDTH else 1
    y = 2 if scale == 2 else 7
    _centered(d, BIG, y, value, WHITE, scale)
    _centered(d, SMALL, 22, SMALL.fit(caption, WIDTH), AMBER)


def short_type(f: Flight) -> str:
    """Aircraft family in a few characters: 'A-350-900 XWB' -> 'A350'."""
    model = (f.type_model or "").replace("XWB", "").strip()
    if model.startswith("A-"):
        model = "A" + model[2:]
    prefix = {"Boeing": "B", "Embraer": "E", "Airbus": "A"}.get(f.type_manufacturer or "")
    if model[:1].isdigit() and prefix:
        model = prefix + model
    # Keep the family, drop the variant: A350-900 -> A350, B787-9 BBJ -> B787.
    # Keep suffixes people know: A320neo, B737 MAX.
    m = re.match(r"^([A-Z]\d{3})(\S*)(.*)$", model)
    if m:
        family, attached, tail = m.groups()
        suffix = attached if attached.isalpha() else ""
        if "MAX" in tail.upper():
            suffix = " MAX"
        model = family + suffix
    if model and BIG.width(model) > WIDTH - 2:
        model = model.split(" ")[0]
    if not model or BIG.width(model) > WIDTH - 2:
        model = f.type_code or ""
    return model


# ---------------------------------------------------------------------------
# Screens (each draws one idea)
# ---------------------------------------------------------------------------

MANUFACTURERS = ("DE HAVILLAND CANADA", "BOMBARDIER", "CANADAIR", "AIRBUS", "BOEING", "EMBRAER")
MODEL_PREFIX = {"BOEING": "B", "EMBRAER": "E", "AIRBUS": "A"}


def model_number(f: Flight) -> str:
    """Exact model without the maker's name: 'Airbus A350-900' -> 'A350-900',
    'Boeing 787-9' -> 'B787-9'. Airbus, Boeing and Embraer are clear from the
    first letter, which leaves room on the line."""
    name = (f.type_name or "").upper().replace("XWB", "").strip()
    if not name:
        return (f.type_code or "").upper()
    maker = next((m for m in MANUFACTURERS if name.startswith(m + " ")), None)
    if maker:
        name = name[len(maker) + 1:]
        if name[:1].isdigit() and maker in MODEL_PREFIX:
            name = MODEL_PREFIX[maker] + name
    if name.startswith("A-"):
        name = "A" + name[2:]
    return name


LOGO_DIR = Path(__file__).resolve().parents[2] / "assets" / "logos"


@lru_cache(maxsize=1024)
def load_logo(icao: str, size: int = 7) -> Optional[Image.Image]:
    """assets/logos/<size>/<ICAO>.png, made by `python -m planeoverme logos`."""
    try:
        img = Image.open(LOGO_DIR / str(size) / f"{icao.upper()}.png").convert("RGB")
    except OSError:
        return None
    if img.size != (size, size):
        img = img.resize((size, size), Image.LANCZOS)
    return img


def distance_label(f: Flight) -> str:
    """Distance from home, e.g. '7KM AWAY'. Whole kilometers; under 1 km
    shows '<1KM AWAY'."""
    km = round(f.distance_km)
    return f"{km}KM AWAY" if km >= 1 else "<1KM AWAY"


def _bottom_line(d: ImageDraw.ImageDraw, f: Flight, cfg: dict[str, Any], tile,
                 y: int = 25, t: float = 0) -> None:
    """Altitude with a climb/descend arrow on the left, distance from home on
    the right, e.g. '↑24975   40KM AWAY'. No unit on the altitude so both fit
    (feet, or metres with altitude_unit = "m"). If they ever don't fit, the
    distance rolls in the space left."""
    if f.altitude_ft is None:
        alt = "-"
    elif cfg["altitude_unit"] == "m":
        alt = str(round(f.altitude_ft * 0.3048))
    else:
        alt = str(f.altitude_ft)
    left = {"climbing": "↑", "descending": "↓"}.get(f.vertical_trend, "→") + alt
    dist = distance_label(f)
    left_w = SMALL.cells_text_width(left)
    dist_w = SMALL.cells_text_width(dist)
    SMALL.draw_cells(d, (0, y), left, DETAIL, tile=tile, width=left_w)
    if left_w + 1 + dist_w <= WIDTH:
        SMALL.draw_cells(d, (left_w + 1, y), dist, DETAIL, tile=tile,
                         width=WIDTH - left_w - 1, align="right")
    else:
        box_x = left_w + 2
        _rolling_line(d, box_x, y, WIDTH - box_x, dist, DETAIL, tile, t, cfg, align="right")


def _model_line(d: ImageDraw.ImageDraw, f: Flight, y: int, tile, t: float = 0,
                cfg: dict[str, Any] | None = None) -> None:
    """Aircraft model: big if it fits in 10 cells, else small, rolling if
    even that doesn't fit."""
    model = model_number(f)
    if BIG.cells_text_width(model) <= BIG.cells_width(10):
        BIG.draw_cells(d, ((WIDTH - BIG.cells_width(10)) // 2, y), model, KEY, 10, tile)
    else:
        _rolling_line(d, 0, y + 1, WIDTH, model, KEY, tile, t, cfg or {})


def _rolling_line(d: ImageDraw.ImageDraw, x: int, y: int, width: int, text: str,
                  color, tile, t: float, cfg: dict[str, Any], font=SMALL,
                  align: str = "left") -> None:
    """Board text in a box. If it fits, it stays still. If not, it rolls left
    continuously like a ticker, with a short gap before it repeats. The tiles
    stay fixed; only the letters move. `t` is seconds since the plane
    appeared, so every plane starts from the beginning of each line."""
    text_w = font.cells_text_width(text)
    if text_w <= width:
        font.draw_cells(d, (x, y), text, color, tile=tile, width=width, align=align)
        return
    font.draw_cells(d, (x, y), "", color, tile=tile, width=width)   # empty tiles
    gap = (font.cell_width + 1) * 3
    period = text_w + gap
    strip = Image.new("RGB", (period * 2, font.height))
    sd = ImageDraw.Draw(strip)
    font.draw_cells(sd, (0, 0), text, color, width=text_w)
    font.draw_cells(sd, (period, 0), text, color, width=text_w)
    offset = int(t * cfg.get("scroll_speed", 10)) % period
    window = strip.crop((offset, 0, offset + width, font.height))
    mask = window.convert("L").point(lambda v: 255 if v else 0)
    d._image.paste(window, (x, y), mask)


def _emergency_label(f: Flight) -> str:
    return f"{f.emergency} {f.squawk or ''}".upper().strip()


def _airline_name(f: Flight, cfg: dict[str, Any]) -> str:
    custom = cfg.get("airline_names", {}).get(f.airline_icao or "")
    return (custom or f.airline_name or f.registration or "").upper()


def _screen_full_big_logo(d: ImageDraw.ImageDraw, f: Flight, cfg: dict[str, Any],
                          t: float, tile, size: int) -> None:
    """Small text with a logo of `size` (16, 20 or 24) top-left.

    16 or 20:                          24:
        [LOGO] FLIGHT    yellow            [LOGO] FLIGHT
        [LOGO] ROUTE     white             [LOGO] ROUTE
        [LOGO] MODEL     yellow            [LOGO] MODEL
        AIRLINE NAME     full width        [LOGO] AIRLINE (rolls if long)
        ↑ALT    7KM NW                     ↑ALT    7KM NW
    """
    logo = load_logo(f.airline_icao, size) if cfg.get("logos", True) and f.airline_icao else None
    x = 0
    if logo:
        d._image.paste(logo, (0, 0))
        x = size + 2
    room = WIDTH - x
    number = (f.flight_number or f.registration or f.hex).upper()
    route = f"{f.origin.code}>{f.destination.code}" if f.origin and f.destination else ""
    name_beside = size >= 24
    step = 6 if (name_beside or size <= 16) else 7
    _rolling_line(d, x, 0, room, number, KEY, tile, t, cfg)
    _rolling_line(d, x, step, room, route, DETAIL, tile, t, cfg)
    _rolling_line(d, x, 2 * step, room, model_number(f), KEY, tile, t, cfg)
    if name_beside:
        nx, ny, nw = x, 3 * step, room
    else:
        nx, ny, nw = 0, 19 if size <= 16 else 20, WIDTH
    if f.emergency:
        _rolling_line(d, nx, ny, nw, _emergency_label(f), RED, tile, t, cfg)
    else:
        _rolling_line(d, nx, ny, nw, _airline_name(f, cfg), DETAIL, tile, t, cfg)
    _bottom_line(d, f, cfg, tile, y=26, t=t)


def screen_full(d: ImageDraw.ImageDraw, f: Flight, cfg: dict[str, Any], t: float = 0) -> None:
    """Everything on one screen, airport-board style. With logo_size 16, 20 or
    24 the text is small and the logo sits top-left (see _screen_full_big_logo).
    With logo_size = 7:

        FLIGHT   ROUTE       big yellow + small white
        MODEL                big yellow
        [logo] AIRLINE NAME  7x7 badge + small white, rolls if too long
                             (red emergency here)
        ↑ALT    7KM AWAY     small white: altitude (no unit), distance
    """
    tile = _tile(cfg)
    size = cfg.get("logo_size", 7)
    if size in (16, 20, 24):
        _screen_full_big_logo(d, f, cfg, t, tile, size)
        return

    # Line 1: flight number (big) and route (small, right-aligned).
    number = (f.flight_number or f.registration or f.hex).upper()
    route = f"{f.origin.code}>{f.destination.code}" if f.origin and f.destination else ""
    route_w = min(max(SMALL.cells_width(7), SMALL.cells_text_width(route)), SMALL.cells_width(9))
    _rolling_line(d, WIDTH - route_w, 1, route_w, route, DETAIL, tile, t, cfg, align="right")
    room = WIDTH - route_w - 1
    if BIG.cells_text_width(number) <= room:
        BIG.draw_cells(d, (0, 0), number, KEY, tile=tile, width=room)
    else:
        _rolling_line(d, 0, 1, room, number, KEY, tile, t, cfg)

    # Line 2: aircraft model.
    _model_line(d, f, 9, tile, t, cfg)

    # Line 3: logo badge + airline name, or the emergency in red.
    if f.emergency:
        _rolling_line(d, 0, 18, WIDTH, _emergency_label(f), RED, tile, t, cfg)
    else:
        logo = load_logo(f.airline_icao, 7) if cfg.get("logos", True) and f.airline_icao else None
        x = 0
        if logo:
            d._image.paste(logo, (0, 17))
            x = 9
        _rolling_line(d, x, 18, WIDTH - x, _airline_name(f, cfg), DETAIL, tile, t, cfg)

    # Line 4: altitude, distance and direction.
    _bottom_line(d, f, cfg, tile, t=t)


def screen_who(d: ImageDraw.ImageDraw, f: Flight, cfg: dict[str, Any], t: float = 0) -> None:
    """Flight number, route, aircraft type."""
    number = BIG.fit(f.flight_number or f.registration or f.hex.upper(), WIDTH)
    kind = short_type(f)
    if f.origin and f.destination:
        _centered(d, BIG, 2, number, WHITE)
        _centered(d, BIG, 12, BIG.fit(f"{f.origin.code}>{f.destination.code}", WIDTH), AMBER)
        _centered(d, BIG, 23, kind, GREY)
    else:
        _centered(d, BIG, 6, number, WHITE)
        _centered(d, BIG, 18, kind, GREY)


def screen_altitude(d: ImageDraw.ImageDraw, f: Flight, cfg: dict[str, Any], t: float = 0) -> None:
    if f.altitude_ft is None:
        _big_number(d, "-", "HEIGHT UNKNOWN")
        return
    if cfg["altitude_unit"] == "m":
        value, unit = round(f.altitude_ft * 0.3048), "METRES"
    else:
        value, unit = f.altitude_ft, "FEET"
    _big_number(d, str(value), f"{unit}  {TREND_WORDS[f.vertical_trend]}")


def screen_distance(d: ImageDraw.ImageDraw, f: Flight, cfg: dict[str, Any], t: float = 0) -> None:
    km = f.distance_km
    value = f"{km:.1f}" if km < 10 else str(round(km))
    compass = COMPASS[round(f.bearing_deg / 45) % 8]
    _big_number(d, value, f"KM AWAY  {compass}")


def screen_speed(d: ImageDraw.ImageDraw, f: Flight, cfg: dict[str, Any], t: float = 0) -> None:
    if f.ground_speed_kt is None:
        _big_number(d, "-", "SPEED UNKNOWN")
    elif cfg["speed_unit"] == "kt":
        _big_number(d, str(round(f.ground_speed_kt)), "KNOTS")
    else:
        _big_number(d, str(round(f.ground_speed_kt * 1.852)), "KM/H")


def screen_alert(d: ImageDraw.ImageDraw, f: Flight, cfg: dict[str, Any], now: float) -> None:
    color = RED if int(now * 2) % 2 == 0 else (120, 0, 0)
    _centered(d, BIG, 2, f.squawk or "SOS", color, 2)
    _centered(d, SMALL, 22, SMALL.fit((f.emergency or "emergency").upper(), WIDTH - 4), WHITE)


def screen_idle(d: ImageDraw.ImageDraw, scene: Scene, now: float, cfg: dict[str, Any]) -> None:
    t = datetime.fromtimestamp(now)
    clock = t.strftime("%H:%M") if cfg["clock_24h"] else t.strftime("%I:%M").lstrip("0")
    if int(now) % 2:
        clock = clock.replace(":", " ")
    _centered(d, BIG, 2, clock, KEY, 2)
    _centered(d, SMALL, 19, t.strftime("%a %d %b").upper(), DETAIL)

    if scene.status:
        color = DETAIL if scene.status == "NIGHT MODE" else RED
        _centered(d, SMALL, 26, SMALL.fit(scene.status, WIDTH), color)
    elif not scene.source_ok:
        _centered(d, SMALL, 26, "NO DATA", RED)
    else:
        n = scene.planes_nearby
        _centered(d, SMALL, 26, f"{n} PLANE{'S' if n != 1 else ''} AROUND", DETAIL)


SCREENS: dict[str, Callable[[ImageDraw.ImageDraw, Flight, dict[str, Any], float], None]] = {
    "full": screen_full,
    "who": screen_who,
    "altitude": screen_altitude,
    "distance": screen_distance,
    "speed": screen_speed,
}


# ---------------------------------------------------------------------------
# Frame
# ---------------------------------------------------------------------------

def _current_screen(scene: Scene, cfg: dict[str, Any], now: float) -> str:
    if len(cfg["screens"]) == 1:
        return cfg["screens"][0]   # a single still screen, no rotation
    cycle = [(name, cfg["screen_seconds"][name]) for name in cfg["screens"]]
    if scene.tier == EMERGENCY:
        cycle.insert(1, ("alert", 3))
    t = (now - scene.shown_since) % sum(s for _, s in cycle)
    for name, seconds in cycle:
        if t < seconds:
            return name
        t -= seconds
    return cycle[0][0]


def render(scene: Scene, cfg: dict[str, Any], now: Optional[float] = None) -> Image.Image:
    """Build one 64x32 frame for the current moment."""
    now = time.time() if now is None else now
    img = Image.new("RGB", (WIDTH, HEIGHT))
    d = ImageDraw.Draw(img)
    f = scene.flight

    if f is None:
        screen_idle(d, scene, now, cfg)
    else:
        name = _current_screen(scene, cfg, now)
        if name == "alert":
            screen_alert(d, f, cfg, now)
        else:
            SCREENS[name](d, f, cfg, now - scene.shown_since)

        # The full screen uses every pixel, so it shows emergencies in its own
        # text line instead of a border, and skips the corner markers.
        if name != "full":
            if scene.tier == EMERGENCY:
                d.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), outline=RED)
            elif scene.tier == WATCHLIST:
                d.point([(63, 0), (62, 0), (63, 1)], fill=GOLD)
            if not scene.source_ok:
                d.point((63, 31), fill=RED)

    if scene.brightness < 1.0:
        img = Image.eval(img, lambda v: int(v * scene.brightness))
    return img
