"""Central configuration for the scouting tool."""

from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "scouting.db"
CACHE_DIR = DATA_DIR / "cache"

DATA_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)

# ── API ────────────────────────────────────────────────────────────────────
# Kostenlosen Key holen: https://www.football-data.org/client/register
FOOTBALL_DATA_API_KEY = "DEIN_API_KEY_HIER"

# ── Ligen (football-data.org Codes) ───────────────────────────────────────
# Freie Tier verfügbare Ligen:
# PL=Premier League, BL1=Bundesliga, PD=La Liga, SA=Serie A, FL1=Ligue 1
# CL=Champions League, EL=Europa League, WC=World Cup, EC=Euros
LEAGUES = {
    "PL":  "ENG-Premier League",
    "BL1": "GER-Bundesliga",
    "PD":  "ESP-La Liga",
    "SA":  "ITA-Serie A",
    "FL1": "FRA-Ligue 1",
}

# Saison als Jahreszahl (football-data.org nutzt Startjahr)
SEASON = 2024

# Wie viele Top-Scorer pro Liga laden (max. 100 im Free-Tier)
SCORERS_LIMIT = 100

# ── Rate-limiting & retry ──────────────────────────────────────────────────
REQUEST_DELAY_SECONDS = 6      # free tier: 10 requests/minute → 6s Abstand
RETRY_MAX_ATTEMPTS = 3
RETRY_BACKOFF_BASE = 2

# ── Normalisation ──────────────────────────────────────────────────────────
MIN_MINUTES = 450              # Spieler unter dieser Schwelle werden ignoriert

# Spalten die bereits Raten/Prozente sind — nicht per-90 dividieren
PERCENT_PATTERNS = [
    r"pct",
    r"%",
    r"_pct",
    r"per_90",
    r"ratio",
    r"rate",
    r"avg",
    r"average",
]

# Metadaten-Spalten, keine Statistiken
NON_STAT_COLUMNS = [
    "player_id", "player", "season", "minutes", "nationality",
    "position", "squad", "comp", "age", "born", "league",
]

# ── Talent scoring weights (per position group) ────────────────────────────
SCORING_WEIGHTS = {
    "FW": {
        "goals_per90":   0.45,
        "assists_per90": 0.30,
        "penalties_per90": 0.25,
    },
    "MF": {
        "goals_per90":   0.30,
        "assists_per90": 0.50,
        "penalties_per90": 0.20,
    },
    "DF": {
        "goals_per90":   0.20,
        "assists_per90": 0.80,
    },
    "GK": {
        "goals_per90":   0.50,   # eigene Tore (Raritäten)
        "assists_per90": 0.50,
    },
}

INVERT_COLUMNS: list = []
