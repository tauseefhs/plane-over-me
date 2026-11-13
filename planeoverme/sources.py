"""Where aircraft positions come from.

Every source returns the raw list of aircraft dicts in adsb.lol v2 format.
Switch sources with [source] mode in settings.toml.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Protocol

import requests

from .settings import resolve_path

ADSB_LOL_POINT = "https://api.adsb.lol/v2/point/{lat}/{lon}/{radius}"
USER_AGENT = "plane-over-me/0.1 (personal home display)"


class SourceError(Exception):
    """Raised when a source cannot deliver data. Callers keep running."""


class RateLimited(SourceError):
    """The API asked us to slow down (HTTP 429). Not an outage.
    `reset_s`: seconds until the limit resets, if the API said so."""

    def __init__(self, message: str, reset_s: float | None = None):
        super().__init__(message)
        self.reset_s = reset_s


def _first_int(value: str | None) -> int | None:
    """Rate-limit headers can list several windows ("0, 3"); use the first."""
    try:
        return int(str(value).split(",")[0].split(";")[0].strip())
    except (TypeError, ValueError):
        return None


class Source(Protocol):
    def fetch(self) -> list[dict[str, Any]]: ...


class AdsbLolSource:
    def __init__(self, lat: float, lon: float, radius_nm: float, timeout: float):
        self.url = ADSB_LOL_POINT.format(lat=lat, lon=lon, radius=int(radius_nm))
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        # What adsb.lol last said about its limits (None if unknown).
        self.remaining: int | None = None
        self.reset_s: int | None = None

    def fetch_raw(self) -> dict[str, Any]:
        try:
            resp = self.session.get(self.url, timeout=self.timeout)
            self.remaining = _first_int(resp.headers.get("x-ratelimit-remaining"))
            self.reset_s = _first_int(resp.headers.get("x-ratelimit-reset"))
            if resp.status_code == 429:
                raise RateLimited("adsb.lol: too many requests", self.reset_s)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise SourceError(f"adsb.lol: {exc}") from exc

    def fetch(self) -> list[dict[str, Any]]:
        return self.fetch_raw().get("ac") or []


class SampleSource:
    """Replays saved data, so everything works without internet.

    A .json file is one frozen snapshot. A .jsonl file is a recording (one
    snapshot per line); each fetch returns the next one and loops at the end.
    """

    def __init__(self, path: Path):
        self.path = path
        self.snapshots: list[dict[str, Any]] = []
        self.index = 0

    def _load(self) -> None:
        try:
            with self.path.open() as f:
                if self.path.suffix == ".jsonl":
                    self.snapshots = [json.loads(line) for line in f if line.strip()]
                else:
                    self.snapshots = [json.load(f)]
        except (OSError, ValueError) as exc:
            raise SourceError(f"sample file {self.path}: {exc}") from exc
        if not self.snapshots:
            raise SourceError(f"sample file {self.path} is empty")

    def fetch(self) -> list[dict[str, Any]]:
        if not self.snapshots:
            self._load()
        snap = self.snapshots[self.index % len(self.snapshots)]
        self.index += 1
        return snap.get("ac") or []


def make_source(settings: dict[str, Any]) -> Source:
    src = settings["source"]
    if src["mode"] == "sample":
        return SampleSource(resolve_path(src["sample_file"]))
    if src["mode"] == "live":
        home = settings["home"]
        return AdsbLolSource(
            home["lat"], home["lon"], src["search_radius_nm"], src["timeout_seconds"]
        )
    raise ValueError(f"Unknown source mode: {src['mode']!r}")


def _live_source(settings: dict[str, Any]) -> AdsbLolSource:
    home, src = settings["home"], settings["source"]
    return AdsbLolSource(
        home["lat"], home["lon"], src["search_radius_nm"], src["timeout_seconds"]
    )


def capture_sample(settings: dict[str, Any], path: Path) -> int:
    """Save one live response to disk for offline use. Returns plane count."""
    data = _live_source(settings).fetch_raw()
    data["captured_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(data, f, indent=1)
    return len(data.get("ac") or [])


def record(settings: dict[str, Any], path: Path, minutes: float) -> int:
    """Append one live snapshot per poll interval to a .jsonl file for replay.
    Returns the number of snapshots written. Network errors are skipped."""
    live = _live_source(settings)
    interval = settings["source"]["poll_seconds"]
    end = time.time() + minutes * 60
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("w") as f:
        while time.time() < end:
            started = time.time()
            try:
                data = live.fetch_raw()
                data["captured_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                f.write(json.dumps(data) + "\n")
                f.flush()
                written += 1
                interval = max(settings["source"]["poll_seconds"], interval - 1)
            except RateLimited as exc:
                if exc.reset_s and exc.reset_s > 120:
                    print(f"daily limit reached; it resets in {exc.reset_s / 3600:.1f} h")
                    break
                interval = min(60, interval * 1.5)
            except SourceError as exc:
                print(f"skipped one snapshot: {exc}")
            time.sleep(max(0.0, interval - (time.time() - started)))
    return written
