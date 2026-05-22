"""CLI entry point for the scouting tool."""

import argparse
import logging
import sys

import config
from database import get_engine, init_db, load_per90_dataframe
from normalizer import normalise_all
from scorer import score_all
from scraper import scrape_all


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Suppress noisy third-party loggers unless in verbose mode
    if not verbose:
        for name in ("urllib3", "requests", "selenium", "soccerdata"):
            logging.getLogger(name).setLevel(logging.WARNING)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scouting",
        description="FBref football scouting tool",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")

    sub = p.add_subparsers(dest="command", required=True)

    # ── scrape ─────────────────────────────────────────────────────────────
    s = sub.add_parser("scrape", help="Fetch raw stats from FBref")
    s.add_argument(
        "--leagues",
        nargs="+",
        default=None,
        metavar="LEAGUE",
        help=(
            'Leagues to scrape, e.g. "ENG-Premier League". '
            f"Defaults to all {len(config.LEAGUES)} configured leagues."
        ),
    )
    s.add_argument("--season", default=None, help=f"Season (default: {config.SEASON})")
    s.add_argument(
        "--categories",
        nargs="+",
        default=None,
        metavar="CAT",
        help="Subset of FBref categories to scrape.",
    )

    # ── normalise ──────────────────────────────────────────────────────────
    n = sub.add_parser("normalise", help="Compute per-90 stats from raw data")
    n.add_argument("--season", default=None, help=f"Season (default: {config.SEASON})")

    # ── score ──────────────────────────────────────────────────────────────
    sc = sub.add_parser("score", help="Compute talent scores")
    sc.add_argument("--season", default=None, help=f"Season (default: {config.SEASON})")

    # ── run-all ────────────────────────────────────────────────────────────
    ra = sub.add_parser("run-all", help="Run scrape → normalise → score in sequence")
    ra.add_argument("--leagues", nargs="+", default=None, metavar="LEAGUE")
    ra.add_argument("--season", default=None)
    ra.add_argument("--categories", nargs="+", default=None, metavar="CAT")

    # ── show ───────────────────────────────────────────────────────────────
    sh = sub.add_parser("show", help="Print top players from the per-90 table")
    sh.add_argument("--season", default=None)
    sh.add_argument("--position", default=None, help="Filter by position group (FW/MF/DF/GK)")
    sh.add_argument("--league", default=None, help="Filter by league name (partial match)")
    sh.add_argument("-n", "--top", type=int, default=20, help="Number of rows to show")

    return p


def cmd_scrape(args) -> None:
    scrape_all(
        leagues=args.leagues,
        season=args.season,
        categories=args.categories,
    )


def cmd_normalise(args) -> None:
    engine = get_engine()
    init_db(engine)
    normalise_all(season=args.season, engine=engine)


def cmd_score(args) -> None:
    engine = get_engine()
    score_all(season=args.season, engine=engine)


def cmd_run_all(args) -> None:
    engine = get_engine()
    init_db(engine)
    scrape_all(leagues=args.leagues, season=args.season, categories=args.categories)
    normalise_all(season=args.season, engine=engine)
    score_all(season=args.season, engine=engine)


def cmd_show(args) -> None:
    import pandas as pd

    engine = get_engine()
    df = load_per90_dataframe(engine)
    if df.empty:
        print("No per-90 data in the database. Run 'scrape' and 'normalise' first.")
        return

    season = args.season or config.SEASON
    df = df[df["season"] == season]

    if args.position:
        from scorer import _position_group
        df = df[df["position"].apply(_position_group) == args.position.upper()]

    if args.league:
        df = df[df["league"].str.contains(args.league, case=False, na=False)]

    # Pick a few headline columns if they exist
    show_cols = ["name", "club", "league", "position", "minutes"]
    stat_candidates = [
        "goals_per90", "assists_per90", "xg_per90", "progressive_passes_per90",
        "tackles_per90", "progressive_carries_per90",
    ]
    show_cols += [c for c in stat_candidates if c in df.columns]

    available = [c for c in show_cols if c in df.columns]
    print(df[available].sort_values("minutes", ascending=False).head(args.top).to_string(index=False))


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    _setup_logging(args.verbose)

    dispatch = {
        "scrape": cmd_scrape,
        "normalise": cmd_normalise,
        "score": cmd_score,
        "run-all": cmd_run_all,
        "show": cmd_show,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
