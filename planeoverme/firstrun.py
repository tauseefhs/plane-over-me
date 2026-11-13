"""First-run setup: everything a new user needs, done automatically.

    python -m planeoverme setup         ask for your location, download data
    python -m planeoverme update-data   download fresh aircraft data and logos

The app also calls `ensure_ready()` before it starts, so a fresh download of
the project just works: missing data is fetched, missing settings are asked.
"""

from __future__ import annotations

import io
import re
import shutil
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from .compat import tomllib
from .settings import LOOKUP_DB, PROJECT_ROOT, STANDING_DATA_DIR

USER_AGENT = "plane-over-me/1.0 (+https://github.com/tauseefhs/plane-over-me)"
TIMEOUT = 30

# Aircraft, airline, airport and route reference data (CC0 licence).
STANDING_DATA_ZIP = "https://codeload.github.com/vradarserver/standing-data/zip/refs/heads/main"
STANDING_DATA_FOLDERS = ("aircraft", "airlines", "airports", "model-type", "routes")

# Airline logos, collected by the Jxck-S/airline-logos project. The logos are
# trademarks of their airlines; they are downloaded to your computer, not
# shipped with this project.
LOGO_REPO = "Jxck-S/airline-logos"
LOGO_FOLDER = "radarbox_logos"
LOGO_TREE = f"https://api.github.com/repos/{LOGO_REPO}/git/trees/main?recursive=1"
LOGO_RAW = f"https://raw.githubusercontent.com/{LOGO_REPO}/main/{{path}}"
LOGO_SOURCE_DIR = PROJECT_ROOT / "assets" / "logos-source"
LOGO_DIR = PROJECT_ROOT / "assets" / "logos"

LOCAL_SETTINGS = PROJECT_ROOT / "settings.local.toml"
GEOCODER = "https://nominatim.openstreetmap.org/search"


def _get(url: str, **kwargs) -> requests.Response:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, **kwargs)
    r.raise_for_status()
    return r


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

