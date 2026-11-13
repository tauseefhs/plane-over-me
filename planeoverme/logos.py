"""Convert source airline logos into LED-ready images.

    python -m planeoverme logos

Reads assets/logos-source/<ICAO>.png (any size, transparent background) and
writes, on black:
    assets/logos/<size>/<ICAO>.png for size 24, 20 and 16 (logo top-left)
    and 7 (badge in front of the airline name), matching logo_size
Hand-drawn badges in data/badges.toml replace the 7x7 conversion. A
hand-edited PNG (marked by <ICAO>.keep next to it) is never overwritten
unless --force is given.

Logos are the airlines' trademarks. They are downloaded on first run
(see firstrun.py) and never shipped with the project.
"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageEnhance

from .compat import tomllib
from .settings import PROJECT_ROOT

SOURCE_DIR = PROJECT_ROOT / "assets" / "logos-source"
LOGO_DIR = PROJECT_ROOT / "assets" / "logos"
BADGES_FILE = PROJECT_ROOT / "data" / "badges.toml"
SIZE = 7          # size of hand-drawn badges
SIZES = (24, 20, 16, 7)   # sizes written by convert_all

# On an LED panel, very dark pixels just look muddy; anything darker than
# this (0-255 brightness) becomes black.
DARK_CUTOFF = 40


def _palette(img: Image.Image, colors: int = 4) -> list[tuple[int, int, int]]:
    """The logo's main colors, from its visible (non-transparent) pixels."""
    px = [p[:3] for p in img.getdata() if p[3] > 128 and max(p[:3]) >= DARK_CUTOFF]
    if not px:
        return [(255, 255, 255)]
    strip = Image.new("RGB", (len(px), 1))
    strip.putdata(px)
    pal = strip.quantize(colors=colors, method=Image.Quantize.MEDIANCUT).getpalette() or []
    found = {tuple(pal[i:i + 3]) for i in range(0, min(len(pal), colors * 3), 3)}
    return [c for c in found if len(c) == 3] or [(255, 255, 255)]


# Dark brand colors (navy blue) barely light up on LEDs; lift them so their
# brightest channel reaches at least this level, keeping the hue.
MIN_BRIGHTNESS = 170


def _lift(c: tuple[int, int, int]) -> tuple[int, int, int]:
    m = max(c)
    if m == 0 or m >= MIN_BRIGHTNESS:
        return c
    k = MIN_BRIGHTNESS / m
    return tuple(min(255, round(v * k)) for v in c)


def convert(src: Path, size: int = SIZE) -> Image.Image:
    img = Image.open(src).convert("RGBA")

    # Trim transparent margins so the emblem uses the whole tile.
    bbox = img.getchannel("A").point(lambda a: 255 if a > 24 else 0).getbbox()
    if bbox:
        img = img.crop(bbox)
    palette = _palette(img)

    # Fit inside size x size, keep the aspect ratio, center on black.
    scale = size / max(img.size)
    w, h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    small = img.resize((w, h), Image.LANCZOS)

    # Snap every pixel to one of the logo's own colors. Shrinking blends
    # neighbouring colors (red + blue -> purple), which looks muddy on LEDs.
    out = Image.new("RGB", (size, size))
    ox, oy = (size - w) // 2, (size - h) // 2
    for y in range(h):
        for x in range(w):
            r, g, b, a = small.getpixel((x, y))
            if a < 70:
                continue
            nearest = min(palette, key=lambda c: (c[0] - r) ** 2 + (c[1] - g) ** 2 + (c[2] - b) ** 2)
            if max(nearest) >= DARK_CUTOFF:
                out.putpixel((ox + x, oy + y), _lift(nearest))

    # LEDs wash out soft colors: push saturation a little.
    return ImageEnhance.Color(out).enhance(1.3)


def load_badges(path: Path = BADGES_FILE) -> dict[str, Image.Image]:
    """Hand-drawn badges from badges.toml, as images keyed by ICAO code."""
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        table = tomllib.load(fh)
    badges: dict[str, Image.Image] = {}

    def draw(code: str, spec: dict) -> Image.Image:
        rows = spec["pixels"]
        if len(rows) != SIZE or any(len(r) != SIZE for r in rows):
            raise ValueError(f"badge {code}: needs {SIZE} rows of {SIZE} characters")
        colors = {k: tuple(int(v.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
                  for k, v in spec["colors"].items()}
        img = Image.new("RGB", (SIZE, SIZE))
        for y, row in enumerate(rows):
            for x, ch in enumerate(row):
                if ch != ".":
                    if ch not in colors:
                        raise ValueError(f"badge {code}: no color for {ch!r}")
                    img.putpixel((x, y), colors[ch])
        return img

    for code, spec in table.items():
        if "pixels" in spec:
            badges[code.upper()] = draw(code, spec)
    for code, spec in table.items():
        if "same_as" in spec:
            badges[code.upper()] = badges[spec["same_as"].upper()]
    return badges


def convert_all(force: bool = False) -> tuple[int, int, int]:
    """Returns (converted automatically, hand-drawn from badges.toml,
    skipped because a hand-edited PNG is marked with .keep), over all sizes."""
    badges = load_badges()
    codes = {p.stem.upper(): p for p in SOURCE_DIR.glob("*.png")}
    auto = drawn = skipped = 0
    for size in SIZES:
        out_dir = LOGO_DIR / str(size)
        out_dir.mkdir(parents=True, exist_ok=True)
        hand = badges if size == SIZE else {}
        for code in sorted(set(codes) | set(hand)):
            dst = out_dir / f"{code}.png"
            if dst.exists() and not force and _is_hand_tuned(dst):
                skipped += 1
                continue
            try:
                img = hand[code] if code in hand else convert(codes[code], size)
                _save(img, dst)
            except OSError as exc:
                print(f"  could not write {dst}: {exc}")
                continue
            if code in hand:
                drawn += 1
            else:
                auto += 1
    return auto, drawn, skipped


def _save(img: Image.Image, dst: Path) -> None:
    """Write to a temp file, then swap it in. Replacing (instead of
    overwriting) avoids hanging on OneDrive's online-only placeholder files."""
    tmp = dst.with_name(f".{dst.stem}.tmp.png")
    img.save(tmp)
    os.replace(tmp, dst)


def _is_hand_tuned(path: Path) -> bool:
    """Hand-tuned logos are marked by a sibling file <ICAO>.keep."""
    return path.with_suffix(".keep").exists()
