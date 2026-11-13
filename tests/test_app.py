import time

from planeoverme.app import App
from planeoverme.settings import load_settings
from planeoverme.sources import RateLimited


class FakeSource:
    def __init__(self, result):
        self.result, self.calls = result, 0
        self.remaining, self.reset_s = None, None

    def fetch(self):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def make_app(result, quiet=""):
    s = load_settings()
    s["source"] = {**s["source"], "mode": "live", "quiet_hours": quiet}
    app = App(s, display=None)
    app.source = FakeSource(result)
    return app


PLANE = {"hex": "39cf05", "flight": "AFR740 ", "t": "A320", "lat": 48.863, "lon": 2.360,
         "alt_baro": 3000, "gs": 200, "track": 90, "baro_rate": 1000, "seen": 1}


def test_daily_limit_blocks_until_reset():
    app = make_app(RateLimited("too many", reset_s=3600))
    app.poll_once()
    assert app.blocked_until and app.blocked_until - time.time() > 3500
    assert app.next_poll_at > app.blocked_until
    assert app.status(time.time()).startswith("LIMIT TILL")


def test_successful_poll_paces_next_request():
    app = make_app([PLANE])
    app.home = {"lat": 48.853, "lon": 2.350}
    app.poll_once()
    wait = app.next_poll_at - time.time()
    assert 15 <= wait <= 400            # paced by the budget, at least poll_seconds
    app.tick()
    assert app.evaluated and app.evaluated[0][0].callsign == "AFR740"


def test_quiet_hours_status():
    app = make_app([PLANE], quiet="00:00-23:59")
    assert app.status(time.time()) == "NIGHT MODE"
