import copy

import pytest

from planeoverme.flight import Flight
from planeoverme.rules import EMERGENCY, NORMAL, WATCHLIST, Rules
from planeoverme.tracker import Tracker

SETTINGS = {
    "rules": {
        "max_distance_km": 18,
        "max_altitude_ft": 25000,
        "airlines_only": True,
        "airliners_only": True,
        "exclude_airlines": ["XXX"],
        "max_position_age_s": 60,
        "watchlist": {"max_distance_km": 40, "airlines": [], "types": ["A388"],
                      "registrations": ["F-HPJA"]},
        "emergency": {"max_distance_km": 45},
    },
    "timing": {"min_display_s": 20, "max_display_s": 60, "cooldown_min": 5,
               "missing_grace_s": 20},
}


def plane(hex_code="aaaaaa", km=5.0, alt=3000, airline=True, **kw) -> Flight:
    f = Flight(
        hex=hex_code, callsign=f"AFR{hex_code[:3].upper()}", lat=48.9, lon=2.5,
        altitude_ft=alt, on_ground=False, ground_speed_kt=250, vertical_rate_fpm=0,
        track_deg=90, squawk="1000", emergency=None, distance_nm=km / 1.852,
        bearing_deg=0, seen_s=1,
    )
    if airline:
        f.airline_icao, f.airline_iata, f.airline_name = "AFR", "AF", "Air France"
    for k, v in kw.items():
        setattr(f, k, v)
    return f


@pytest.fixture
def rules():
    return Rules(SETTINGS)


def test_basic_eligibility(rules):
    assert rules.evaluate(plane()).eligible
    assert not rules.evaluate(plane(km=25)).eligible
    assert not rules.evaluate(plane(alt=30000)).eligible
    assert not rules.evaluate(plane(airline=False)).eligible
    assert not rules.evaluate(plane(on_ground=True, alt=None)).eligible
    assert not rules.evaluate(plane(seen_s=120)).eligible


def test_rejection_reasons_are_listed(rules):
    v = rules.evaluate(plane(km=25, alt=30000))
    assert len(v.reasons) == 2


def test_watchlist_ignores_altitude_and_uses_own_distance(rules):
    v = rules.evaluate(plane(km=35, alt=38000, type_code="A388"))
    assert v.eligible and v.tier == WATCHLIST
    v = rules.evaluate(plane(km=10, airline=False, registration="FHPJA"))
    assert v.eligible and v.tier == WATCHLIST


def test_emergency_wins(rules):
    v = rules.evaluate(plane(km=40, alt=30000, emergency="emergency"))
    assert v.eligible and v.tier == EMERGENCY


def run(tracker, rules, planes, now):
    sel = tracker.update([(p, rules.evaluate(p)) for p in planes], now)
    return sel.flight.hex if sel else None


def test_picks_closest(rules):
    t = Tracker(SETTINGS)
    assert run(t, rules, [plane("aaa", km=8), plane("bbb", km=3)], 0) == "bbb"


def test_sticky_until_min_display(rules):
    t = Tracker(SETTINGS)
    assert run(t, rules, [plane("aaa", km=8)], 0) == "aaa"
    # A closer plane appears, but aaa has only been up 10 s.
    assert run(t, rules, [plane("aaa", km=8), plane("bbb", km=3)], 10) == "aaa"
    # After 20 s, switch.
    assert run(t, rules, [plane("aaa", km=8), plane("bbb", km=3)], 21) == "bbb"


def test_current_stays_while_it_is_best_until_max(rules):
    t = Tracker(SETTINGS)
    for now in range(0, 60, 10):
        assert run(t, rules, [plane("aaa", km=3), plane("bbb", km=8)], now) == "aaa"
    # 60 s is up: bbb gets its turn even though aaa is closer.
    assert run(t, rules, [plane("aaa", km=3), plane("bbb", km=8)], 60) == "bbb"
    # aaa is on cooldown, so bbb keeps the screen after its own 60 s.
    assert run(t, rules, [plane("aaa", km=3), plane("bbb", km=8)], 130) == "bbb"


def test_stays_past_max_when_alone(rules):
    t = Tracker(SETTINGS)
    for now in range(0, 300, 10):
        assert run(t, rules, [plane("aaa", km=3)], now) == "aaa"


def test_dropped_at_once_when_out_of_range(rules):
    t = Tracker(SETTINGS)
    assert run(t, rules, [plane("aaa", km=3)], 0) == "aaa"
    assert run(t, rules, [plane("aaa", km=25)], 10) is None


def test_higher_tier_takes_over_immediately(rules):
    t = Tracker(SETTINGS)
    assert run(t, rules, [plane("aaa", km=3)], 0) == "aaa"
    big = plane("bbb", km=30, alt=37000, type_code="A388")
    assert run(t, rules, [plane("aaa", km=3), big], 5) == "bbb"


def test_grace_then_idle(rules):
    t = Tracker(SETTINGS)
    assert run(t, rules, [plane("aaa")], 0) == "aaa"
    assert run(t, rules, [], 15) == "aaa"      # briefly missing: keep it
    assert run(t, rules, [], 25) is None       # gone: idle


def test_cooldown(rules):
    t = Tracker(SETTINGS)
    a, b = plane("aaa", km=3), plane("bbb", km=8)
    assert run(t, rules, [a], 0) == "aaa"
    assert run(t, rules, [], 30) is None                   # aaa left, on cooldown
    assert run(t, rules, [a, b], 40) == "bbb"               # aaa back but cooling down
    assert run(t, rules, [a], 55) == "bbb"                  # bbb missing, in grace
    assert run(t, rules, [a], 30 + 300 + 30) == "aaa"       # cooldown over


def test_emergency_ignores_cooldown(rules):
    t = Tracker(SETTINGS)
    a = plane("aaa", km=3)
    run(t, rules, [a], 0)
    run(t, rules, [], 30)
    e = copy.copy(a)
    e.emergency = "emergency"
    assert run(t, rules, [e], 40) == "aaa"


def test_business_jets_are_not_airliners(rules):
    netjets = plane(type_code="C56X", type_name="Cessna Citation XLS")
    assert not rules.evaluate(netjets).eligible
    assert any("not an airliner" in r for r in rules.evaluate(netjets).reasons)
    assert rules.evaluate(plane(type_code="A320")).eligible
    assert rules.evaluate(plane(type_code="DH8D")).eligible        # regional turboprop
    assert rules.evaluate(plane(type_code=None)).eligible          # unknown type: allowed


def test_excluded_airline(rules):
    f = plane()
    f.airline_icao = "XXX"
    assert not rules.evaluate(f).eligible


def test_watchlist_still_ignores_airliner_rule(rules):
    # A watchlisted registration (e.g. a special jet) is still shown.
    assert rules.evaluate(plane(type_code="GLF6", airline=False, registration="F-HPJA")).eligible
