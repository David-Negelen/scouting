"""Talent scoring: weighted, percentile-rank-based composite score."""

import json
import logging
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session
from tqdm import tqdm

import config
from database import Player, PlayerStatsPer90, get_engine, load_per90_dataframe, upsert_score

log = logging.getLogger(__name__)


# ── Position grouping ──────────────────────────────────────────────────────

_POS_MAP = {
    "GK": "GK",
    "CB": "DF", "LB": "DF", "RB": "DF", "WB": "DF",
    "LWB": "DF", "RWB": "DF",
    "DM": "MF", "CM": "MF", "AM": "MF", "LM": "MF", "RM": "MF",
    "LW": "FW", "RW": "FW", "CF": "FW", "ST": "FW", "SS": "FW",
}

_FALLBACK_PREFIX = {
    "G": "GK",
    "D": "DF",
    "M": "MF",
    "F": "FW",
    "A": "FW",
}


def _position_group(position: Optional[str]) -> Optional[str]:
    if not position:
        return None
    pos = str(position).upper().strip().split(",")[0].strip()
    if pos in _POS_MAP:
        return _POS_MAP[pos]
    for prefix, group in _FALLBACK_PREFIX.items():
        if pos.startswith(prefix):
            return group
    return None


# ── Percentile scoring ─────────────────────────────────────────────────────

def _percentile_rank(series: pd.Series) -> pd.Series:
    """Convert raw values to percentile ranks (0–100) within the group."""
    return series.rank(pct=True, na_option="keep") * 100


def _score_group(
    df: pd.DataFrame,
    position_group: str,
    weights: dict[str, float],
) -> pd.DataFrame:
    """Return a DataFrame with talent_score and sub_scores for one group."""
    available = [col for col in weights if col in df.columns]
    if not available:
        log.warning("No scoring columns available for %s", position_group)
        return pd.DataFrame()

    # Normalise weights to sum to 1 over available columns
    total_w = sum(weights[c] for c in available)
    norm_weights = {c: weights[c] / total_w for c in available}

    sub: pd.DataFrame = pd.DataFrame(index=df.index)
    for col in available:
        pct = _percentile_rank(df[col])
        # Invert for "lower is better" metrics
        if col in config.INVERT_COLUMNS:
            pct = 100 - pct
        sub[col] = pct

    talent_score = sum(sub[col] * norm_weights[col] for col in available)

    result = df[["player_id", "season"]].copy()
    result["position_group"] = position_group
    result["talent_score"] = talent_score.round(2)
    result["sub_scores"] = sub[available].round(2).to_dict(orient="records")
    return result


# ── Main scoring pipeline ──────────────────────────────────────────────────

def score_all(season: Optional[str] = None, engine=None) -> None:
    if season is None:
        season = config.SEASON
    if engine is None:
        engine = get_engine()

    df = load_per90_dataframe(engine)
    if df.empty:
        log.warning("No per-90 data found — run normalisation first.")
        return

    df = df[df["season"] == season].copy()
    if df.empty:
        log.warning("No per-90 data for season %s", season)
        return

    # Need player_id in df — join from DB
    with Session(engine) as session:
        id_map = {p.name: p.id for p in session.query(Player).all()}
    df["player_id"] = df["name"].map(id_map)

    df["position_group"] = df["position"].apply(_position_group)

    scored_rows: list[dict] = []
    for pos_group, weights in config.SCORING_WEIGHTS.items():
        subset = df[df["position_group"] == pos_group].copy()
        if subset.empty:
            print(f"  {pos_group}: keine Spieler")
            continue
        result = _score_group(subset, pos_group, weights)
        if not result.empty:
            scored_rows.append(result)
            print(f"  {pos_group}: {len(subset)} Spieler bewertet")
        else:
            print(f"  {pos_group}: keine Scoring-Spalten verfügbar")

    if not scored_rows:
        print("  !! Keine Scores berechnet.")
        return

    all_scores = pd.concat(scored_rows, ignore_index=True)

    with Session(engine) as session:
        for _, row in tqdm(all_scores.iterrows(), total=len(all_scores), desc="  Scores speichern"):
            if pd.isna(row["player_id"]):
                continue
            upsert_score(
                session,
                player_id=int(row["player_id"]),
                season=row["season"],
                position_group=row["position_group"],
                talent_score=float(row["talent_score"]),
                sub_scores=row["sub_scores"],
            )
        session.commit()

    print(f"  -> {len(all_scores)} Spieler mit Talent-Score gespeichert")
