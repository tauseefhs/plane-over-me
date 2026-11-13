"""Hand-drawn bitmap fonts for the 64x32 panel.

Each glyph is rows of '#' (lit) and '.' (dark) separated by '/'. Glyphs can
have different widths. Text is upper-cased; unknown characters draw as '?'.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from PIL import ImageDraw

_BIG = {
    "A": ".###./#...#/#...#/#####/#...#/#...#/#...#",
    "B": "####./#...#/#...#/####./#...#/#...#/####.",
    "C": ".###./#...#/#..../#..../#..../#...#/.###.",
    "D": "####./#...#/#...#/#...#/#...#/#...#/####.",
    "E": "#####/#..../#..../####./#..../#..../#####",
    "F": "#####/#..../#..../####./#..../#..../#....",
    "G": ".###./#...#/#..../#.###/#...#/#...#/.####",
    "H": "#...#/#...#/#...#/#####/#...#/#...#/#...#",
    "I": "###/.#./.#./.#./.#./.#./###",
    "J": "..###/...#./...#./...#./#..#./#..#./.##..",
    "K": "#...#/#..#./#.#../##.../#.#../#..#./#...#",
    "L": "#..../#..../#..../#..../#..../#..../#####",
    "M": "#...#/##.##/#.#.#/#.#.#/#...#/#...#/#...#",
    "N": "#...#/#...#/##..#/#.#.#/#..##/#...#/#...#",
    "O": ".###./#...#/#...#/#...#/#...#/#...#/.###.",
    "P": "####./#...#/#...#/####./#..../#..../#....",
    "Q": ".###./#...#/#...#/#...#/#.#.#/#..#./.##.#",
    "R": "####./#...#/#...#/####./#.#../#..#./#...#",
    "S": ".####/#..../#..../.###./....#/....#/####.",
    "T": "#####/..#../..#../..#../..#../..#../..#..",
    "U": "#...#/#...#/#...#/#...#/#...#/#...#/.###.",
    "V": "#...#/#...#/#...#/#...#/#...#/.#.#./..#..",
    "W": "#...#/#...#/#...#/#.#.#/#.#.#/#.#.#/.#.#.",
    "X": "#...#/#...#/.#.#./..#../.#.#./#...#/#...#",
    "Y": "#...#/#...#/.#.#./..#../..#../..#../..#..",
    "Z": "#####/....#/...#./..#../.#.../#..../#####",
    "0": ".###./#...#/#..##/#.#.#/##..#/#...#/.###.",
    "1": ".#./##./.#./.#./.#./.#./###",
    "2": ".###./#...#/....#/...#./..#../.#.../#####",
    "3": "#####/...#./..#../...#./....#/#...#/.###.",
    "4": "...#./..##./.#.#./#..#./#####/...#./...#.",
    "5": "#####/#..../####./....#/....#/#...#/.###.",
    "6": "..##./.#.../#..../####./#...#/#...#/.###.",
    "7": "#####/....#/...#./..#../.#.../.#.../.#...",
    "8": ".###./#...#/#...#/.###./#...#/#...#/.###.",
    "9": ".###./#...#/#...#/.####/....#/...#./.##..",
    " ": ".../.../.../.../.../.../...",
    ">": "#.../.#../..#./...#/..#./.#../#...",
    "-": "..../..../..../####/..../..../....",
    ".": "././././././#",
    ":": "././#/././#/.",
    "/": "....#/....#/...#./..#../.#.../#..../#....",
    "+": "...../..#../..#../#####/..#../..#../.....",
    ",": "../../../../../.#/#.",
    "'": "#/#/././././.",
    "!": "#/#/#/#/#/./#",
    "?": ".###./#...#/....#/...#./..#../...../..#..",
    "(": "..#/.#./#../#../#../.#./..#",
    ")": "#../.#./..#/..#/..#/.#./#..",
}

_SMALL = {
    "A": ".#./#.#/###/#.#/#.#",
    "B": "##./#.#/##./#.#/##.",
    "C": ".##/#../#../#../.##",
    "D": "##./#.#/#.#/#.#/##.",
    "E": "###/#../##./#../###",
    "F": "###/#../##./#../#..",
    "G": ".##/#../#.#/#.#/.##",
    "H": "#.#/#.#/###/#.#/#.#",
    "I": "###/.#./.#./.#./###",
    "J": "..#/..#/..#/#.#/.#.",
    "K": "#.#/#.#/##./#.#/#.#",
    "L": "#../#../#../#../###",
    "M": "#...#/##.##/#.#.#/#...#/#...#",
    "N": "#..#/##.#/#.##/#..#/#..#",
    "O": ".#./#.#/#.#/#.#/.#.",
    "P": "##./#.#/##./#../#..",
    "Q": ".#./#.#/#.#/##./.##",
    "R": "##./#.#/##./#.#/#.#",
    "S": ".##/#../.#./..#/##.",
    "T": "###/.#./.#./.#./.#.",
    "U": "#.#/#.#/#.#/#.#/###",
    "V": "#.#/#.#/#.#/#.#/.#.",
    "W": "#...#/#...#/#.#.#/##.##/#...#",
    "X": "#.#/#.#/.#./#.#/#.#",
    "Y": "#.#/#.#/.#./.#./.#.",
    "Z": "###/..#/.#./#../###",
    "0": "###/#.#/#.#/#.#/###",
    "1": ".#./##./.#./.#./###",
    "2": "##./..#/.#./#../###",
    "3": "##./..#/.#./..#/##.",
    "4": "#.#/#.#/###/..#/..#",
    "5": "###/#../##./..#/##.",
    "6": ".##/#../###/#.#/###",
    "7": "###/..#/.#./.#./.#.",
    "8": "###/#.#/###/#.#/###",
    "9": "###/#.#/###/..#/##.",
    " ": "../../../../..",
    "-": ".../.../###/.../...",
    ".": "././././#",
    ":": "./#/./#/.",
    "/": "..#/..#/.#./#../#..",
    ">": "#../.#./..#/.#./#..",
    "<": "..#/.#./#../.#./..#",
    "+": ".../.#./###/.#./...",
    "'": "#/#/././.",
    ",": "./././#/#",
    "(": ".#/#./#./#./.#",
    ")": "#./.#/.#/.#/#.",
    "?": "##./..#/.#./.../.#.",
    "!": "#/#/#/./#",
    "↑": ".#./###/.#./.#./.#.",
    "↓": ".#./.#./.#./###/.#.",
    "→": ".../..#/###/..#/...",
}

def plain(text: str) -> str:
    """Strip accents so names like Aeroméxico use the plain glyphs."""
    return "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))


def _parse(table: dict[str, str], height: int) -> dict[str, list[str]]:
    glyphs = {ch: rows.split("/") for ch, rows in table.items()}
    for ch, rows in glyphs.items():
        assert len(rows) == height, f"glyph {ch!r} has {len(rows)} rows, not {height}"
        assert len({len(r) for r in rows}) == 1, f"glyph {ch!r} has uneven rows"
    return glyphs


@dataclass
class Font:
    name: str
    height: int
    glyphs: dict[str, list[str]]
    spacing: int = 1

    def _glyph(self, ch: str) -> list[str]:
        return self.glyphs.get(ch.upper()) or self.glyphs["?"]

    def width(self, text: str, scale: int = 1) -> int:
        text = plain(text)
        if not text:
            return 0
        total = sum(len(self._glyph(c)[0]) + self.spacing for c in text) - self.spacing
        return total * scale

    def draw(self, draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str,
             color: tuple[int, int, int], scale: int = 1) -> int:
        """Draw text with its top-left at xy. Returns the x after the text."""
        x, y = xy
        for ch in plain(text):
            rows = self._glyph(ch)
            for dy, row in enumerate(rows):
                for dx, px in enumerate(row):
                    if px == "#":
                        if scale == 1:
                            draw.point((x + dx, y + dy), fill=color)
                        else:
                            draw.rectangle(
                                (x + dx * scale, y + dy * scale,
                                 x + dx * scale + scale - 1, y + dy * scale + scale - 1),
                                fill=color,
                            )
            x += (len(rows[0]) + self.spacing) * scale
        return x

    @property
    def cell_width(self) -> int:
        """Width of one fixed-width cell: the widest normal glyph."""
        return 5 if self.height == 7 else 3

    def _cell(self, ch: str) -> int:
        """Cell width for a character: the standard cell, or wider for glyphs
        that need it (M, W, N in the small font) so they stay readable."""
        return max(self.cell_width, len(self._glyph(ch)[0]))

    def cells_text_width(self, text: str) -> int:
        text = plain(text).upper()
        return sum(self._cell(c) + 1 for c in text) - 1 if text else 0

    def draw_cells(self, draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str,
                   color: tuple[int, int, int], cells: int = 0,
                   tile: tuple[tuple[int, int, int], tuple[int, int, int]] | None = None,
                   align: str = "left", width: int = 0) -> None:
        """Airport-board style: one character per cell, each on a dim tile (top
        half lighter, like a split flap), filling a box `cells` standard cells
        wide (or exactly `width` pixels). Empty space gets empty tiles. Text
        that doesn't fit is cut."""
        box = width or self.cells_width(cells)
        text = plain(text).upper()
        while text and self.cells_text_width(text) > box:
            text = text[:-1]
        layout = [(c, self._cell(c)) for c in text]
        used = self.cells_text_width(text) + (1 if text else 0)
        empties = max(0, (box + 1 - used) // (self.cell_width + 1))
        blank = [(" ", self.cell_width)] * empties
        layout = blank + layout if align == "right" else layout + blank
        x0, y = xy
        if align == "right":
            x0 += box - (sum(w + 1 for _, w in layout) - 1)
        h = self.height
        top_h = (h + 1) // 2
        cx = x0
        for ch, cw in layout:
            if tile:
                draw.rectangle((cx, y, cx + cw - 1, y + top_h - 1), fill=tile[0])
                draw.rectangle((cx, y + top_h, cx + cw - 1, y + h - 1), fill=tile[1])
            if ch != " ":
                rows = self._glyph(ch)
                ox = cx + (cw - len(rows[0])) // 2
                for dy, row in enumerate(rows):
                    for dx, px in enumerate(row):
                        if px == "#":
                            draw.point((ox + dx, y + dy), fill=color)
            cx += cw + 1

    def cells_width(self, cells: int) -> int:
        return cells * (self.cell_width + 1) - 1

    def fit(self, text: str, max_width: int) -> str:
        """Trim text so it fits max_width pixels."""
        while text and self.width(text) > max_width:
            text = text[:-1]
        return text.rstrip()


BIG = Font("5x7", 7, _parse(_BIG, 7))
SMALL = Font("3x5", 5, _parse(_SMALL, 5))
