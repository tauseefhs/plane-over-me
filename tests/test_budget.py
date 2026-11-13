import datetime as dt

import pytest

from planeoverme.budget import Budget, next_utc_midnight, parse_quiet_hours
from planeoverme.flight import Flight, predict


def local_ts(hour: int, minute: int = 0) -> float:
    return dt.datetime(2026, 10, 20, hour, minute).timestamp()


def test_parse_quiet_hours():
    assert parse_quiet_hours("23:00-07:00") == (1380, 420)
    assert parse_quiet_hours("") is None


def test_quiet_hours_wrap_midnight():
    b = Budget(quiet=(1380, 420))
    assert b.is_quiet(local_ts(23, 30)) and b.is_quiet(local_ts(3)) and b.is_quiet(local_ts(6, 59))
    assert not b.is_quiet(local_ts(7)) and not b.is_quiet(local_ts(12)) and not b.is_quiet(local_ts(22, 59))
    assert b.quiet_ends_in(local_ts(6, 0)) == pytest.approx(3600, abs=60)


def test_full_day_budget_paces_to_about_two_minutes():
    """500 requests over 16 active hours: one every ~2 minutes."""
    b = Budget(daily_limit=500, min_interval_s=15, quiet=(1380, 420))
    now = local_ts(7)
    interval = b.interval(now, remaining=500)
    active = b.active_seconds(now, next_utc_midnight(now))
    assert interval == pytest.approx(active / 480, rel=0.01)
    assert 90 <= interval <= 150


def test_never_faster_than_minimum():
    b = Budget(daily_limit=500, min_interval_s=15)
    now = next_utc_midnight(local_ts(12)) - 600       # 10 minutes before reset
    assert b.interval(now, remaining=400) == 15


def test_nothing_left_waits_for_reset():
    b = Budget(daily_limit=500, min_interval_s=15)
    now = local_ts(15)
    assert b.interval(now, remaining=10) == pytest.approx(next_utc_midnight(now) - now + 5)


def test_budget_lasts_until_reset():
    """Simulate a day: the requests made never exceed the allowance."""
    b = Budget(daily_limit=500, min_interval_s=15, quiet=(1380, 420))
    start = next_utc_midnight(local_ts(12)) - 86400
    t, used = start, 0
    while t < start + 86400:
        if not b.is_quiet(t):
            used += 1
            t += b.interval(t, 500 - used)
        else:
            t += 60
    assert used <= 500 - b.reserve + 1


def plane(**kw) -> Flight:
    f = Flight(hex="a", callsign="X", lat=48.853, lon=2.350, altitude_ft=3000, on_ground=False,
               ground_speed_kt=240, vertical_rate_fpm=1200, track_deg=90, squawk=None,
               emergency=None, distance_nm=0, bearing_deg=0)
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def test_predict_moves_along_track_and_climbs():
    p = predict(plane(), 60, 48.853, 2.350)
    assert p.distance_km == pytest.approx(240 / 60 * 1.852, rel=0.02)   # 4 NM east
    assert p.bearing_deg == pytest.approx(90, abs=1)
    assert p.altitude_ft == 4200


def test_predict_never_below_ground_and_keeps_original():
    f = plane(altitude_ft=500, vertical_rate_fpm=-1500)
    p = predict(f, 60, 48.853, 2.350)
    assert p.altitude_ft == 0
    assert f.altitude_ft == 500 and f.lat == 48.853      # original untouched
