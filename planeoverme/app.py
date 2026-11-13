"""The main loop: fetch planes, apply rules, pick one, draw frames.

Two threads:
- data: asks the source for planes when the request budget allows (live
  mode), and every few seconds re-runs the rules on estimated positions so
  the selection and the numbers on screen keep moving between API calls
- render: draws ~10 frames per second and sends them to the display
Nothing in either loop is allowed to crash the app: errors are logged and the
display falls back to the last selection (within the grace period) or idle.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from typing import Any, Optional

from . import lookup
from .budget import Budget, next_utc_midnight, parse_quiet_hours
from .flight import Flight, enrich_basic, enrich_route, parse_aircraft, predict
from .render.screens import Scene, render
from .rules import Rules, Verdict
from .sources import RateLimited, SourceError, make_source
from .tracker import Tracker

log = logging.getLogger("planeoverme")

FPS = 10
TICK_S = 5            # re-run rules on estimated positions this often
RETRY_ERROR_S = 60    # after a network error, try again after this long


class App:
    def __init__(self, settings: dict[str, Any], display):
        self.settings = settings
        self.home = settings["home"]
        src = settings["source"]
        self.source = make_source(settings)
        self.live = src["mode"] == "live"
        self.rules = Rules(settings)
        self.tracker = Tracker(settings)
        self.display = display
        self.render_cfg = {**settings["display"], **settings["timing"]}

        self.budget = Budget(
            daily_limit=src.get("daily_request_limit", 500),
            min_interval_s=src["poll_seconds"],
            quiet=parse_quiet_hours(src.get("quiet_hours", "")),
        )
        self.max_data_age_s = src.get("max_data_age_s", 300)
        self.estimate = src.get("estimate_movement", True)

        self.lock = threading.Lock()
        self.flights: list[Flight] = []          # as of the last fetch
        self.fetched_at: Optional[float] = None
        self.evaluated: list[tuple[Flight, Verdict]] = []
        self.source_ok = True
        self.last_error: Optional[str] = None
        self.next_poll_at = 0.0
        self.blocked_until: Optional[float] = None
        self.remaining: Optional[int] = None     # requests left today (estimate)
        self.requests_today = 0
        self.request_day = 0.0
        self.stop = threading.Event()

    # -- data ---------------------------------------------------------------

    def _count_request(self, now: float) -> None:
        day = next_utc_midnight(now)
        if day != self.request_day:
            self.request_day, self.requests_today = day, 0
        self.requests_today += 1

    def _remaining_today(self) -> int:
        """Requests left until the daily reset: what adsb.lol reported when
        that figure is about the daily window, else our own count."""
        own = self.budget.daily_limit - self.requests_today
        api_remaining = getattr(self.source, "remaining", None)
        api_reset = getattr(self.source, "reset_s", None)
        if api_remaining is not None and api_reset and api_reset > 120:
            return min(own, api_remaining)
        return own

    def poll_once(self) -> None:
        now = time.time()
        if self.live:
            self._count_request(now)
        try:
            raw = self.source.fetch()
        except RateLimited as exc:
            if exc.reset_s and exc.reset_s > 120:
                self.blocked_until = now + exc.reset_s
                self.next_poll_at = self.blocked_until + 5
                log.warning("daily request limit reached; no requests until %s",
                            dt.datetime.fromtimestamp(self.blocked_until).strftime("%H:%M"))
            else:
                self.next_poll_at = now + max(30, exc.reset_s or 30)
                log.info("rate limited, next request in %.0fs", self.next_poll_at - now)
            self.remaining = 0 if self.blocked_until else self.remaining
            return
        except SourceError as exc:
            log.warning("fetch failed: %s", exc)
            with self.lock:
                self.source_ok, self.last_error = False, str(exc)
            self.next_poll_at = now + RETRY_ERROR_S
            return

        flights = [f for ac in raw if (f := parse_aircraft(ac, self.home["lat"], self.home["lon"]))]
        for f in flights:
            enrich_basic(f)
        with self.lock:
            self.flights, self.fetched_at = flights, now
            self.source_ok, self.last_error = True, None
        self.blocked_until = None
        if self.live:
            self.remaining = self._remaining_today()
            self.next_poll_at = now + self.budget.interval(now, self.remaining)
        else:
            self.next_poll_at = now + self.settings["source"]["poll_seconds"]

    def tick(self) -> None:
        """Re-run the rules on where the planes probably are now."""
        now = time.time()
        with self.lock:
            flights, fetched_at = self.flights, self.fetched_at
        evaluated: list[tuple[Flight, Verdict]] = []
        if fetched_at and now - fetched_at <= self.max_data_age_s:
            age = now - fetched_at
            for f in flights:
                p = predict(f, age, self.home["lat"], self.home["lon"]) if self.estimate else f
                v = self.rules.evaluate(p)
                if v.eligible and not p.route_source:
                    enrich_route(f, online=self.live)        # cached per callsign
                    p.origin, p.destination, p.route_source = f.origin, f.destination, f.route_source
                evaluated.append((p, v))
            evaluated.sort(key=lambda fv: fv[0].distance_nm)
        with self.lock:
            self.evaluated = evaluated
            self.tracker.update(evaluated, now)

    def data_loop(self) -> None:
        while not self.stop.is_set():
            now = time.time()
            try:
                quiet = self.live and self.budget.is_quiet(now)
                blocked = self.blocked_until and now < self.blocked_until
                if not quiet and not blocked and now >= self.next_poll_at:
                    self.poll_once()
                self.tick()
            except Exception:
                log.exception("unexpected error in the data loop")
            self.stop.wait(TICK_S)

    def status(self, now: float) -> Optional[str]:
        """A short message for the clock screen, or None to show the plane count."""
        if self.live and self.budget.is_quiet(now):
            return "NIGHT MODE"
        if self.blocked_until and now < self.blocked_until:
            return "LIMIT TILL " + dt.datetime.fromtimestamp(self.blocked_until).strftime("%H:%M")
        if not self.source_ok or not self.fetched_at or now - self.fetched_at > self.max_data_age_s:
            return "NO DATA"
        return None

    # -- render -------------------------------------------------------------

    def scene(self) -> Scene:
        now = time.time()
        with self.lock:
            sel = self.tracker.current
            flight = sel.flight if sel else None
            if flight and self.estimate and self.fetched_at:
                # Keep altitude and distance moving every frame, not just every tick.
                base = next((f for f in self.flights if f.hex == flight.hex), None)
                if base:
                    moved = predict(base, now - self.fetched_at, self.home["lat"], self.home["lon"])
                    moved.origin, moved.destination = flight.origin, flight.destination
                    flight = moved
            status = self.status(now)
            return Scene(
                flight=flight,
                tier=sel.verdict.tier if sel else 2,
                shown_since=sel.shown_since if sel else 0.0,
                planes_nearby=len(self.evaluated),
                source_ok=status is None,
                status=status,
            )

    def render_loop(self) -> None:
        while not self.stop.is_set():
            started = time.time()
            try:
                self.display.show(render(self.scene(), self.render_cfg))
            except Exception:
                log.exception("unexpected error while rendering")
            self.stop.wait(max(0.0, 1 / FPS - (time.time() - started)))

    # -- debug state for the browser -------------------------------------------

    def state(self) -> dict[str, Any]:
        now = time.time()
        with self.lock:
            sel = self.tracker.current
            planes = []
            for f, v in self.evaluated:
                d = f.to_dict()
                d.update(
                    eligible=v.eligible, tier=v.tier_name, reasons=v.reasons,
                    watch_match=v.watch_match,
                    cooldown_s=max(0, round(self.tracker.cooldown.get(f.hex, 0) - now)),
                    selected=bool(sel and sel.flight.hex == f.hex),
                )
                planes.append(d)
            return {
                "now": now,
                "home": self.home,
                "source": {
                    "mode": self.settings["source"]["mode"],
                    "ok": self.source_ok,
                    "error": self.last_error,
                    "last_update": self.fetched_at,
                    "next_update_in_s": max(0, round(self.next_poll_at - now)) if self.live else None,
                    "requests_left_today": self.remaining,
                    "status": self.status(now),
                },
                "rules": self.settings["rules"],
                "timing": self.settings["timing"],
                "selected": sel and {
                    "hex": sel.flight.hex,
                    "why": sel.why,
                    "shown_for_s": round(now - sel.shown_since),
                    "tier": sel.verdict.tier_name,
                },
                "planes": planes,
                "history": self.tracker.history,
                "landmarks": self.landmarks(),
            }

    def landmarks(self) -> list[dict[str, Any]]:
        """Airports drawn on the browser radar. "auto" picks the airports
        nearest the active location, so switching location needs no edit."""
        from .flight import bearing_deg, distance_nm
        home_lat, home_lon = self.home["lat"], self.home["lon"]
        setting = self.settings.get("web", {}).get("landmarks", "auto")
        if setting == "auto":
            radius = max(self.settings["rules"]["watchlist"]["max_distance_km"],
                         self.settings["rules"]["max_distance_km"])
            airports = lookup.airports_near(home_lat, home_lon, radius)
        else:
            airports = [a for a in (lookup.airport(c) for c in setting) if a]
        out = []
        for a in airports:
            km = distance_nm(home_lat, home_lon, a["lat"], a["lon"]) * 1.852
            if setting == "auto" and km > radius:
                continue
            out.append({"code": a["iata"] or a["icao"], "distance_km": km,
                        "bearing_deg": bearing_deg(home_lat, home_lon, a["lat"], a["lon"])})
        return sorted(out, key=lambda x: x["distance_km"])[:6]

    # -- run ----------------------------------------------------------------

    def run(self) -> None:
        threads = [
            threading.Thread(target=self.data_loop, name="data", daemon=True),
            threading.Thread(target=self.render_loop, name="render", daemon=True),
        ]
        for t in threads:
            t.start()
        try:
            while not self.stop.is_set():
                self.stop.wait(1)
        except KeyboardInterrupt:
            self.stop.set()
