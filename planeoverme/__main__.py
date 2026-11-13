"""Command line entry point.

    python -m planeoverme start             set up if needed, then run and open the browser
    python -m planeoverme setup             choose your location, download data and logos
    python -m planeoverme update-data       download fresh aircraft data and logos
    python -m planeoverme run [--sample]    run the display
    python -m planeoverme nearby [--sample] list planes around home, closest first
    python -m planeoverme build-db          rebuild data/lookup.db from data/standing-data
    python -m planeoverme capture           save a live snapshot to the sample file
"""

from __future__ import annotations

import argparse
import sys

from .flight import enrich_basic, enrich_route, parse_aircraft
from .lookup import build_db
from .settings import load_settings, resolve_path
from .sources import SourceError, capture_sample, make_source, record


def cmd_build_db(_args: argparse.Namespace) -> None:
    print("Building data/lookup.db ...")
    for table, count in build_db().items():
        print(f"  {table:<12} {count:>8,} rows")


def cmd_capture(args: argparse.Namespace) -> None:
    settings = load_settings()
    path = resolve_path(args.path or settings["source"]["sample_file"])
    count = capture_sample(settings, path)
    print(f"Saved {count} aircraft to {path}")


def cmd_record(args: argparse.Namespace) -> None:
    settings = load_settings()
    path = resolve_path(args.path)
    print(f"Recording {args.minutes:g} min of live traffic to {path} ...")
    count = record(settings, path, args.minutes)
    print(f"Saved {count} snapshots")


def _fmt(value, suffix: str = "", width: int = 0) -> str:
    text = "-" if value is None else f"{value}{suffix}"
    return text.rjust(width) if width else text


def cmd_nearby(args: argparse.Namespace) -> None:
    settings = load_settings()
    if args.sample:
        settings["source"]["mode"] = "sample"
    home = settings["home"]
    online_routes = settings["source"]["mode"] == "live"

    try:
        raw = make_source(settings).fetch()
    except SourceError as exc:
        sys.exit(f"Could not get aircraft: {exc}")

    flights = [f for ac in raw if (f := parse_aircraft(ac, home["lat"], home["lon"]))]
    flights.sort(key=lambda f: f.distance_nm)
    print(f"{len(flights)} aircraft with a position ({settings['source']['mode']} data)\n")
    print(f"{'dist km':>7}  {'dir':>3}  {'flight':<8} {'airline':<22} {'type':<24} "
          f"{'alt ft':>6} {'trend':<10} {'route':<12}")
    for f in flights[: args.limit]:
        enrich_basic(f)
        enrich_route(f, online=online_routes)
        route = f"{f.origin.code}>{f.destination.code}" if f.origin and f.destination else "-"
        alt = "ground" if f.on_ground else _fmt(f.altitude_ft)
        flag = f"  !! {f.emergency}" if f.emergency else ""
        print(
            f"{f.distance_km:7.1f}  {round(f.bearing_deg):>3}  "
            f"{_fmt(f.flight_number):<8} {(f.airline_name or '-')[:22]:<22} "
            f"{(f.type_name or f.type_code or '-')[:24]:<24} {alt:>6} "
            f"{f.vertical_trend:<10} {route:<12}{flag}"
        )


def cmd_setup(_args: argparse.Namespace) -> None:
    from . import firstrun

    firstrun.ask_location()
    firstrun.ensure_ready(interactive=False)
    print("\nAll set. Start the display with:  python -m planeoverme start")


def cmd_update_data(_args: argparse.Namespace) -> None:
    from . import firstrun

    firstrun.download_reference_data()
    firstrun.download_logos()


def cmd_start(args: argparse.Namespace) -> None:
    from . import firstrun

    firstrun.ensure_ready()
    args.open_browser = True
    cmd_run(args)


def cmd_run(args: argparse.Namespace) -> None:
    import logging

    from .app import App

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings()
    if args.sample:
        settings["source"]["mode"] = "sample"
    if args.sample_file:
        settings["source"]["sample_file"] = args.sample_file

    backend = settings["display"]["backend"]
    if backend != "web":
        sys.exit(f"Display backend {backend!r} is not built yet. Use 'web'.")
    from .display.web import WebDisplay

    display = WebDisplay(settings["display"]["web_port"])
    app = App(settings, display)
    display.state_provider = app.state
    display.start()
    url = f"http://localhost:{display.port}"
    print(f"LED simulator running at {url}  (press Ctrl+C to stop)")
    if getattr(args, "open_browser", False):
        import webbrowser

        webbrowser.open(url)
    app.run()


def cmd_logos(args: argparse.Namespace) -> None:
    from .logos import LOGO_DIR, convert_all

    auto, drawn, skipped = convert_all(force=args.force)
    print(f"Wrote {auto + drawn} logo badges to {LOGO_DIR}: {drawn} hand-drawn, {auto} automatic"
          + (f", {skipped} hand-edited kept" if skipped else ""))


def main() -> None:
    parser = argparse.ArgumentParser(prog="planeoverme")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, func, text in (
        ("start", cmd_start, "set up if needed, then run and open the browser"),
        ("run", cmd_run, "run the display (browser simulator)"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("--sample", action="store_true", help="replay saved data instead of live")
        p.add_argument("--sample-file", help="which saved file to replay")
        p.set_defaults(func=func)

    sub.add_parser("setup", help="choose your location, download data and logos"
                   ).set_defaults(func=cmd_setup)
    sub.add_parser("update-data", help="download fresh aircraft data and logos"
                   ).set_defaults(func=cmd_update_data)

    sub.add_parser("build-db").set_defaults(func=cmd_build_db)

    p = sub.add_parser("logos", help="convert assets/logos-source to 7x7 LED logo badges")
    p.add_argument("--force", action="store_true", help="also overwrite hand-tuned logos")
    p.set_defaults(func=cmd_logos)

    p = sub.add_parser("capture")
    p.add_argument("path", nargs="?")
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("record", help="record live traffic to a .jsonl file for replay")
    p.add_argument("--minutes", type=float, default=10)
    p.add_argument("--path", default="samples/recording.jsonl")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("nearby")
    p.add_argument("--sample", action="store_true", help="use the saved sample file")
    p.add_argument("--limit", type=int, default=15)
    p.set_defaults(func=cmd_nearby)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