def download_reference_data() -> None:
    """Download the standing-data CSVs and build data/lookup.db."""
    from .lookup import build_db

    print("Downloading aircraft and airline data (about 6 MB) ...")
    data = _get(STANDING_DATA_ZIP).content
    tmp = STANDING_DATA_DIR.with_name("standing-data.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            parts = name.split("/", 1)          # "standing-data-main/<path>"
            if len(parts) < 2 or not parts[1] or name.endswith("/"):
                continue
            rel = parts[1]
            if rel.split("/")[0] in STANDING_DATA_FOLDERS or rel in ("LICENSE", "README.md"):
                dst = tmp / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(zf.read(name))
    shutil.rmtree(STANDING_DATA_DIR, ignore_errors=True)
    tmp.replace(STANDING_DATA_DIR)
    print("Building the lookup database ...")
    counts = build_db()
    print("  " + ", ".join(f"{n:,} {t}" for t, n in counts.items()))


# ---------------------------------------------------------------------------
# Logos
# ---------------------------------------------------------------------------

def download_logos() -> None:
    """Download airline logos and convert them for the LED panel."""
    from .logos import convert_all

    print(f"Downloading airline logos from github.com/{LOGO_REPO} ...")
    tree = _get(LOGO_TREE).json()["tree"]
    paths = [e["path"] for e in tree
             if e["type"] == "blob" and e["path"].startswith(LOGO_FOLDER + "/")
             and re.fullmatch(r"[A-Z0-9]{3}\.png", e["path"].rsplit("/", 1)[1])]
    LOGO_SOURCE_DIR.mkdir(parents=True, exist_ok=True)

    def fetch(path: str) -> bool:
        dst = LOGO_SOURCE_DIR / path.rsplit("/", 1)[1]
        try:
            dst.write_bytes(_get(LOGO_RAW.format(path=path)).content)
            return True
        except requests.RequestException:
            return False

    with ThreadPoolExecutor(max_workers=16) as pool:
        ok = sum(pool.map(fetch, paths))
    print(f"  {ok} of {len(paths)} logos downloaded")
    print("Converting logos for the LED panel ...")
    auto, drawn, _ = convert_all()
    print(f"  {auto + drawn} logo images ready")


def logos_ready() -> bool:
    return any((LOGO_DIR / "16").glob("*.png"))


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

def _parse_coordinates(text: str) -> tuple[float, float] | None:
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*[,; ]\s*(-?\d+(?:\.\d+)?)\s*", text)
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if -90 <= lat <= 90 and -180 <= lon <= 180:
        return lat, lon
    return None


def geocode(query: str) -> list[dict]:
    """Up to 5 matches for a town, postcode or address (OpenStreetMap)."""
    r = _get(GEOCODER, params={"q": query, "format": "json", "limit": 5})
    return [{"name": p["display_name"], "lat": float(p["lat"]), "lon": float(p["lon"])}
            for p in r.json()]


def location_configured() -> bool:
    if not LOCAL_SETTINGS.exists():
        return False
    with LOCAL_SETTINGS.open("rb") as f:
        home = tomllib.load(f).get("home", {})
    return "lat" in home and "lon" in home


def save_location(lat: float, lon: float, label: str) -> None:
    label = label.replace("\n", " ")[:120]
    LOCAL_SETTINGS.write_text(
        "# Your personal settings. This file stays on your computer.\n"
        "# Anything here overrides settings.toml.\n\n"
        "[home]\n"
        f"# {label}\n"
        f"lat = {lat:.5f}\n"
        f"lon = {lon:.5f}\n",
        encoding="utf-8",
    )


def ask_location() -> None:
    """Ask where the display is, in plain words, and save it."""
    print()
    print("Where will the display be? Planes are shown around this spot.")
    print("Type a town, postcode or address, e.g.  Lyon  or  10115 Berlin")
    print("(or exact coordinates, e.g.  48.8530, 2.3499)")
    while True:
        try:
            text = input("> ").strip()
        except EOFError:
            return
        if not text:
            continue
        coords = _parse_coordinates(text)
        if coords:
            save_location(*coords, "entered as coordinates")
            print(f"Saved: {coords[0]:.5f}, {coords[1]:.5f}")
            return
        try:
            matches = geocode(text)
        except requests.RequestException:
            print("Could not look that up (no internet?). Try exact coordinates instead.")
            continue
        if not matches:
            print("Nothing found. Try adding the country, e.g.  Rennes, France")
            continue
        for i, m in enumerate(matches, 1):
            print(f"  {i}. {m['name']}")
        pick = input("Which one? Type its number, or press Enter to search again: ").strip()
        if pick.isdigit() and 1 <= int(pick) <= len(matches):
            m = matches[int(pick) - 1]
            save_location(m["lat"], m["lon"], m["name"])
            print(f"Saved: {m['name']}")
            return


# ---------------------------------------------------------------------------
# Everything at once
# ---------------------------------------------------------------------------

def ensure_ready(interactive: bool | None = None) -> None:
    """Make sure the app has what it needs. Never fails hard: without data
    or logos the display still runs, with less detail."""
    if interactive is None:
        interactive = sys.stdin.isatty()
    if not location_configured():
        if interactive:
            ask_location()
        else:
            print("No location set yet: showing planes over central Paris.")
            print("Run  python -m planeoverme setup  to choose your own.")
    if not LOOKUP_DB.exists():
        try:
            download_reference_data()
        except (requests.RequestException, OSError, zipfile.BadZipFile) as exc:
            print(f"Could not download aircraft data ({exc}). Continuing without it.")
    if not logos_ready():
        try:
            download_logos()
        except (requests.RequestException, OSError, KeyError, ValueError) as exc:
            print(f"Could not download logos ({exc}). Continuing without them.")
