"""Central configuration for the scouting tool."""

from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "scouting.db"
CACHE_DIR = DATA_DIR / "fbref_cache"

DATA_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)

# ── Seasons & Leagues ──────────────────────────────────────────────────────
SEASON = "2024-2025"

LEAGUES = [
    "ENG-Premier League",
    "GER-Bundesliga",
    "ESP-La Liga",
    "ITA-Serie A",
    "FRA-Ligue 1",
]

# ── FBref stat categories to scrape ───────────────────────────────────────
FBREF_CATEGORIES = [
    "standard",
    "shooting",
    "passing",
    "pass_types",
    "goal_shot_creation",
    "defense",
    "possession",
    "misc",
]

# ── Rate-limiting & retry ──────────────────────────────────────────────────
REQUEST_DELAY_SECONDS = 2          # pause between category requests
RETRY_MAX_ATTEMPTS = 3
RETRY_BACKOFF_BASE = 2             # exponential backoff: base^attempt seconds

# ── Normalisation ──────────────────────────────────────────────────────────
MIN_MINUTES = 450                  # players below this threshold are excluded

# Columns that are already rates/percentages — skip per-90 division.
# Patterns are matched with str.contains (case-insensitive).
PERCENT_PATTERNS = [
    r"pct",
    r"%",
    r"_pct",
    r"per_90",
    r"xg_per",
    r"ratio",
    r"rate",
    r"avg",
    r"average",
]

# Columns that are never counting stats (identifiers, metadata).
NON_STAT_COLUMNS = [
    "player_id",
    "player",
    "season",
    "minutes",
    "nationality",
    "position",
    "squad",
    "comp",
    "age",
    "born",
    "league",
    "90s",          # FBref already provides 90s-played column
]

# ── Talent scoring weights (per position group) ────────────────────────────
# Each position group maps stat column → weight.
# Weights are normalised inside scorer.py — they don't need to sum to 1.
SCORING_WEIGHTS = {
    "FW": {
        "goals_per90":            0.30,
        "xg_per90":               0.20,
        "shots_on_target_per90":  0.10,
        "npxg_per90":             0.15,
        "progressive_carries_per90": 0.10,
        "progressive_passes_per90":  0.05,
        "sca_per90":              0.10,
    },
    "MF": {
        "progressive_passes_per90":   0.20,
        "key_passes_per90":           0.15,
        "sca_per90":                  0.10,
        "gca_per90":                  0.10,
        "progressive_carries_per90":  0.10,
        "tackles_per90":              0.10,
        "interceptions_per90":        0.10,
        "passes_completed_per90":     0.05,
        "assists_per90":              0.10,
    },
    "DF": {
        "tackles_per90":              0.25,
        "interceptions_per90":        0.20,
        "blocks_per90":               0.15,
        "clearances_per90":           0.15,
        "progressive_passes_per90":   0.10,
        "aerials_won_per90":          0.15,
    },
    "GK": {
        "save_pct":                   0.30,
        "goals_against_per90":        0.25,  # lower is better — inverted in scorer
        "clean_sheets_per90":         0.20,
        "psxg_minus_ga_per90":        0.25,
    },
}

# Columns where a lower value is better (will be inverted during scoring).
INVERT_COLUMNS = [
    "goals_against_per90",
    "cards_yellow_per90",
    "cards_red_per90",
    "fouls_per90",
    "offsides_per90",
]
