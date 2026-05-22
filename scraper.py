"""FBref scraping via the soccerdata library."""

import logging
import time
from typing import Optional

import pandas as pd
import soccerdata as sd
from tqdm import tqdm

import config
from database import (
    Session,
    get_engine,
    get_or_create_player,
    init_db,
    upsert_raw_stats,
)

log = logging.getLogger(__name__)


# ── Retry decorator ────────────────────────────────────────────────────────

def _with_retry(fn, *args, **kwargs):
    """Call *fn* with exponential-backoff retry on any exception."""
    last_exc: Optional[Exception] = None
    for attempt in range(config.RETRY_MAX_ATTEMPTS):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            wait = config.RETRY_BACKOFF_BASE ** attempt
            log.warning(
                "Attempt %d/%d failed (%s). Retrying in %ds…",
                attempt + 1,
                config.RETRY_MAX_ATTEMPTS,
                exc,
                wait,
            )
            time.sleep(wait)
    raise RuntimeError(
        f"All {config.RETRY_MAX_ATTEMPTS} attempts failed"
    ) from last_exc


# ── FBref reader factory ───────────────────────────────────────────────────

def _make_reader(leagues: list[str], season: str) -> sd.FBref:
    return sd.FBref(
        leagues=leagues,
        seasons=season,
        data_dir=config.CACHE_DIR,
        no_cache=False,
    )


# ── Core scraping logic ────────────────────────────────────────────────────

def _extract_fbref_id(index_val) -> str:
    """FBref player URLs contain a stable hash — use it as the primary key."""
    # soccerdata exposes the player URL in the index when stat_type includes it;
    # fall back to a string representation of whatever key is present.
    if isinstance(index_val, tuple):
        return str(index_val[-1])
    return str(index_val)


def _parse_player_meta(row: pd.Series, league: str, season: str) -> dict:
    """Extract player metadata from a stat row."""

    def _safe(col: str):
        return row.get(col) if col in row.index else None

    age_raw = _safe("age")
    try:
        age = int(str(age_raw).split("-")[0]) if age_raw else None
    except (ValueError, AttributeError):
        age = None

    return {
        "name": _safe("player") or _safe("Player") or "Unknown",
        "age": age,
        "nationality": _safe("nationality") or _safe("nation"),
        "position": _safe("position") or _safe("pos"),
        "club": _safe("squad") or _safe("team"),
        "league": league,
        "season": season,
    }


def scrape_league(
    league: str,
    season: str,
    categories: Optional[list[str]] = None,
    engine=None,
) -> None:
    """Scrape all configured stat categories for one league/season pair."""
    if categories is None:
        categories = config.FBREF_CATEGORIES
    if engine is None:
        engine = get_engine()

    fbref = _make_reader([league], season)

    total_cats = len(categories)
    with Session(engine) as session:
        for cat_idx, category in enumerate(categories, 1):
            print(f"  [{cat_idx}/{total_cats}] {category} ...", end=" ", flush=True)

            try:
                df: pd.DataFrame = _with_retry(
                    fbref.read_player_season_stats, stat_type=category
                )
            except RuntimeError as exc:
                print(f"FEHLER ({exc})")
                log.error("Skipping %s / %s: %s", category, league, exc)
                time.sleep(config.REQUEST_DELAY_SECONDS)
                continue

            if df is None or df.empty:
                print("keine Daten")
                log.warning("No data returned for %s / %s / %s", category, league, season)
                time.sleep(config.REQUEST_DELAY_SECONDS)
                continue

            # Flatten MultiIndex columns (soccerdata uses them)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [
                    "_".join(filter(None, map(str, col))).strip("_").lower()
                    for col in df.columns
                ]
            else:
                df.columns = [str(c).strip().lower() for c in df.columns]

            # Reset index to expose player/squad columns
            df = df.reset_index()

            saved = 0
            for _, row in df.iterrows():
                player_key = f"{row.get('player', '')}_{row.get('squad', '')}".strip("_")
                if not player_key:
                    continue

                meta = _parse_player_meta(row, league, season)
                player = get_or_create_player(session, fbref_id=player_key, meta=meta)

                # Minutes — check several possible column names
                minutes_col = next(
                    (c for c in ["minutes", "min", "mins", "playing_time_min"] if c in row.index),
                    None,
                )
                minutes = float(row[minutes_col]) if minutes_col and pd.notna(row[minutes_col]) else 0.0

                # Collect numeric stat columns (exclude metadata)
                stat_cols = [
                    c for c in row.index
                    if c not in config.NON_STAT_COLUMNS
                    and pd.api.types.is_numeric_dtype(type(row[c]))
                ]
                stats = {
                    col: (None if pd.isna(row[col]) else float(row[col]))
                    for col in stat_cols
                }

                upsert_raw_stats(
                    session,
                    player_id=player.id,
                    season=season,
                    category=category,
                    minutes=minutes,
                    stats=stats,
                )
                saved += 1

            session.commit()
            print(f"{saved} Spieler gespeichert")
            time.sleep(config.REQUEST_DELAY_SECONDS)

    print(f"  -> {league} {season} abgeschlossen")


def scrape_all(
    leagues: Optional[list[str]] = None,
    season: Optional[str] = None,
    categories: Optional[list[str]] = None,
) -> None:
    """Entry point: scrape every configured league."""
    if leagues is None:
        leagues = config.LEAGUES
    if season is None:
        season = config.SEASON

    engine = get_engine()
    init_db(engine)

    total = len(leagues)
    for i, league in enumerate(leagues, 1):
        print(f"\n  Liga {i}/{total}: {league}")
        try:
            scrape_league(league, season, categories=categories, engine=engine)
        except Exception as exc:  # noqa: BLE001
            print(f"  !! Liga {league} komplett fehlgeschlagen: {exc}")
            log.error("League %s failed entirely: %s", league, exc)
