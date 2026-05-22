"""Per-90 normalisation of raw player stats."""

import json
import logging
import re
from typing import Optional
from typing import Any

from sqlalchemy.orm import Session
from tqdm import tqdm

import config
from database import Player, PlayerStatsRaw, get_engine, upsert_per90_stats

log = logging.getLogger(__name__)


# ── Column classification ──────────────────────────────────────────────────

def _is_percent_col(col: str) -> bool:
    """Return True if the column represents a rate/percentage (don't divide)."""
    col_lower = col.lower()
    return any(re.search(p, col_lower) for p in config.PERCENT_PATTERNS)


def _is_non_stat_col(col: str) -> bool:
    return col.lower() in {c.lower() for c in config.NON_STAT_COLUMNS}


def _should_normalise(col: str) -> bool:
    return not _is_non_stat_col(col) and not _is_percent_col(col)


# ── Merge raw categories into one flat dict ────────────────────────────────

def _merge_categories(raw_rows: list[PlayerStatsRaw]) -> tuple[float, dict[str, Any]]:
    """Merge multiple category dicts into one, preferring non-None values."""
    merged: dict[str, Any] = {}
    minutes = 0.0

    for row in raw_rows:
        if row.minutes and row.minutes > minutes:
            minutes = row.minutes
        for col, val in row.stats.items():
            if val is not None and col not in merged:
                merged[col] = val
            elif val is not None and merged.get(col) is None:
                merged[col] = val

    return minutes, merged


# ── Per-90 computation ─────────────────────────────────────────────────────

def _normalise(stats: dict[str, Any], minutes: float) -> dict[str, Any]:
    """Return a new dict with counting stats divided by (minutes / 90)."""
    denominator = minutes / 90.0  # will always be ≥ 5 after filter

    result: dict[str, Any] = {}
    for col, val in stats.items():
        if val is None:
            result[col] = None
            continue

        if _should_normalise(col):
            try:
                result[f"{col}_per90"] = round(float(val) / denominator, 4)
            except (TypeError, ZeroDivisionError):
                result[f"{col}_per90"] = None
        else:
            # Percentages and rates kept as-is
            result[col] = val

    return result


# ── Main normalisation pipeline ────────────────────────────────────────────

def normalise_all(season: Optional[str] = None, engine=None) -> None:
    """Read raw stats from DB, normalise, write per-90 rows."""
    if season is None:
        season = config.SEASON
    if engine is None:
        engine = get_engine()

    with Session(engine) as session:
        players = (
            session.query(Player)
            .join(Player.raw_stats)
            .filter(PlayerStatsRaw.season == season)
            .distinct()
            .all()
        )

        skipped = 0
        processed = 0

        for player in tqdm(players, desc="Normalising", unit="player"):
            raw_rows = [r for r in player.raw_stats if r.season == season]
            if not raw_rows:
                continue

            minutes, merged_stats = _merge_categories(raw_rows)

            if minutes < config.MIN_MINUTES:
                log.debug(
                    "Skipping %s — only %.0f min (threshold: %d)",
                    player.name,
                    minutes,
                    config.MIN_MINUTES,
                )
                skipped += 1
                continue

            per90 = _normalise(merged_stats, minutes)

            upsert_per90_stats(
                session,
                player_id=player.id,
                season=season,
                minutes=minutes,
                stats=per90,
            )
            processed += 1

        session.commit()

    log.info(
        "Normalisation complete — %d players processed, %d skipped (< %d min)",
        processed,
        skipped,
        config.MIN_MINUTES,
    )
